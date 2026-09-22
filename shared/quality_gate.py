"""Shared stage quality-gate contract.

Every stage report carries the upstream ``script_revision`` it validated.  A
downstream stage may only consume a report when both conditions hold:

* report revision == current script revision (stale reports are rejected);
* report is not blocking.

The revision is intentionally stored on the script and copied downstream rather
than recomputed after every local metadata write.  That makes the report an
identity token for the story/visual plan, not a hash of transient file paths.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


QUALITY_RULE_VERSION = "2026-09-22.10"


class QualityGateError(RuntimeError):
    pass


_SEMANTIC_SHOT_FIELDS = (
    "index", "main_subject", "prompt", "ff_desc", "lf_desc", "motion_desc",
    "narration_segment", "subtitle", "frame_layouts", "char_layout",
    "keyframes", "camera_movement", "clip_transition", "context_bridge",
    "core_keywords", "duration_sec", "scene", "location", "time_of_day",
    "color_tone", "shot_type", "variation_type",
    # v6.79（2026-09-20）：`temporal_plan` 是**结构行为的消费对象**
    # （拆镜边界 `timecode._event_boundary_cut` 直接读它），却曾不在这份白名单里
    # ——于是事件表单独变化既不改 script revision 也不改 per-shot revision，
    # 已有的 semantic audit 会继续显示「revision 匹配」，而实际用于拆镜的时间
    # 语义早已变化；修复轮的 non-target gate 也看不见对未点名镜头事件表的改写。
    # 详见 docs/step01-temporal-writers-implementation-review-2026-09-19.md 发现 11。
    "temporal_plan",
)

# `temporal_plan` 里**会影响结构行为**的字段。其余（派生诊断、模型自报元数据）
# 不入哈希——它们不改变任何消费方的决策，进了哈希只会让 revision 抖动。
_TEMPORAL_PLAN_EVENT_FIELDS = ("event_id", "start_sec", "end_sec", "description", "source_quote")


def _canonical_temporal_plan(plan):
    """规范化 `temporal_plan` → 可哈希的稳定结构；无有效事件 → None.

    数值统一 round 到 3 位小数：事件边界是浮点，2.0 与 2.0000000001 是同一个
    边界，不该产生两个 revision。
    """
    if not isinstance(plan, dict):
        return None
    events = plan.get("events")
    if not isinstance(events, list) or not events:
        return None
    items: list[dict] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        item: dict = {}
        for k in _TEMPORAL_PLAN_EVENT_FIELDS:
            if k not in ev:
                continue
            v = ev.get(k)
            if k in ("start_sec", "end_sec"):
                try:
                    v = round(float(v), 3)
                except (TypeError, ValueError):
                    v = None
            elif v is not None:
                v = str(v)
            item[k] = v
        items.append(item)
    if not items:
        return None
    out: dict = {"events": items}
    src = plan.get("source")
    if src:
        out["source"] = str(src)
    return out


def _semantic_shot(shot) -> dict:
    if not isinstance(shot, dict):
        return {}
    out = {k: shot.get(k) for k in _SEMANTIC_SHOT_FIELDS if k in shot}
    kfs = out.get("keyframes")
    if isinstance(kfs, list):
        out["keyframes"] = [
            {k: v for k, v in kf.items() if k in ("desc", "stage", "t_sec", "time_ratio")}
            for kf in kfs if isinstance(kf, dict)
        ]
    if "temporal_plan" in out:
        plan = _canonical_temporal_plan(out["temporal_plan"])
        if plan is None:
            out.pop("temporal_plan", None)
        else:
            out["temporal_plan"] = plan
    return out


def _semantic_character(char) -> dict:
    if not isinstance(char, dict):
        return {}
    keep = ("identifier", "gender", "age_range", "static_features",
            "dynamic_features", "is_visible", "is_new")
    return {k: char.get(k) for k in keep if k in char}


def _stable_payload(script: dict) -> dict:
    keys = (
        "title", "chapter_index", "chapter_title", "book_name",
        "outline", "characters", "shots", "hook", "narration",
        "negative_prompt", "scene_objects", "resolution", "ratio", "fps",
        "render_style",
    )
    payload = {k: script.get(k) for k in keys if k in script}
    if "shots" in payload:
        payload["shots"] = [_semantic_shot(s) for s in (payload.get("shots") or [])]
    if "hook" in payload:
        payload["hook"] = _semantic_shot(payload.get("hook"))
    if "characters" in payload:
        payload["characters"] = [_semantic_character(c) for c in (payload.get("characters") or [])]
    return payload


def semantic_payload(script: dict) -> dict:
    """语义 payload（revision 哈希的那个对象），以可读结构返回.

    v6.77 新增：此前只有 `compute_script_revision` 一个不透明哈希，测试只能
    断言"哈希相等/不等"，**无法定位差异**。要写「连续跑两次 hard-fix 结果
    字节级一致」这类回归，必须先拿到可逐字段比较的对象。
    """
    return _stable_payload(script)


def semantic_payload_json(script: dict) -> str:
    """语义 payload 的规范 JSON 文本——**逐字节可比**（排序键、无多余空白）."""
    return json.dumps(_stable_payload(script), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"), default=str)


def payload_diff(before: dict, after: dict, *, max_items: int = 12) -> list[str]:
    """逐镜头比较两个 script 的语义 payload，返回人类可读差异行.

    供回归测试在断言失败时打印"到底哪一镜的哪个字段变了"。
    仅覆盖 shots / hook 的逐字段差异（不含 characters 等 script 级字段）。
    """
    out: list[str] = []
    for key in ("shots", "hook"):
        a, b = before.get(key), after.get(key)
        if key == "hook":
            a, b = [a] if isinstance(a, dict) else [], [b] if isinstance(b, dict) else []
        a = [_semantic_shot(x) for x in (a or [])]
        b = [_semantic_shot(x) for x in (b or [])]
        if len(a) != len(b):
            out.append("%s: 镜头数 %d -> %d" % (key, len(a), len(b)))
        for i in range(min(len(a), len(b))):
            sa, sb = a[i], b[i]
            for f in sorted(set(sa) | set(sb)):
                if sa.get(f) != sb.get(f):
                    out.append("%s[%d].%s: %r -> %r"
                               % (key, i, f, sa.get(f), sb.get(f)))
                    if len(out) >= max_items:
                        return out
    return out


def compute_script_revision(script: dict) -> str:
    return hashlib.sha256(semantic_payload_json(script).encode("utf-8")).hexdigest()


def ensure_script_revision(script: dict, *, force: bool = False) -> str:
    """Recompute the content revision; stored values are never trusted.

    A stored ``script_revision`` is only a cache hint.  Recomputing on every
    gate check catches manual edits to story/visual fields that previously
    slipped through with an old revision.
    """
    revision = compute_script_revision(script)
    if force or str(script.get("script_revision") or "").strip() != revision:
        script["script_revision"] = revision
    return revision



def shot_semantic_digest(shot) -> str:
    """单镜语义摘要哈希（`_SEMANTIC_SHOT_FIELDS` 白名单内的字段）."""
    return _hash_json(_semantic_shot(shot))


def semantic_payload_digest(script: dict) -> dict:
    """**结构 + 逐镜语义**的可比较摘要（v6.98，2026-09-22 审核 P0-3）.

    与 `per_shot_revisions` 的区别：那个按 `index` 为键（修复轮点名要用它），
    这个按**列表位置**为键——阶段屏障关心的是「这一镜的语义有没有被改」，
    而 `shot_index_renumber` 会让 index 平移，用 index 当键会把整次重编号
    报成「每一镜都变了」（正是屏障在 v6.84 踩过的坑）。

    屏障此前**只**快照时间字段（`duration` / `duration_sec` / `motion_desc` /
    `keyframes[*].t_sec`），于是「删掉一整个镜头对象」「重排镜头顺序」「改写
    prompt/ff/lf/布局/角色」这些**语义级**写入在审计段里完全不可见——审核 P0-3
    实测：`opening_empty_shot_remove` 在审计边界之后 `shots.pop(0)`，屏障毫无反应。
    """
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    hook = script.get("hook")
    return {
        "count": len(shots),
        "shots": [shot_semantic_digest(s) for s in shots],
        "hook": shot_semantic_digest(hook) if isinstance(hook, dict) else "",
        "characters": _hash_json([_semantic_character(c)
                                  for c in (script.get("characters") or [])]),
    }


def diff_semantic_payload(before: dict, after: dict, *, max_items: int = 20) -> list[str]:
    """比较两个 `semantic_payload_digest`，返回人可读差异（结构变更 + 逐镜语义变更）."""
    out: list[str] = []
    _bc, _ac = before.get("count"), after.get("count")
    if _bc != _ac:
        out.append("镜头数 %s -> %s" % (_bc, _ac))
    _bs, _as = before.get("shots") or [], after.get("shots") or []
    for i in range(min(len(_bs), len(_as))):
        if _bs[i] != _as[i]:
            out.append("镜头#%d 语义变化" % (i + 1))
            if len(out) >= max_items:
                return out
    for k in ("hook", "characters"):
        if before.get(k) != after.get(k):
            out.append("%s 语义变化" % k)
            if len(out) >= max_items:
                return out
    return out


def per_shot_revisions(script: dict) -> dict:
    """逐镜语义 revision：`{index: sha256}`（hook 用 `"hook"` 为键）.

    v6.77 新增：验收条件「修复单个镜头不能改变其他镜头的语义 revision」
    需要**按镜**比较，而 `compute_script_revision` 只有整脚本一个哈希——
    修了 A 镜，整脚本哈希必然变，无法判定 B 镜有没有被动过。
    """
    out: dict = {}
    for i, s in enumerate(script.get("shots") or []):
        key = s.get("index") if isinstance(s, dict) and "index" in s else i
        out[key] = _hash_json(_semantic_shot(s))
    hook = script.get("hook")
    if isinstance(hook, dict):
        out["hook"] = _hash_json(_semantic_shot(hook))
    return out


def changed_shot_keys(before: dict, after: dict) -> list:
    """两个 `per_shot_revisions` 之间发生变化的镜 key（含新增/删除）."""
    keys = {k for k in list(before) + list(after) if before.get(k) != after.get(k)}
    return sorted(keys, key=lambda k: (isinstance(k, str), k))


def _hash_json(obj) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def attach_gate_metadata(payload: dict, script: dict, *, stage: str) -> dict:
    revision = ensure_script_revision(script)
    payload.setdefault("source", stage)
    payload.setdefault("report_type", "quality_gate")
    payload.setdefault("rule_version", QUALITY_RULE_VERSION)
    if "blocking" in payload and "status" not in payload:
        payload["status"] = "BLOCK" if payload.get("blocking") else "PASS"
    payload["script_revision"] = revision
    return payload


def load_gate(report_path: Path) -> dict:
    if not report_path.exists():
        raise QualityGateError(f"quality gate report missing: {report_path}")
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise QualityGateError(f"quality gate report unreadable: {report_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise QualityGateError(f"quality gate report is not an object: {report_path}")
    return data



def _sha1_file(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_output_files(obj):
    if isinstance(obj, dict):
        if "path" in obj and "sha1" in obj:
            yield obj
            return
        for value in obj.values():
            yield from _iter_output_files(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from _iter_output_files(value)


def verify_report_outputs(report: dict) -> list[str]:
    errors = []
    items = list(_iter_output_files(report.get("outputs") or {}))
    if not items:
        return ["report contains no verifiable outputs"]
    for item in items:
        p = Path(str(item.get("path") or ""))
        if not p.exists():
            errors.append(f"missing output: {p}")
            continue
        actual = _sha1_file(p)
        if actual != str(item.get("sha1") or ""):
            errors.append(f"output changed: {p}")
    return errors



def require_semantic_gate(quality_path: Path, audit_path: Path, script: dict,
                          *, stage: str = "step01") -> dict:
    """Require both structural PASS and a non-blocking final audit."""
    quality = require_gate(
        quality_path, script, stage=stage, required_report_type="quality_gate"
    )
    audit = load_gate(audit_path)
    revision = ensure_script_revision(script)
    if str(audit.get("status") or "").strip().lower() != "ok":
        raise QualityGateError(
            f"{stage} semantic audit is not ok: status={audit.get('status') or 'missing'}"
        )
    if str(audit.get("input_revision") or "").strip() != revision:
        raise QualityGateError(
            f"{stage} semantic audit is stale: audit={audit.get('input_revision') or 'missing'} current={revision}"
        )
    if str(audit.get("rule_source_fingerprint") or "").strip() != str(
        quality.get("rule_source_fingerprint") or ""
    ).strip():
        raise QualityGateError(
            f"{stage} semantic audit fingerprint mismatch: "
            f"audit={audit.get('rule_source_fingerprint') or 'missing'} "
            f"quality={quality.get('rule_source_fingerprint') or 'missing'}"
        )
    if stage == "step01":
        try:
            from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT
        except Exception:
            RULE_SOURCE_FINGERPRINT = ""
        report_fp = str(quality.get("rule_source_fingerprint") or "").strip()
        if RULE_SOURCE_FINGERPRINT and report_fp != RULE_SOURCE_FINGERPRINT:
            raise QualityGateError(
                f"{stage} quality gate was produced by different rule sources: "
                f"report={report_fp or 'missing'} current={RULE_SOURCE_FINGERPRINT}"
            )
    if not str(audit.get("audit_scope") or "").strip():
        raise QualityGateError(f"{stage} semantic audit scope is missing")
    blocking = [
        issue for issue in (audit.get("issues") or [])
        if str(issue.get("severity") or "").strip() in ("hard", "visual_risk", "blocking")
    ]
    if blocking:
        first = blocking[0]
        raise QualityGateError(
            f"{stage} semantic audit contains {len(blocking)} blocking issue(s): "
            f"shot={first.get('shot_id')} frame={first.get('frame_id')} "
            f"code={first.get('code')} message={first.get('message')}"
        )
    return {"quality": quality, "audit": audit}


def require_gate(report_path: Path, script: dict, *, stage: str,
                 allow_blocking: bool = False,
                 required_report_type: str | None = None,
                 verify_outputs: bool = False) -> dict:
    report = load_gate(report_path)
    revision = ensure_script_revision(script)
    report_revision = str(report.get("script_revision") or "").strip()
    if not report_revision or report_revision != revision:
        raise QualityGateError(
            f"{stage} quality gate is stale: report={report_revision or 'missing'} "
            f"current={revision}. Re-run the upstream stage."
        )
    if report.get("blocking") and not allow_blocking:
        raise QualityGateError(
            f"{stage} quality gate is blocking: "
            f"hard={report.get('hard_count')} visual={report.get('visual_risk_count')} "
            f"categories={report.get('blocking_categories')}"
        )
    report_status = str(report.get("status") or "").strip().upper()
    if report_status != "PASS":
        raise QualityGateError(
            f"{stage} quality gate has no explicit PASS status "
            f"(status={report_status or 'missing'}). Re-run the upstream stage."
        )
    report_rule = str(report.get("rule_version") or "").strip()
    if report_rule != QUALITY_RULE_VERSION:
        raise QualityGateError(
            f"{stage} quality gate rule_version mismatch: "
            f"report={report_rule or 'missing'} expected={QUALITY_RULE_VERSION}; re-run the upstream stage"
        )
    if required_report_type and report.get("report_type") != required_report_type:
        raise QualityGateError(
            f"{stage} quality gate report_type mismatch: "
            f"report={report.get('report_type') or 'missing'} expected={required_report_type}"
        )
    if verify_outputs:
        _out_errors = verify_report_outputs(report)
        if _out_errors:
            raise QualityGateError(
                f"{stage} product outputs failed verification: " + "; ".join(_out_errors[:5])
            )
    return report


__all__ = [
    "semantic_payload",
    "semantic_payload_digest",
    "shot_semantic_digest",
    "diff_semantic_payload",
    "semantic_payload_json",
    "payload_diff",
    "per_shot_revisions",
    "changed_shot_keys",
    "QualityGateError",
    "compute_script_revision",
    "ensure_script_revision",
    "attach_gate_metadata",
    "load_gate",
    "require_gate",
    "require_semantic_gate",
    "verify_report_outputs",
]
