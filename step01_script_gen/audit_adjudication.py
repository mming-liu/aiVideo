"""S03：审计发现的**影子裁决**（shadow adjudication，2026-11-11）.

## 解除的阻塞

现行提交链里，模型自报的严重度**直接**推动返工，而「目标镜头未改动」又能成为
降低严重度的依据（`main._downgrade_out_of_delta_audit_issues`）。两条规则叠在一起，
同一份内容会因「这次有没有动它」得到不同裁决，内容是否成立与修改范围发生耦合。

本模块把三件事分开记账：

1. **原始发现**：issue 原样保留（不改 severity、不改 message、不改 repair_targets）；
2. **确定性核验**：代码能证明的部分——镜头/字段是否存在、证据是否逐字、修复目标
   是否越界、帧与字段是否自洽、枚举是否合法、声称的重复是否真的重复；
3. **建议处置**：四类之一，附依据与来源。

## 处置的边界

`disposition` **不由模型严重度决定**：

- 确定性核验**证伪**（枚举合法、声称的重复并不存在、转场已有显式声明等）
  → `legal_expression`（记录反证）；
- 确定性核验**证实**（逐字证据成立且命中确定性规则）
  → `confirmed_defect`（可进入修订）；
- 引用不成立（镜头/字段不存在、证据非逐字、目标越界、帧与字段不自洽）
  → `insufficient_evidence`（保留待裁决，**不得**当作已解决）；
- 其余：advisory → `improvement_suggestion`（进内容评阅）；blocking/warning
  → `insufficient_evidence` 且 `needs_semantic_review=True`（语义裁决留给冻结输入，
  模型自述不能单独证明问题成立或已解决）。

## 影子边界

本模块**不参与准入**：不修改 issue、不驱动修复、不影响 blocking 计数。它的输出只进
隔离报告（`step01_audit_adjudication.json`）供并排比较。接入正式门禁属于准入语义
变更，需在 S06 对照结果之后统一裁决。

## S07（2026-11-12）：脚本级确定性检查

`script_findings.scan_script` 对**全稿**做三类检查（重复文本、景别矛盾、道具/状态链），
不再只依赖模型先报出问题。命中同一镜头/字段/证据时，本模块把对应 issue 判为
`confirmed_defect`；确定性反证（已声明的运镜轨迹、已写出的取回动作、显式时间跳跃）
可判为 `legal_expression`；没有 finding 的仍按原逻辑留待语义裁决——**不把「没检出」
写成「没问题」**。报告仍是 `shadow_only`，不改变 severity 与 blocking。
"""
from __future__ import annotations

import re

from step01_script_gen.script_findings import (
    analyze_framing, has_acquisition, has_explicit_time_jump, scan_script)

#: 建议处置（四类，顺序即报告顺序）
DISPOSITIONS = (
    "confirmed_defect",
    "insufficient_evidence",
    "legal_expression",
    "improvement_suggestion",
)

#: 现行门禁里算 blocking 的严重度（与 `main` 的 `is_blocking` 同口径）
_BLOCKING_SEVERITIES = ("hard", "visual_risk", "blocking")

#: 确定性规则命中的 code 族（族名由审计侧 `model_audit_<CODE>` 携带，大小写无关）
_DUPLICATE_CODES = ("duplicate", "repetition", "repeat")
_STAGE_CODES = ("stage",)

#: 审计要求「把 stage 改成这些词」时，枚举合法性才能**准确反驳**该要求
#: （`process` / `result` 都不是 `KF_STAGE_ENUM` 的成员）。
_NON_ENUM_STAGE_WORDS = ("process", "result", "开始态", "结束态")


def _code_family(issue: dict) -> str:
    return str(issue.get("code") or "").strip().lower()


