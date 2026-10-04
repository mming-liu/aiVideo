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
import re
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


#: revision 算法版本（2026-09-30 复核 P1：「换哈希算法会让旧欠账失去匹配」）.
#: v1 = 只 hash 语义 payload；v2 = 语义 payload + 契约身份指纹。
#: 版本升级时**未解决的欠账不能因换哈希消失**——台账读写两侧一律用
#: `script_revision_aliases()` 的全部版本做匹配（见 `main._load_blocked_revisions`
#: 与 BEST-GUARD 候选筛选）。
SCRIPT_REVISION_VERSION = 2

#: 「字段不存在」的显式标记。**不能**用 `None` 代替——`null` 与缺失在事件契约里
#: 判据不同（`null`/标量 = 声明形态非法 → `plan_refs_missing`；缺失 = 没声明）。
_MISSING_FIELD = {"_absent": True}


def _typed(obj):
    """把任意值转成**保留存在性、原始类型与值**的可哈希结构.

    2026-09-30 复核第 4 轮 P1：契约身份此前用 `list(x or [])` / `list(x)` 取值，把
    `[]` 与 `null`、`["1"]` 与 `"1"` 折成同一个哈希，而事件契约对这两组的判据
    **不同** ⇒「同 revision、不同契约结论」；`beats=1` 更会让哈希函数直接抛
    `TypeError`（连结论都出不来）。未经校验的数据一律**不做隐式转换**。
    """
    if isinstance(obj, dict):
        return {"t": "dict",
                "v": [[_typed(k), _typed(v)] for k, v in
                      sorted(obj.items(),
                             key=lambda kv: (type(kv[0]).__name__, str(kv[0])))]}
    if isinstance(obj, (list, tuple)):
        return {"t": "list", "v": [_typed(x) for x in obj]}
    if isinstance(obj, bool):
        return {"t": "bool", "v": obj}
    if isinstance(obj, int):
        return {"t": "int", "v": obj}
    if isinstance(obj, float):
        return {"t": "float", "v": obj}
    if isinstance(obj, str):
        return {"t": "str", "v": obj}
    if obj is None:
        return {"t": "null", "v": None}
    return {"t": type(obj).__name__, "v": str(obj)}


def shot_contract_identity(shot) -> dict:
    """单镜**契约身份**：`index` + `plan_refs`——**全稿指纹与逐镜守卫共用这一处表示**.

    2026-09-30 复核第 4 轮 P2：契约声明此前有两套表示——全稿指纹自带一套，逐镜摘要
    (`shot_semantic_digest`) 完全看不见 ⇒ 改某一镜的 `plan_refs` 会换掉全稿 revision，
    却换不掉 `per_shot_revisions`，修复轮「未点名镜头不得变化」的守卫因此漏拦
    （`main.py` 的 `_non_target` 判定）。
    """
    if not isinstance(shot, dict):
        # 非 dict 镜头（含 `None`/标量）**原样留痕**，不静默丢弃：丢弃会把
        # 「第 3 个元素不是对象」与「只有 2 个元素」折成同一个哈希。
        return _typed(shot)
    return {k: (_typed(shot[k]) if k in shot else _MISSING_FIELD)
            for k in ("index", "plan_refs")}


def contract_identity(script: dict) -> dict:
    """全稿**契约身份**：`plan_id` + 逐镜（含 hook）契约身份——保留字段存在性."""
    if not isinstance(script, dict):
        return _typed(script)
    out = {"plan_id": (_typed(script["plan_id"]) if "plan_id" in script
                       else _MISSING_FIELD)}
    for _key, _single in (("shots", False), ("hook", True)):
        if _key not in script:
            out[_key] = _MISSING_FIELD
        elif _single or not isinstance(script[_key], list):
            # hook 是单对象；`shots` 不是列表（含 `None`）时原样留痕，不按空表处理。
            out[_key] = shot_contract_identity(script[_key])
        else:
            out[_key] = [shot_contract_identity(x) for x in script[_key]]
    return out


def contract_fingerprint(script: dict) -> str:
    """**事件履约契约**指纹：`plan_id` + 每镜（含 hook）的 `plan_refs`.

    2026-09-30 复核 P1：改 `plan_id` 或 `plan_refs` 会让事件检查从通过变成失败，但
    `compute_script_revision` **完全不变** ⇒ 「同一 revision」可以带着**不同的承载契约**
    复用审计/质检结论。契约是本集内容取舍的声明，必须与稿件语义一起进身份判定。
    """
    return _hash_json(contract_identity(script))


