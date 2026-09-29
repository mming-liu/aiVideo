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


QUALITY_RULE_VERSION = "2026-09-23.4"


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
        "outline", "chapter_outline", "characters", "shots", "hook", "narration",
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
    digests = [shot_semantic_digest(s) for s in shots]
    return {
        "count": len(shots),
        "shots": digests,
        # `order` / `set` 只用于**区分「重排」与「逐镜改写」**：两者在逐位置比较
        # 下长得一样（都是「镜头#i 语义变化」），而语义完全不同——
        # 重排会改变分镜叙事顺序（必须拦），逐镜改写是本层约束的**设计行为**（不能拦）。
        "order": _hash_json(digests),
        "set": _hash_json(sorted(digests)),
        "hook": shot_semantic_digest(hook) if isinstance(hook, dict) else "",
        "characters": _hash_json([_semantic_character(c)
                                  for c in (script.get("characters") or [])]),
        # 结构红线的**粗粒度身份**：hook 只有「在不在」（整对象替换与整对象改写
        # 难以区分，而审计段对 hook 的合法写入就是追加文本）；characters 看
        # **identifier 集合**（新增/删除角色是结构变更，`dynamic_features` 随章节
        # 与约束改写是正常行为）。
        "hook_present": isinstance(hook, dict),
        "character_ids": sorted(str(c.get("identifier") or "")
                                for c in (script.get("characters") or [])
                                if isinstance(c, dict) and c.get("identifier")),
    }


def diff_semantic_structure(before: dict, after: dict, *, max_items: int = 20) -> list[str]:
    """只比**结构**：镜头数 / 顺序 / hook / characters——审计段真正的红线.

    v6.98（2026-09-22 审核 P0-3）：审核发现「审计段能静默删掉一整个镜头对象」。
    但**逐镜文本改写不能算红线**——`kf_time_window_alignment` 之后的硬修复层
    （补景别前缀 / 锚点重建 / 合规替换 / 冗余清洗…共 71 个约束）**本来就是靠写
    `prompt`/`ff_desc`/`lf_desc` 工作的**；把逐镜语义也判越权，等于让每一次运行
    都必然 BLOCK（实测该层每天在跑，这正是「自己造 blocking」）。
    所以红线只取结构与顺序：

      - 镜头数变化（增/删）→ 叙事被删或凭空多出；
      - **纯重排**（`set` 相同、`order` 不同）→ 叙事顺序被改；
      - hook **消失/出现**；characters 的 **identifier 集合**增删。

    **刻意不判**的：对 hook / 角色的**文本改写**——`transition_keywords` 之类的
    追加类约束本来就会写 hook 的 prompt，角色的 `dynamic_features` 也随章节更新
    （真实链条实测：把 hook 的「任何变化」当红线 → `transition_keywords` 立刻
    误报）。整对象替换难以与整对象改写区分，所以 hook 的红线只取「在不在」。
    """
    out: list[str] = []
    _bc, _ac = before.get("count"), after.get("count")
    if _bc != _ac:
        out.append("镜头数 %s -> %s" % (_bc, _ac))
    elif (before.get("set") == after.get("set")
          and before.get("order") != after.get("order")):
        out.append("镜头顺序变化（内容相同、顺序不同）")
    if bool(before.get("hook_present")) != bool(after.get("hook_present")):
        out.append("hook %s -> %s" % ("有" if before.get("hook_present") else "无",
                                      "有" if after.get("hook_present") else "无"))
    _bid, _aid = set(before.get("character_ids") or []), set(after.get("character_ids") or [])
    if _bid != _aid:
        out.append("角色集合变化 +%s -%s" % (sorted(_aid - _bid), sorted(_bid - _aid)))
    return out[:max_items]


def diff_semantic_payload(before: dict, after: dict, *, max_items: int = 20) -> list[str]:
    """比较两个 `semantic_payload_digest`，返回人可读差异（结构变更 + 逐镜语义变更）.

    诊断用（谁改了什么）；**判越权请用 `diff_semantic_structure`**——逐镜文本改写
    是审计段硬修复层的设计行为，不是违规。
    """
    out: list[str] = list(diff_semantic_structure(before, after, max_items=max_items))
    _bs, _as = before.get("shots") or [], after.get("shots") or []
    for i in range(min(len(_bs), len(_as))):
        if _bs[i] != _as[i]:
            out.append("镜头#%d 语义变化" % (i + 1))
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