def _shots_by_id(script: dict) -> dict:
    out: dict = {}
    hook = script.get("hook")
    if isinstance(hook, dict):
        out["0"] = hook
        out["hook"] = hook
    for i, shot in enumerate(script.get("shots") or []):
        if not isinstance(shot, dict):
            continue
        sid = shot.get("index", i + 1)
        out[str(sid)] = shot
    return out


def _segments(path: str) -> list[str]:
    return [seg for seg in re.sub(r"\[(\d+)\]", r".\1", str(path or "").strip()).split(".")
            if seg]


def read_path(obj, path: str):
    """按 `a.b[2].c` 逐段取值；任一段缺失返回 `_MISSING` 哨兵（不猜、不补）."""
    cur = obj
    for seg in _segments(path):
        if isinstance(cur, dict):
            if seg not in cur:
                return _MISSING
            cur = cur[seg]
        elif isinstance(cur, list):
            if not seg.isdigit():
                return _MISSING
            i = int(seg)
            if i < 0 or i >= len(cur):
                return _MISSING
            cur = cur[i]
        else:
            return _MISSING
    return cur


class _Missing:
    def __repr__(self) -> str:  # pragma: no cover - 仅诊断用
        return "<missing>"


_MISSING = _Missing()


def path_exists(obj, path: str) -> bool:
    return read_path(obj, path) is not _MISSING


def _frame_of_path(path: str) -> str | None:
    """字段路径 → 它所属的帧标识；不属于任何帧时返回 `None`.

    `ff_desc` → `ff`；`lf_desc` → `lf`；`keyframes[N].*` / `frame_layouts.kfs[N]...`
    → `kf{N+1}`（与审计侧 `frame_id` 的写法一致）。其余字段返回 `None`（不适用）。
    """
    p = re.sub(r"\[(\d+)\]", r".\1", str(path or "").strip())
    if p == "ff_desc" or p.startswith("frame_layouts.ff"):
        return "ff"
    if p == "lf_desc" or p.startswith("frame_layouts.lf"):
        return "lf"
    m = re.match(r"^(?:keyframes|frame_layouts\.kfs)\.(\d+)(?:\.|$)", p)
    if m:
        return "kf%d" % (int(m.group(1)) + 1)
    return None


def _text_of(value) -> str | None:
    """可逐字比对的文本视图：字符串原样，字符串列表按分隔拼接，其它返回 `None`."""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value and all(isinstance(x, str) for x in value):
        return "\n".join(value)
    return None


def _kf_index(path: str) -> "int | None":
    m = re.match(r"^(?:keyframes|frame_layouts\.kfs)\[(\d+)\]", str(path or "").strip())
    return int(m.group(1)) if m else None


def _sibling_desc_path(field_path: str, frame_id: str) -> str:
    """`props` 路径 → 同一帧的描述字段（道具状态是「描述 ↔ 布局」两侧的事）."""
    if frame_id == "ff":
        return "ff_desc"
    if frame_id == "lf":
        return "lf_desc"
    if frame_id.startswith("kf"):
        kf = _kf_index(field_path)
        if kf is not None:
            return "keyframes[%d].desc" % kf
    return ""


def _frame_desc_text(shot: dict, frame_id: str, field_path: str) -> str:
    """该帧的描述文本（同帧两侧矛盾要用它作依据；取不到返回空串）."""
    path = ""
    if frame_id == "ff":
        path = "ff_desc"
    elif frame_id == "lf":
        path = "lf_desc"
    elif frame_id.startswith("kf"):
        kf = _kf_index(field_path)
        if kf is not None:
            path = "keyframes[%d].desc" % kf
    if not path:
        return ""
    return _text_of(read_path(shot, path)) or ""


def _stage_enum() -> tuple:
    try:
        from shared.kf_time import KF_STAGE_ENUM
        return tuple(KF_STAGE_ENUM)
    except Exception:  # pragma: no cover - 枚举缺失时不做确定性判定
        return ()