#: 各版 revision 算法的**已知版本号**（新增版本时往后追加；逐版一起算）
KNOWN_REVISION_VERSIONS = (1, 2)


def script_revision_for_version(script: dict, version: int) -> str:
    """按**指定版本**的算法算身份.

    版本 1 = 只 hash 语义 payload（历史算法，值逐字保持不变）；
    版本 2 = 语义 payload + 契约指纹（带 `v2|` 标签，避免与将来版本串号）。
    """
    _payload = semantic_payload_json(script)
    if int(version) <= 1:
        return hashlib.sha256(_payload.encode("utf-8")).hexdigest()
    return hashlib.sha256(
        ("v%d|%s|%s" % (int(version), _payload,
                        contract_fingerprint(script))).encode("utf-8")).hexdigest()


def script_revision_aliases(script: dict) -> dict:
    """`{版本号: revision}`：同一份稿件在**各版算法**下的身份.

    2026-09-30 复核第 4 轮 P1：换哈希算法后，同一份**完全未改**的历史稿在新算法下
    拿到另一个 revision，而欠账台账里存的还是旧值 ⇒「旧审计欠账仍有效，但选稿查询
    命不中它」——所以各版身份要一起算出来。

    2026-09-30 复核第 5 轮 P1-2：**必须按版本比对，不能把各版本混成一个等价关系**。
    两份稿件文字相同、契约不同时，v2 身份不同而 v1 身份相同；若拿「任一版本命中」
    当判同/欠账匹配，「引用修好、契约已通过」的好稿就会被「同一段文字、契约破损」
    的旧欠账连坐（离线反例已确认）。调用方（台账读写、选稿判同）一律逐版比对：
    现行版本用完整身份，旧版本身份**只用于匹配明确属于那个版本的旧记录**。
    """
    return {int(v): script_revision_for_version(script, int(v))
            for v in KNOWN_REVISION_VERSIONS}


def compute_script_revision(script: dict) -> str:
    """稿件 revision = **现行版本**的完整身份（语义 payload + 契约指纹）."""
    return script_revision_for_version(script, SCRIPT_REVISION_VERSION)


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
    """单镜摘要哈希：语义白名单字段 **+ 契约身份**（`plan_refs`/`index`）.

    2026-09-30 复核第 4 轮 P2：`plan_refs` 是**消费品**（事件履约契约、B↔D 配对、
    承载关系都读它），却不在 `_SEMANTIC_SHOT_FIELDS` 里 ⇒ 逐镜 revision 看不见
    它的变化，而全稿 revision 看得见（契约指纹）。修 v6.79 给 `temporal_plan`
    补白名单时是同一条道理：**消费方读的字段必须在逐镜身份里**。
    契约身份用 `shot_contract_identity()`——与全稿指纹共用同一处表示。
    """
    return _hash_json({"semantic": _semantic_shot(shot),
                       "contract": shot_contract_identity(shot)})


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
        # 2026-09-30 复核第 4 轮 P2：这里必须走 `shot_semantic_digest`（= 语义 +
        # **契约身份**）。旧实现直接调 `_semantic_shot`，于是同一次改动的两个摘要
        # 各说各话——`semantic_payload_digest` 看得见 `plan_refs` 变化，逐镜
        # revision 看不见，修复轮「未点名镜头不得变化」的守卫正好漏在这半边。
        out[key] = shot_semantic_digest(s)
    hook = script.get("hook")
    if isinstance(hook, dict):
        out["hook"] = shot_semantic_digest(hook)
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
                                 prompt_source_fingerprint: str = "",
                                 audit_status: str = "") -> dict:
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
        # v6.152（C2）：本次终局审计的**状态**（ok / request_failed / truncated /
        # parse_failed …）。BLOCK 时正式产物 `step01_audit.json` 刻意保持旧 revision
        # 不动，读者只凭它无从知道「本次到底审没审出来」——这个字段就是那条区分：
        # 空串 = 本次 BLOCK 与审计无关（确定性门 / 台词门 / 结构门）。
        "audit_status": audit_status,
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


_CHAPTER_DIR_RE = re.compile(r"^chapter_(\d+)$")