# ===========================================================================
# step01 唯一提交点的 manifest（v6.103，总计划阶段 4）
# ===========================================================================
# 三份正式产物必须**同属一个提交批次**。文件系统不支持「三个文件同时原子替换」，
# 所以提交完成的标志是一个独立的 manifest：它记录 revision / 规则指纹 / audit
# scope 与三份文件的 SHA1。下游除了原有的三份检查，还要核对 manifest——
# 缺失、状态非 COMMITTED、revision 不符或任一摘要不符，一律 fail-closed。
#
# 这样即使进程在替换中途崩溃（正式路径出现新旧混合），manifest 也对不上，
# 混合版本不会被误用。
STEP01_COMMIT_FILENAME = "step01_commit.json"
STEP01_ARTIFACT_FILES = ("script_result.json", "step01_quality.json", "step01_audit.json")
STEP01_MANIFEST_VERSION = 1


def sha1_file(path: Path) -> str:
    return _sha1_file(Path(path))


def _cap_blocking_for_manifest(blocking: list, *, cap: int = 20) -> list:
    """BLOCK manifest 的 blocking 清单：截断但**每个 code 至少留一条**（v6.143 P6）.

    此前是裸 `[:20]`：`blocking_count` 是真值，而清单只留**顺序前 20 条**，于是
    「这一轮到底有哪些类缺陷」从清单读不出来——2026-09-27 十次连跑里 36 条的 run
    只留 20 条，问题报告只能按清单统计出**下限**（kf 族被漏掉一部分）。

    现在：先按出现顺序保证每个 `code` 至少一条，再用原顺序补齐到 `cap`。
    形状不变（仍是原来的 record 列表，只是选法变了），计数仍是 `blocking_count`。
    是否发生截断由 `blocking_truncated` 显式声明——读者不必数。
    """
    items = list(blocking or [])
    if len(items) <= cap:
        return items
    picked: list = []
    seen_codes: set = set()
    for rec in items:
        code = str((rec or {}).get("code") or "")
        if code in seen_codes:
            continue
        seen_codes.add(code)
        picked.append(rec)
        if len(picked) >= cap:
            return picked
    for rec in items:
        if rec in picked:
            continue
        picked.append(rec)
        if len(picked) >= cap:
            break
    return picked


def build_step01_commit_manifest(*, run_id: str, created_at: str, revision: str,
                                 rule_source_fingerprint: str, rule_version: str,
                                 audit_scope: str, chapter_dir: Path,
                                 status: str = "COMMITTED",
                                 blocking: list | None = None,
                                 detail: str = "",
                                 prompt_source_fingerprint: str = "") -> dict:
    """构造 commit manifest；`status=COMMITTED` 时才带三份产物的摘要.

    `prompt_source_fingerprint`（v6.112）是**诊断字段**：记录本批产物出自哪一版
    生成/修复提示词与大纲预算。它**不参与**任何准入或复用判定——改提示词改变的
    是稿件内容，内容变化已由 `script_revision` 覆盖；只有
    `rule_source_fingerprint` 才决定旧报告是否仍然有效。
    """
    payload = {
        "manifest_version": STEP01_MANIFEST_VERSION,
        "status": status,
        "run_id": run_id,
        "created_at": created_at,
        "script_revision": revision,
        "rule_source_fingerprint": rule_source_fingerprint,
        "rule_version": rule_version,
        "prompt_source_fingerprint": prompt_source_fingerprint,
        "audit_scope": audit_scope,
        "blocking_count": len(blocking or []),
        # v6.143（P6）：清单截断与否**显式声明**——此前读者只能靠「正好 20 条」
        # 猜是否被截断（36 条的 run 也只看到 20 条）。
        "blocking_truncated": len(blocking or []) > 20,
        "blocking": _cap_blocking_for_manifest(blocking),
        "detail": detail,
        "artifacts": {},
    }
    if status == "COMMITTED":
        for name in STEP01_ARTIFACT_FILES:
            path = Path(chapter_dir) / name
            if path.exists():
                payload["artifacts"][name] = {
                    "sha1": sha1_file(path),
                    "bytes": path.stat().st_size,
                }
    return payload