def deterministic_checks(issue: dict, script: dict, *,
                         findings: "list | None" = None) -> dict:
    """对单条 issue 做**只读**确定性核验，返回可复核的事实（不下结论）.

    `findings` 由调用方一次算好后传入（`build_shadow_report` 会共享同一份全稿
    检查结果）；不传时按本条 issue 现场扫描，保持旧调用方式可用。
    """
    shots = _shots_by_id(script)
    sid = str(issue.get("shot_id") or "").strip()
    field_path = str(issue.get("field_path") or "").strip()
    evidence = str(issue.get("evidence") or "")
    shot = shots.get(sid) if sid else None
    if findings is None:
        try:
            findings = scan_script(script) if isinstance(script, dict) else []
        except Exception:                                      # noqa: BLE001 - 诊断不改变结论
            findings = []

    value = read_path(shot, field_path) if (shot is not None and field_path) else _MISSING
    field_exists = value is not _MISSING
    text = _text_of(value) if field_exists else None
    evidence_verbatim = bool(text is not None and evidence and evidence in text)

    frame_expected = _frame_of_path(field_path)
    frame_id = str(issue.get("frame_id") or "").strip()
    frame_consistent = None
    if frame_expected is not None and frame_id:
        frame_consistent = frame_id == frame_expected

    # 供确定性关系使用：原值（列表成员/道具清单）、同帧描述与同帧文本
    sibling_text = ""
    if field_exists and field_path.endswith("props"):
        _sib = _sibling_desc_path(field_path, frame_expected or frame_id)
        if _sib:
            sibling_text = _text_of(read_path(shot, _sib)) or ""
    frame_text = ""
    if field_exists:
        frame_text = _frame_desc_text(shot, frame_expected or frame_id, field_path)

    targets = issue.get("repair_targets")
    targets_checked = 0
    targets_rejected: list[str] = []
    for t in (targets if isinstance(targets, list) else []):
        if not isinstance(t, dict):
            targets_checked += 1
            targets_rejected.append("<非对象目标>")
            continue
        targets_checked += 1
        tsid = str(t.get("shot_id") or "").strip()
        tpath = str(t.get("field_path") or "").strip()
        tshot = shots.get(tsid)
        if tshot is None:
            targets_rejected.append("%s（镜头不存在）" % tsid)
        elif not tpath or not path_exists(tshot, tpath):
            targets_rejected.append("%s/%s（字段不存在）" % (tsid, tpath))

    return {
        "shot_exists": shot is not None,
        "field_exists": field_exists,
        "field_value_head": (text[:200] if text else None),
        # 原值（列表成员 / 道具清单）、同帧描述与同帧文本，供确定性关系判定；不进报告
        "field_value_raw": (value if field_exists else None),
        "sibling_text": sibling_text,
        "frame_text": frame_text,
        # 完整文本只供判定使用，**不进报告**（报告只留 head）
        "field_text": text,
        "evidence_verbatim": evidence_verbatim,
        "evidence_checked": text is not None,
        "frame_expected": frame_expected,
        "frame_consistent": frame_consistent,
        "targets_checked": targets_checked,
        "targets_rejected": targets_rejected,
        # S07：全稿确定性检查（内部使用；公开视图只留条数）
        "script_findings": len(findings),
        "_findings": findings,
        "_shot": shot,
    }


def _duplicate_count(text: str, fragment: str) -> int:
    """`fragment` 在 `text` 中的出现次数（逐字、不重叠）."""
    if not text or not fragment:
        return 0
    return len(re.findall(re.escape(fragment), text))


