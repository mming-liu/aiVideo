"""Clean up duplicated motion and invalid reaction tags after shot splitting."""
from __future__ import annotations

import copy
import difflib
import re

from .base import Constraint
from .types import RepairRecord, Severity, Violation
from step01_script_gen.state_updates import (
    extract_states, extract_states_with_status, merge_states, diff_states, render_delta, has_role_state_regression,
)


def _rebase_motion_times(text: str, offset: float) -> str:
    if not text or offset <= 0:
        return text
    out = []
    for atom in re.split(r"[;；。]", text):
        atom = atom.strip()
        if not atom:
            continue
        m = re.search(
            r"(\d+(?:\.\d+)?)\s*(?:-|~|至|到)\s*(\d+(?:\.\d+)?)\s*(?:秒|s)?",
            atom,
        )
        if not m:
            out.append(atom)
            continue
        a, b = float(m.group(1)), float(m.group(2))
        if a < offset - 0.01:
            out.append(atom)
            continue
        body = atom[m.end():].lstrip("：: ").strip()
        out.append(f"{max(0.0, a - offset):g}-{max(0.1, b - offset):g}秒：{body}")
    return "；".join(out)


def _split_clauses(text: str) -> list[str]:
    return [x for x in re.split(r"[，。；、]", text or "") if x.strip()]


def _split_segments(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"[;；]", text or "") if x.strip()]


def _match_segment(text: str):
    return re.match(
        r"(?P<range>\d+(?:\.\d+)?\s*(?:-|~|至|到)\s*\d+(?:\.\d+)?\s*秒?)"
        r"\s*[：:]\s*(?P<body>.*)",
        text or "",
    )


_HEAD_TOKENS = ("抬头", "低头", "仰面")
_BODY_TOKENS = ("跪坐", "站立", "俯身", "仰卧", "躺", "坐", "直起")
_STATE_TOKENS = _HEAD_TOKENS + _BODY_TOKENS


def _state_tokens(text: str) -> set[str]:
    return {token for token in _STATE_TOKENS if token in (text or "")}


def _named_states(text: str, names: list[str]) -> dict[str, set[str]]:
    states: dict[str, set[str]] = {}
    last_subject = ""
    for clause, _delimiter in _split_clauses_with_delimiters(text):
        mentioned = [name for name in names if name in clause]
        if mentioned:
            last_subject = mentioned[0]
        subject = mentioned[0] if mentioned else last_subject
        if not subject:
            continue
        tokens = {token for token in _STATE_TOKENS if token in clause}
        if tokens:
            states.setdefault(subject, set()).update(tokens)
    return states


def _split_clauses_with_delimiters(text: str) -> list[tuple[str, str]]:
    import re as _re
    parts = _re.split(r"(，|。|；|、|……)", str(text or ""))
    out = []
    for i in range(0, len(parts), 2):
        clause = parts[i].strip()
        delimiter = parts[i + 1] if i + 1 < len(parts) else ""
        if clause:
            out.append((clause, delimiter))
    return out


def _state_delta_body(current_texts: list[str], target_text: str,
                      names: list[str], evidence_text: str):
    current: dict[str, dict[str, str]] = {}
    unresolved: list[dict] = []
    for text in current_texts:
        states, pending = extract_states_with_status(text, names)
        current = merge_states(current, states)
        unresolved.extend(pending)
    target, pending = extract_states_with_status(target_text, names)
    unresolved.extend(pending)
    delta = diff_states(current, target)
    return render_delta(delta, evidence_text), unresolved, delta


def _intermediate_state_change(earlier_bodies: list[str], prev_lf: str,
                               candidate: str, names: list[str]) -> bool:
    previous = extract_states(prev_lf, names)
    candidate_states = extract_states(candidate, names)
    earlier_states = [extract_states(body, names) for body in earlier_bodies]
    return has_role_state_regression(earlier_states, previous, candidate_states)


def _boundary_score(candidate: str, prev_lf: str) -> float:
    clauses = _split_clauses(prev_lf)
    return max(
        (difflib.SequenceMatcher(None, candidate, clause).ratio() for clause in clauses),
        default=0.0,
    )