def write_step01_commit_manifest(chapter_dir: Path, manifest: dict) -> Path:
    """原子写 manifest（提交完成的唯一标志）."""
    from shared.json_io import atomic_dump_json

    path = Path(chapter_dir) / STEP01_COMMIT_FILENAME
    atomic_dump_json(path, manifest, ensure_ascii=False, indent=2)
    return path


def verify_step01_commit_manifest(chapter_dir: Path, script: dict,
                                  *, stage: str = "step01") -> dict:
    """核对三份正式产物与 `step01_commit.json` 的 revision 与摘要.

    失败一律抛 `QualityGateError`（fail-closed）：manifest 缺失、状态不是
    COMMITTED、revision 与当前 script 不符、任一产物缺失或摘要不符。
    """
    path = Path(chapter_dir) / STEP01_COMMIT_FILENAME
    if not path.exists():
        raise QualityGateError(
            f"{stage} commit manifest missing: {path}. "
            "step01 must finish its atomic commit before step02 consumes the outputs."
        )
    manifest = load_gate(path)
    status = str(manifest.get("status") or "").strip().upper()
    if status != "COMMITTED":
        raise QualityGateError(
            f"{stage} commit manifest is not COMMITTED (status={status or 'missing'}, "
            f"blocking={manifest.get('blocking_count')}): {manifest.get('detail') or ''}"
        )
    revision = ensure_script_revision(script)
    manifest_rev = str(manifest.get("script_revision") or "").strip()
    if manifest_rev != revision:
        raise QualityGateError(
            f"{stage} commit manifest revision mismatch: "
            f"manifest={manifest_rev or 'missing'} current={revision}"
        )
    artifacts = manifest.get("artifacts") or {}
    if not isinstance(artifacts, dict) or not artifacts:
        raise QualityGateError(f"{stage} commit manifest carries no artifact digests")
    for name in STEP01_ARTIFACT_FILES:
        item = artifacts.get(name)
        if not isinstance(item, dict) or not item.get("sha1"):
            raise QualityGateError(
                f"{stage} commit manifest is missing the digest of {name}")
        target = Path(chapter_dir) / name
        if not target.exists():
            raise QualityGateError(f"{stage} committed artifact missing: {target}")
        actual = sha1_file(target)
        if actual != str(item.get("sha1")):
            raise QualityGateError(
                f"{stage} committed artifact changed after commit: {name} "
                f"(manifest={item.get('sha1')} actual={actual})"
            )
    return manifest


def require_step01_upstream(chapter_dir: Path, script: "dict | None" = None,
                           *, stage: str = "step01") -> dict:
    """**step01 上游准入的唯一入口**：语义门 + commit manifest 两道都要过（v6.111）.

    为什么要有这个函数：此前四个消费点各写各的——`video_ab_experiment` 调两道，
    而 `experiment_context` / 联系表 / prompt 基线工具只调 `verify_step01_commit_manifest`。
    manifest 只证明「三份产物的摘要与 revision 一致」，**不直接证明 quality/audit
    是 PASS**（报告文件若被单独改动，manifest 摘要校验未必覆盖到）。只调一道的
    工具会把不 eligible 的上游标成 `eligible`。

    与真实生成走**同一对检查**（`require_semantic_gate` + `verify_step01_commit_manifest`），
    避免「工具说可以、真跑被拒」的分叉。

    Returns:
        manifest dict（调用方常要 revision / blocking_count）。

    Raises:
        QualityGateError: 任一环节不通过。
    """
    ch = Path(chapter_dir)
    if script is None:
        import json as _json
        try:
            script = _json.loads((ch / "script_result.json").read_text(encoding="utf-8"))
        except Exception as exc:
            raise QualityGateError(f"{stage} 上游 script_result.json 读取失败: {exc}")
    require_semantic_gate(ch / "step01_quality.json", ch / "step01_audit.json",
                          script, stage=stage)
    return verify_step01_commit_manifest(ch, script, stage=stage)


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
    "STEP01_ARTIFACT_FILES",
    "STEP01_COMMIT_FILENAME",
    "build_step01_commit_manifest",
    "sha1_file",
    "verify_step01_commit_manifest",
    "write_step01_commit_manifest",
    "semantic_payload",
    "semantic_payload_digest",
    "shot_semantic_digest",
    "diff_semantic_payload",
    "diff_semantic_structure",
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