def _repeat_factor(fragment: str) -> int:
    """`fragment` 自身是某个更短单元的几次重复（`X X` → 2）；不是重复返回 1.

    审计侧常把重复片段**写成两遍**当证据（`（A）（A）`）。只数「整条证据出现几次」
    会把这种写法数成 1 次，从而把真实重复误判成「未重复」——2026-11-11 在冻结
    候选的 shot6 上实测到过这个误判。
    """
    n = len(fragment)
    for k in range(1, n // 2 + 1):
        if n % k == 0 and fragment == fragment[:k] * (n // k):
            return n // k
    return 1


def _entry_names(value) -> list[str]:
    """`frame_layouts.*` 列表 → 出镜条目名（只取结构内的 `name`，不猜别名）."""
    if not isinstance(value, list):
        return []
    return [str(e.get("name") or "").strip() for e in value if isinstance(e, dict)]


def _layout_props(shot: dict) -> list[str]:
    """该镜 frame_layouts 里出现过的道具名（去重、保序）."""
    out: list[str] = []
    layouts = shot.get("frame_layouts") if isinstance(shot.get("frame_layouts"), dict) else {}
    buckets = [layouts.get("ff") or [], layouts.get("lf") or []]
    buckets += [frame or [] for frame in (layouts.get("kfs") or [])]
    for bucket in buckets:
        for entry in bucket:
            if not isinstance(entry, dict):
                continue
            for prop in entry.get("props") or []:
                name = str(prop or "").strip()
                if name and name not in out:
                    out.append(name)
    return out


def _issue_prop(issue: dict, shot: dict, text: str) -> str:
    """从 issue 与目标字段里定位道具名（只取本稿布局里真实出现的道具）."""
    evidence = str(issue.get("evidence") or "")
    for name in _layout_props(shot):
        candidates = [name]
        if len(name) >= 3:
            candidates.append(name[-2:])
        for candidate in sorted(candidates, key=len, reverse=True):
            if candidate and (candidate in evidence or candidate in text):
                return candidate
    evidence = evidence.strip()
    if 2 <= len(evidence) <= 8 and evidence in text:
        return evidence
    return ""


def _framing_refutation(issue: dict, checks: dict) -> str:
    """景别类主张的确定性反证（运镜轨迹 / 同镜声明一致）."""
    shot = checks.get("_shot")
    if not isinstance(shot, dict):
        return ""
    evidence = str(issue.get("evidence") or "")
    field_path = str(issue.get("field_path") or "").strip()
    code = _code_family(issue)
    message = str(issue.get("message") or "") + " " + str(issue.get("minimal_fix") or "")
    info = analyze_framing(shot)
    levels = info.get("levels") or {}
    # 只有「这条主张确实在谈景别」时才做景别反证：族名、消息、证据或
    # 证据里逐字出现的景别词，任一成立即可。泛泛的 FIELD_CONFLICT 不据此放行。
    evidence_is_framing = any(v[0] in evidence for v in levels.values())
    looks_framing = (any(k in code for k in ("framing", "shot_size"))
                     or "景别" in evidence or "景别" in message or evidence_is_framing)
    if not looks_framing:
        return ""
    if field_path not in info.get("fields", {}) and field_path not in levels:
        return ""
    term = levels[field_path][0] if field_path in levels else ""
    if info.get("trajectory"):
        if field_path in (info.get("multi_level_fields") or []) \
                or (term and term in info.get("motion", "")):
            return "本镜已声明运镜/景别轨迹，静态景别矛盾不成立"
    if field_path in levels and len(levels) >= 2:
        idx = levels[field_path][2]
        others = [v[2] for p, v in levels.items() if p != field_path]
        if others and all(abs(idx - other) < 3 for other in others):
            return "同镜景别声明一致（相差 <3 级），不构成矛盾"
    return ""


def _prop_refutation(issue: dict, checks: dict) -> str:
    """道具/状态类主张的确定性反证（显式时间跳跃 / 已写出的取回动作）."""
    shot = checks.get("_shot")
    if not isinstance(shot, dict):
        return ""
    code = _code_family(issue)
    message = str(issue.get("message") or "") + " " + str(issue.get("minimal_fix") or "")
    looks_prop = (any(k in code for k in ("prop", "state", "continuity"))
                  or "道具" in message or "状态" in message)
    if not looks_prop:
        return ""
    if has_explicit_time_jump(shot):
        return "本镜声明了显式时间跳跃，前后状态差异不构成矛盾"
    text = str(checks.get("field_text") or "")
    motion = str(shot.get("motion_desc") or "")
    prop = _issue_prop(issue, shot, text)
    if prop and (has_acquisition(text, prop) or has_acquisition(motion, prop)):
        return "本帧/本镜已有取回「%s」的动作，状态变化有依据" % prop
    return ""


def _match_finding(issue: dict, checks: dict, findings: list) -> "dict | None":
    """issue 是否被某条全稿确定性 finding 覆盖（同镜 + 字段/证据/主体）."""
    sid = str(issue.get("shot_id") or "").strip()
    field_path = str(issue.get("field_path") or "").strip()
    evidence = str(issue.get("evidence") or "")
    text = str(checks.get("field_text") or "")
    if not sid or not findings:
        return None
    for finding in findings:
        if str(finding.get("shot_id") or "") != sid:
            continue
        finding_evidence = str(finding.get("evidence") or "")
        overlap = bool(finding_evidence and (
            finding_evidence in evidence or (evidence and evidence in finding_evidence)))
        same_field = str(finding.get("field_path") or "") == field_path
        if finding.get("code") in ("DUPLICATE_TEXT", "FRAMING_CONFLICT"):
            if same_field and (overlap or (finding_evidence and finding_evidence in text)):
                return finding
            continue
        if same_field and (overlap or (finding_evidence and finding_evidence in text)):
            return finding
        subject = str(finding.get("prop") or finding.get("subject") or "")
        if subject and subject in (evidence + "\n" + text):
            return finding
    return None


def deterministic_verdict(issue: dict, checks: dict) -> tuple[str, list[str]]:
    """确定性结论 → `(verdict, reasons)`；verdict ∈ `confirmed / refuted / undecidable`."""
    reasons: list[str] = []
    family = _code_family(issue)
    field_path = str(issue.get("field_path") or "").strip()

    if not checks["shot_exists"]:
        return "undecidable", ["引用的镜头不在本稿"]
    if not checks["field_exists"]:
        return "undecidable", ["引用的字段不在本稿"]
    if checks["frame_consistent"] is False:
        return "undecidable", [
            "frame_id=%s 与字段路径所属帧 %s 不一致" % (
                issue.get("frame_id"), checks["frame_expected"])]

    # --- 确定性关系：出镜列表里到底有没有这个实体（**只报事实，不判义务**） ---
    # 「列表没有乙」只能证明缺席；**不能**证明本帧要求乙出镜。缺席在本帧合法
    # （乙明确在画外）时，把它判成 confirmed 会把合法表达算成缺陷。
    # 只有同帧文本（该帧描述）点名了这个实体、布局却没有它，才是可确定的两侧矛盾。
    if field_path.startswith("frame_layouts") and "presence" in family:
        names = _entry_names(checks.get("field_value_raw"))
        entity = str(issue.get("entity_id") or "").strip()
        if entity and names:
            if entity in names:
                return "refuted", ["该帧出镜列表已有 %s（%s）" % (entity, "/".join(names))]
            frame_text = str(checks.get("frame_text") or "")
            if frame_text and entity in frame_text:
                return "confirmed", [
                    "同帧描述点名 %s、出镜列表没有它（现有：%s）"
                    % (entity, "/".join(names))]
            return "undecidable", [
                "出镜列表确实没有 %s（现有：%s），但本帧没有「必须出镜」的依据"
                % (entity, "/".join(names))]

    # --- 确定性关系：描述里点名的道具是否进了布局 props（同帧两侧矛盾） ---
    if field_path.endswith("props") and "prop" in family:
        ev = str(issue.get("evidence") or "").strip()
        props = checks.get("field_value_raw")
        props_text = " ".join(props) if isinstance(props, list) else str(props or "")
        sibling = str(checks.get("sibling_text") or "")
        if 0 < len(ev) <= 8 and sibling and ev in sibling:
            if ev in props_text:
                return "refuted", ["props 已列 %s" % ev]
            return "confirmed", ["描述里有 %s，props 未列（现有：%s）" % (ev, props_text or "空")]

    # --- 枚举合法性：只证明「值合法」，不证明「阶段与动作内容匹配」 ---
    # 只有原问题**明确要求改成非枚举词**时，枚举才能准确反驳它；
    # 否则「stage=approach 合法」与「approach 与动作不符」是两件事。
    if field_path.endswith("stage") and any(k in family for k in _STAGE_CODES):
        enum = _stage_enum()
        raw = str(issue.get("evidence") or "").strip()
        text = str(issue.get("message") or "") + " " + str(issue.get("minimal_fix") or "")
        _asks_non_enum = any(w in text for w in _NON_ENUM_STAGE_WORDS)
        if enum and raw and raw in enum and _asks_non_enum:
            return "refuted", ["stage=%s 属合法枚举，且原问题要求改成非枚举词" % raw]
        if enum and raw and raw not in enum:
            return "confirmed", ["stage=%s 不在合法枚举 %s 内" % (raw, "/".join(enum))]
        return "undecidable", ["stage=%s 的合法性不回答「阶段与动作是否匹配」" % (raw or "?")]

    # --- 转场：字段非空只证明「已声明」，不证明「转场语义正确」 ---
    if "transition" in family and field_path == "clip_transition":
        text = str(issue.get("message") or "") + " " + str(issue.get("minimal_fix") or "")
        declared = bool((checks.get("field_text") or "").strip())
        if declared and ("缺" in text or "没有" in text) and "转场" in text:
            return "refuted", ["转场字段已有声明：%s" % (checks.get("field_text") or "")[:40]]
        return "undecidable", ["转场已声明不等于转场语义成立，需按冻结输入裁决"]

    # --- 确定性证伪 ---
    if any(k in family for k in _DUPLICATE_CODES):
        text = checks.get("field_text") or ""
        ev = str(issue.get("evidence") or "")
        if not checks["evidence_verbatim"]:
            return "undecidable", ["逐字证据不成立，无法核对『重复』主张"]
        if _repeat_factor(ev) >= 2:
            return "confirmed", ["逐字证据本身即 %d 次重复" % _repeat_factor(ev)]
        if _duplicate_count(text, ev) >= 2:
            return "confirmed", ["逐字证据在字段内出现 %d 次" % _duplicate_count(text, ev)]
        return "undecidable", ["逐字证据只出现 1 次，不足以支持『重复』主张"]

    # --- S07：全稿确定性检查命中 → 确认；确定性反证 → 合法表达 ---
    # 放在具体关系判定之后：props 缺项、stage 非法等既有更精确的规则优先。
    matched = _match_finding(issue, checks, checks.get("_findings") or [])
    if matched is not None:
        return "confirmed", ["确定性检查命中 %s：%s"
                             % (matched.get("code"), matched.get("detail"))]
    refutation = _framing_refutation(issue, checks) or _prop_refutation(issue, checks)
    if refutation:
        return "refuted", [refutation]

    reasons.append("确定性规则未覆盖该缺陷族，需按冻结输入做语义裁决")
    return "undecidable", reasons


def _is_blocking(issue: dict) -> bool:
    return str(issue.get("severity") or "").strip().lower() in _BLOCKING_SEVERITIES


def _was_downgraded(issue: dict) -> bool:
    """该条是否被现行机制降级过（`main` 的两处降级都会留标记）."""
    return bool(issue.get("_downgraded_locked_only") or issue.get("out_of_delta_scope"))


def adjudicate_issue(issue: dict, script: dict, *, context: "dict | None" = None,
                     findings: "list | None" = None) -> dict:
    """单条 issue → 裁决记录（原始发现原样保留在 `issue` 字段里）."""
    checks = deterministic_checks(issue, script, findings=findings)
    verdict, reasons = deterministic_verdict(issue, checks)
    matched = _match_finding(issue, checks, checks.get("_findings") or [])
    checks_public = {k: v for k, v in checks.items()
                     if k not in ("field_text", "field_value_raw", "sibling_text",
                                  "frame_text", "_findings", "_shot")}

    if verdict == "confirmed":
        disposition = "confirmed_defect"
    elif verdict == "refuted":
        disposition = "legal_expression"
    else:
        sev = str(issue.get("severity") or "").strip().lower()
        disposition = ("improvement_suggestion"
                       if sev in ("advisory", "info", "video_advisory")
                       else "insufficient_evidence")

    needs_semantic_review = disposition == "insufficient_evidence" and verdict == "undecidable"
    if not checks["shot_exists"] or not checks["field_exists"] \
            or checks["frame_consistent"] is False or checks["targets_rejected"]:
        needs_semantic_review = False
        if disposition == "confirmed_defect":
            # 引用不成立时不得进入修订：确定性事实优先于严重度
            disposition = "insufficient_evidence"
        reasons = list(reasons)
        if not checks["shot_exists"]:
            reasons.append("引用镜头不存在 → 引用不成立")
        if not checks["field_exists"]:
            reasons.append("引用字段不存在 → 引用不成立")
        if checks["frame_consistent"] is False:
            reasons.append("frame_id 与字段路径不一致 → 引用不成立")
        if checks["targets_rejected"]:
            reasons.append("修复目标越界：%s" % "；".join(checks["targets_rejected"][:3]))

    return {
        "issue": dict(issue),                     # 原始发现：原样保留
        "severity_original": str(issue.get("severity") or ""),
        "currently_blocking": _is_blocking(issue),
        "downgraded_by_gate": _was_downgraded(issue),
        "deterministic": checks_public,
        "verdict": verdict,
        "disposition": disposition,
        "needs_semantic_review": needs_semantic_review,
        "reasons": reasons,
        # S07：这条 issue 被哪条全稿确定性 finding 覆盖（没有则为空）
        "matched_findings": ([matched.get("code")] if matched else []),
        "sources": ["issue", "script_fields", "deterministic_rules"],
        "context": dict(context or {}),
    }


def build_shadow_report(audit: dict, script: dict, *,
                        context: "dict | None" = None) -> dict:
    """审计 payload → 影子裁决报告（含与现行门禁的并排差异）."""
    issues = [i for i in (audit.get("issues") or []) if isinstance(i, dict)]
    findings = scan_script(script) if isinstance(script, dict) else []
    records = [adjudicate_issue(i, script, context=context, findings=findings)
               for i in issues]
    findings_by_code: dict[str, int] = {}
    for finding in findings:
        code = str(finding.get("code") or "")
        findings_by_code[code] = findings_by_code.get(code, 0) + 1

    by_disposition = {d: 0 for d in DISPOSITIONS}
    for rec in records:
        by_disposition[rec["disposition"]] = by_disposition.get(rec["disposition"], 0) + 1

    hidden = [r for r in records
              if r["disposition"] == "confirmed_defect"
              and (r["downgraded_by_gate"]
                   or (str(r["severity_original"]).strip().lower() in _BLOCKING_SEVERITIES
                       and not r["currently_blocking"]))]
    # 被现行机制降级、但确定性层证明不了也证伪不了的条目：正是「内容是否成立与
    # 修改范围耦合」最需要人工看见的一类（不得算作已解决）。
    pending_downgraded = [r for r in records
                          if r["needs_semantic_review"] and r["downgraded_by_gate"]]
    blocked_legal = [r for r in records
                     if r["disposition"] == "legal_expression" and r["currently_blocking"]]
    broken_refs = [r for r in records
                   if not r["deterministic"]["shot_exists"]
                   or not r["deterministic"]["field_exists"]
                   or r["deterministic"]["frame_consistent"] is False
                   or r["deterministic"]["targets_rejected"]]

    return {
        "kind": "audit_adjudication_shadow",
        "version": 1,
        "audit_identity": {
            "input_revision": audit.get("input_revision"),
            "rule_source_fingerprint": audit.get("rule_source_fingerprint"),
            "audit_scope": audit.get("audit_scope"),
            "audit_prompt_version": audit.get("audit_prompt_version"),
            "request_identity": audit.get("request_identity"),
            "run_id": audit.get("run_id"),
        },
        "summary": {
            "issues": len(records),
            "by_disposition": by_disposition,
            "confirmed_defect_hidden_by_gate": len(hidden),
            "semantic_pending_and_downgraded": len(pending_downgraded),
            "legal_expression_blocked": len(blocked_legal),
            "broken_references": len(broken_refs),
            "needs_semantic_review": sum(1 for r in records if r["needs_semantic_review"]),
            # S07：与模型 issue 无关的全稿检查结果（诊断；不参与准入）
            "deterministic_findings": len(findings),
            "deterministic_findings_by_code": findings_by_code,
            "issues_matched_to_findings": sum(1 for r in records if r["matched_findings"]),
        },
        # S07：全稿确定性检查的完整记录。消费者可据此看到模型没报、代码能证的部分；
        # 它**不**进入 blocking 计数，也不改变 records 里的原始 issue。
        "deterministic_findings": {
            "count": len(findings),
            "by_code": findings_by_code,
            "records": findings,
        },
        "records": records,
        # 影子结论**不**参与准入：这里只给出并排视图，门禁仍由现行规则决定
        "gate_effect": "shadow_only",
    }


def render_report_text(report: dict) -> str:
    """报告 → 可读文本（供日志/评阅；不参与准入）."""
    s = report.get("summary") or {}
    lines = ["[ADJUDICATION] 影子裁决：%d 条（仅报告，不影响门禁）" % s.get("issues", 0)]
    for d in DISPOSITIONS:
        lines.append("  - %-22s %d" % (d, (s.get("by_disposition") or {}).get(d, 0)))
    if s.get("confirmed_defect_hidden_by_gate"):
        lines.append("  ! 已被降级/未阻断的确定缺陷：%d 条" % s["confirmed_defect_hidden_by_gate"])
    if s.get("semantic_pending_and_downgraded"):
        lines.append("  ! 已降级但仍待语义裁决：%d 条" % s["semantic_pending_and_downgraded"])
    if s.get("legal_expression_blocked"):
        lines.append("  ! 现行门禁仍阻断的合法表达：%d 条" % s["legal_expression_blocked"])
    if s.get("broken_references"):
        lines.append("  ! 引用不成立的发现：%d 条" % s["broken_references"])
    if s.get("deterministic_findings"):
        by_code = s.get("deterministic_findings_by_code") or {}
        lines.append("  · 全稿确定性检查：%d 条（仅报告，不参与门禁）%s"
                     % (s["deterministic_findings"],
                        ("：" + "/".join("%s=%d" % (k, v) for k, v in sorted(by_code.items())))
                        if by_code else ""))
    return "\n".join(lines)


def dump_report(path, report: dict) -> None:
    """原子写报告（`shared.json_io` 是唯一写入实现）."""
    from shared.json_io import atomic_dump_json
    atomic_dump_json(path, report, ensure_ascii=False, indent=2)


__all__ = [
    "DISPOSITIONS", "adjudicate_issue", "build_shadow_report", "deterministic_checks",
    "deterministic_verdict", "dump_report", "path_exists", "read_path",
    "render_report_text",
]