def _chapter_from_dir(chapter_dir: Path) -> "int | None":
    """从章目录名解析章号（`chapter_002` -> 2）；不是章目录（如测试用的裸 tmp_path）返回 None。"""
    m = _CHAPTER_DIR_RE.match(Path(chapter_dir).name)
    return int(m.group(1)) if m else None


def _verify_chapter_identity(chapter_dir: Path, script: dict, stage: str,
                             expected_chapter: "int | None") -> None:
    """章号三方一致性：请求的章（`expected_chapter`）/ 目录名 / 脚本内 `chapter_index`.

    为什么要有这道门：章号此前**没有任何跨产物校验**。用错章跑起来后果是静默的——
    `global/` 下的角色/场景/道具 bank 按章号记 `last_chapter`（跨章共享，写错就永久污染），
    窗口判据 `NOVEL_CHAPTER - 1` 依然成立，`source_hash` 里含章号只会让缓存持续 miss。
    目录名与脚本章号不一致 = 这个目录里的产物不是这一章的，读下去等于拿错章的内容生图。

    降噪（不误伤，也不静默）：目录名不含 `chapter_NNN`、或脚本缺 `chapter_index`
    字段时无法判定——打印 WARN 后继续，不 fail（测试用裸 tmp_path；
    `chapter_index` 由 `get_chapter_meta` 单点写入，历史产物可能没有）。
    """
    dir_chapter = _chapter_from_dir(chapter_dir)
    raw = script.get("chapter_index")
    try:
        script_chapter = int(raw) if raw is not None else None
    except (TypeError, ValueError):
        script_chapter = None

    if expected_chapter is not None:
        want = int(expected_chapter)
        if dir_chapter is not None and dir_chapter != want:
            raise QualityGateError(
                f"{stage} chapter mismatch: 请求第 {want} 章，但目录是 {Path(chapter_dir).name}"
                f"（{chapter_dir}）——章/部参数与目标目录不一致，拒绝在错章产物上继续。"
                "检查 --chapter/--book/--book-name 与 .env.content 的 NOVEL_CHAPTER/BOOK_*。"
            )
        if script_chapter is not None and script_chapter != want:
            raise QualityGateError(
                f"{stage} chapter mismatch: 请求第 {want} 章，但 script_result.json 里是"
                f" chapter_index={script_chapter}（{chapter_dir}）。"
            )
    if dir_chapter is not None and script_chapter is not None and dir_chapter != script_chapter:
        raise QualityGateError(
            f"{stage} chapter mismatch: 目录 {Path(chapter_dir).name} 与 script_result.json 的"
            f" chapter_index={script_chapter} 不是同一章（{chapter_dir}）；产物放错目录，拒绝继续。"
        )
    if dir_chapter is None or script_chapter is None:
        missing = (f"目录名 {Path(chapter_dir).name!r} 不含 chapter_NNN"
                   if dir_chapter is None else "script_result.json 缺 chapter_index 字段")
        print(f"[WARN] {stage} 章号一致性未校验（{missing}）：{chapter_dir}")


def verify_step01_commit_manifest(chapter_dir: Path, script: dict,
                                  *, stage: str = "step01",
                                  expected_chapter: "int | None" = None) -> dict:
    """核对三份正式产物与 `step01_commit.json` 的 revision 与摘要.

    失败一律抛 `QualityGateError`（fail-closed）：manifest 缺失、状态不是
    COMMITTED、revision 与当前 script 不符、任一产物缺失或摘要不符；
    另加章号一致性（见 `_verify_chapter_identity`）——`expected_chapter` 由
    **请求方**显式传入（阶段入口传本次生效的章号），不在此处读 env：
    工具（如联系表 / prompt 基线）允许按 `--chapter` 检查任意章目录，
    用 env 比对会把它们的合法调用判成错章。
    """
    _verify_chapter_identity(Path(chapter_dir), script, stage, expected_chapter)
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
                           *, stage: str = "step01",
                           expected_chapter: "int | None" = None) -> dict:
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
    return verify_step01_commit_manifest(ch, script, stage=stage,
                                         expected_chapter=expected_chapter)


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
    "SCRIPT_REVISION_VERSION",
    "KNOWN_REVISION_VERSIONS",
    "script_revision_for_version",
    "contract_identity",
    "contract_fingerprint",
    "shot_contract_identity",
    "script_revision_aliases",
    "compute_script_revision",
    "ensure_script_revision",
    "attach_gate_metadata",
    "load_gate",
    "require_gate",
    "require_semantic_gate",
    "verify_report_outputs",
]