class SplitContinuityConstraint(Constraint):
    name = "split_continuity"
    severity = Severity.WARN

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = [s for s in (ctx.shots or []) if isinstance(s, dict)]
        by_index = {str(s.get("index")): s for s in shots}
        from step01_script_gen.split_visuals import _char_inert_in_text
        for shot in shots:
            idx = shot.get("index")
            try:
                f = float(idx)
            except (TypeError, ValueError):
                continue
            if abs(f - round(f)) < 1e-9:
                continue
            prev = by_index.get(str(round(f - 0.5, 4))) or by_index.get(str(int(f)))
            if isinstance(prev, dict):
                a_motion = str(prev.get("motion_desc") or "")
                b_motion = str(shot.get("motion_desc") or "")
                _offset = float(prev.get("duration_sec") or 0)
                if b_motion:
                    _range = re.search(
                        r"(\d+(?:\.\d+)?)\s*(?:-|~|至|到)\s*(\d+(?:\.\d+)?)",
                        b_motion,
                    )
                    if _range:
                        _start = float(_range.group(1))
                        _rebased = _rebase_motion_times(b_motion, _offset) if _offset > 0 else b_motion
                        if _rebased == b_motion and _start > 0:
                            _offset = _start
                            _rebased = _rebase_motion_times(b_motion, _offset)
                    else:
                        _rebased = b_motion
                    if _rebased and _rebased != b_motion:
                        shot["motion_desc"] = _rebased
                        for kf in shot.get("keyframes") or []:
                            if not isinstance(kf, dict):
                                continue
                            try:
                                _kt = float(kf.get("t_sec") or 0)
                            except (TypeError, ValueError):
                                continue
                            if _kt >= _offset - 0.01:
                                kf["t_sec"] = round(max(0.1, _kt - _offset), 3)
                        repairs.append(RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx,
                            action="rewrite",
                            target="motion_desc",
                            before=b_motion[:120],
                            after=_rebased[:120],
                            detail="后半镜动作/关键帧时间改为局部时间",
                        ))
                        b_motion = _rebased

                prev_lf = str(prev.get("lf_desc") or "").strip()
                if prev_lf:
                    if str(shot.get("ff_desc") or "").strip() != prev_lf:
                        shot["ff_desc"] = prev_lf
                        repairs.append(RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx,
                            action="rewrite",
                            target="ff_desc",
                            before=str(shot.get("ff_desc") or "")[:120],
                            after=prev_lf[:120],
                            detail="后半镜首帧状态对齐前半镜尾帧",
                        ))
                    prev_layouts = prev.get("frame_layouts")
                    prev_lf_layout = (
                        prev_layouts.get("lf")
                        if isinstance(prev_layouts, dict)
                        else None
                    )
                    if isinstance(prev_lf_layout, list) and prev_lf_layout:
                        layouts = shot.setdefault("frame_layouts", {})
                        if isinstance(layouts, dict):
                            layouts["ff"] = copy.deepcopy(prev_lf_layout)
                split_names = [
                    str(item.get("name") or "")
                    for item in (shot.get("char_layout") or [])
                    if isinstance(item, dict) and item.get("name")
                ]
                if prev_lf and b_motion:
                    rebuilt = []
                    earlier_bodies: list[str] = []
                    changed_boundary = False
                    for seg_i, atom in enumerate(_split_segments(b_motion)):
                        if seg_i == 0:
                            first_match = _match_segment(atom)
                            if first_match:
                                earlier_bodies.append(first_match.group("body").strip())
                            rebuilt.append(atom)
                            continue
                        m = _match_segment(atom)
                        if not m:
                            rebuilt.append(atom)
                            continue
                        body = m.group("body").strip()
                        if (body
                                and _boundary_score(body, prev_lf) >= 0.62
                                and not _intermediate_state_change(earlier_bodies, prev_lf, body, split_names)):
                            earlier_bodies.append(body)
                            # Similar wording alone does not prove that the
                            # split segment has no event.  Keep the authored
                            # segment and report the unresolved duplication;
                            # the old generic “承接前半镜终态” text was itself
                            # ambiguous and overwrote later state logic.
                            ctx.record_violation(Violation(
                                constraint_name=self.name,
                                severity=Severity.WARN,
                                shot_index=idx,
                                field="motion_desc",
                                message=(
                                    "拆分后半镜动作与前半镜终态高度相似，但缺少独立事件证据；"
                                    "保留原文并交由 audit 判断，未替换为通用承接句。"
                                ),
                                evidence=atom[:180],
                                repaired=False,
                            ))
                            repairs.append(RepairRecord(
                                constraint_name=self.name,
                                shot_index=idx,
                                action="warn",
                                target="motion_desc",
                                before=atom[:120],
                                after=atom[:120],
                                detail="疑似重复边界状态，保留原文交由 audit",
                            ))
                            rebuilt.append(atom)
                        else:
                            if m:
                                earlier_bodies.append(m.group("body").strip())
                            rebuilt.append(atom)
                    if changed_boundary:
                        b_motion = "；".join(rebuilt)
                        shot["motion_desc"] = b_motion


                # If the split boundary was previously collapsed into a generic
                # continuation segment, the LF still carries the authoritative
                # end state.  When LF differs from the last keyframe, restore
                # that end event into the last motion segment so the keyframe
                # chain can no longer end on the wrong state.
                current_lf = str(shot.get("lf_desc") or "").strip()
                kfs = shot.get("keyframes") or []
                last_kf = kfs[-1] if kfs and isinstance(kfs[-1], dict) else {}
                lf_states = _state_tokens(current_lf)
                kf_states = _state_tokens(str(last_kf.get("desc") or ""))
                if current_lf and lf_states:
                    segments = _split_segments(b_motion)
                    if segments:
                        last_atom = segments[-1]
                        m = _match_segment(last_atom)
                        last_body = m.group("body").strip() if m else ""
                        boundary_ok = (
                            "承接前半镜终态" in b_motion
                            or (
                                last_body
                                and difflib.SequenceMatcher(None, last_body, current_lf).ratio() >= 0.55
                            )
                        )
                        if m and boundary_ok:
                            names = [
                                str(item.get("name") or "")
                                for item in (shot.get("char_layout") or [])
                                if isinstance(item, dict) and item.get("name")
                            ]
                            state_kfs = [
                                k for k in kfs
                                if isinstance(k, dict) and _state_tokens(str(k.get("desc") or ""))
                            ]
                            prior_texts = [str(shot.get("ff_desc") or "")]
                            if state_kfs:
                                prior_texts.append(
                                    str((state_kfs[-2] if len(state_kfs) >= 2 else state_kfs[-1]).get("desc") or "")
                                )
                            target_text = (
                                current_lf
                                if (not last_body or "承接前半镜终态" in last_body)
                                else last_body
                            )
                            evidence_text = (
                                current_lf
                                if "承接前半镜终态" in last_body
                                else last_body
                            )
                            delta, pending, _diff = _state_delta_body(
                                prior_texts,
                                target_text,
                                names,
                                evidence_text,
                            )
                            if pending:
                                ctx.record_violation(Violation(
                                    constraint_name=self.name,
                                    severity=Severity.WARN,
                                    shot_index=idx,
                                    field="motion_desc",
                                    message="状态差分存在未决证据，拒绝自动生成末段动作",
                                    evidence=str(pending[:2]),
                                    repaired=False,
                                ))
                            if not pending and delta and delta != last_body:
                                new_atom = f"{m.group('range')}：{delta}"
                                segments[-1] = new_atom
                                b_motion = "；".join(segments)
                                shot["motion_desc"] = b_motion
                                repairs.append(RepairRecord(
                                    constraint_name=self.name,
                                    shot_index=idx,
                                    action="rewrite",
                                    target="motion_desc",
                                    before=last_atom,
                                    after=new_atom,
                                    detail="通用承接段与 LF 状态冲突，只恢复必要状态变化",
                                ))

                if a_motion and b_motion:
                    ratio = difflib.SequenceMatcher(None, a_motion, b_motion).ratio()
                    if ratio >= 0.95 or a_motion == b_motion:
                        repairs.append(RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx,
                            action="warn",
                            target="motion_desc",
                            before=b_motion[:120],
                            after=b_motion[:120],
                            detail="拆分前后动作文本重复；先报告，再仅在时间段可证明时切分",
                        ))
                        from step01_script_gen.timecode import _split_motion_desc_at_cut
                        total = float(prev.get("duration_sec") or 0) + float(shot.get("duration_sec") or 0)
                        left, right = _split_motion_desc_at_cut(a_motion, float(prev.get("duration_sec") or 0), total)
                        if left or right:
                            prev["motion_desc"] = left or prev.get("motion_desc", "")
                            shot["motion_desc"] = right or shot.get("motion_desc", "")
                            repairs.append(RepairRecord(
                                constraint_name=self.name,
                                shot_index=idx,
                                action="rewrite",
                                target="motion_desc",
                                before=b_motion[:120],
                                after=str(shot["motion_desc"])[:120],
                                detail="拆分后 motion_desc 重复，已按明确切分时间分配动作段",
                            ))
                        else:
                            ctx.record_violation(Violation(
                                constraint_name=self.name,
                                severity=Severity.WARN,
                                shot_index=idx,
                                field="motion_desc",
                                message=(
                                    "前后镜 motion_desc 重复，但无法从原时间段可靠切分；"
                                    "保留原文，未生成“从首帧推进到切分状态”等通用补写。"
                                ),
                                evidence=b_motion[:180],
                                repaired=False,
                            ))
                            repairs.append(RepairRecord(
                                constraint_name=self.name,
                                shot_index=idx,
                                action="warn",
                                target="motion_desc",
                                before=b_motion[:120],
                                after=b_motion[:120],
                                detail="重复 motion 无法可靠切分，保留原文交由 audit",
                            ))
            text = " ".join(filter(None, [
                str(shot.get("prompt") or ""),
                str(shot.get("ff_desc") or ""),
                str(shot.get("lf_desc") or ""),
            ]))
            if "聆听反应" not in text:
                continue
            for item in (shot.get("char_layout") or []):
                name = str(item.get("name") or "") if isinstance(item, dict) else ""
                if not name or not _char_inert_in_text(name, text):
                    continue
                for field in ("prompt", "ff_desc", "lf_desc"):
                    value = str(shot.get(field) or "")
                    if "聆听反应" in value:
                        shot[field] = value.replace(f"聚焦{name}（聆听反应）", f"聚焦{name}（状态反应）")
                        repairs.append(RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx,
                            action="rewrite",
                            target=field,
                            detail=f"{name} 已失能，禁止安排聆听反应",
                        ))
            try:
                from step01_script_gen.validation import _fix_emotion_visualization
                for field in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
                    value = str(shot.get(field) or "")
                    fixed = _fix_emotion_visualization(value)
                    if fixed and fixed != value:
                        shot[field] = fixed
                        repairs.append(RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx,
                            action="rewrite",
                            target=field,
                            detail="拆分续写后补情绪可视化",
                        ))
            except Exception:
                pass
        return repairs


__all__ = ["SplitContinuityConstraint"]
