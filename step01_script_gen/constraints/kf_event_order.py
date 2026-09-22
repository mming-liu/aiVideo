"""Normalize keyframes to one event per explicit motion time segment.

The previous implementation only handled shots with at least two explicit
segments and treated low-confidence text matches as a reason to leave the whole
shot untouched.  That let split shots keep four keyframes for a single event and
let three-segment shots retain only two frames.

This version uses the keyframe's existing time window as structural evidence and
event text only as a tie-breaker.  It then normalizes the final timeline to:

* exactly one frame per explicit motion segment;
* strictly increasing ``t_sec`` values with a minimum gap;
* ``frame_layouts.kfs`` aligned one-for-one with ``keyframes``.

The operation is idempotent: a second run chooses the same frames and writes the
same times.
"""
from __future__ import annotations

import copy
import difflib
import re

from shared.alias_map import CHARACTER_ALIASES
from shared.kf_time import get_kf_t_sec, set_kf_t_sec

from shared import motion_segments as _ms
from .base import Constraint
from .types import RepairRecord, Severity, Violation


_MIN_GAP = 0.08


def _segments(motion: str, total: float) -> list[tuple[float, float, str]]:
    """解析 motion 为 [(start, end, body), ...]（v6.76 起走统一解析器）.

    与全仓同一套正则（分隔符并集、支持小数）；body 由 strip_time_label 剥离，
    与解析出的区间同源——不再有「解析认小数、剥标签不认」的错配。
    """
    out: list[tuple[float, float, str]] = []
    for atom in _ms.atoms(motion or ""):
        rng = _ms.atom_range(atom, total)
        if rng is None:
            continue
        a, b = rng
        body = _ms.strip_time_label(atom) or atom
        out.append((max(0.0, a),
                    min(max(0.0, b), total) if total > 0 else b,
                    body))
    return out

def _event_text(desc: str) -> str:
    text = str(desc or "")
    text = text.split("【角色锚定】", 1)[0]
    text = re.sub(r"（本帧[^）]*）", "", text)
    return text.strip()


def _clean(s: str) -> str:
    text = re.sub(r"[（(][^）)]*[）)]", "", str(s or ""))
    return re.sub(r"[，。；！？、：:（）()【】\s]", "", text)


def _event_overlap(desc: str, body: str) -> float:
    desc = _clean(_event_text(desc))
    body = _clean(body)
    if not desc or not body:
        return 0.0
    ngrams = {body[i:i + 2] for i in range(max(0, len(body) - 1))}
    if not ngrams:
        return 0.0
    return sum(1 for ng in ngrams if ng in desc) / len(ngrams)


def _match_score(desc: str, body: str) -> float:
    return max(
        difflib.SequenceMatcher(
            None, _clean(_event_text(desc)), _clean(body)
        ).ratio(),
        _event_overlap(desc, body),
    )


def _strip_scene_prefix(desc: str, scene_prefix: str) -> str:
    text = str(desc or "")
    if scene_prefix and text.startswith(scene_prefix):
        return text[len(scene_prefix):]
    return text


_BODY_POSE_TOKENS = (
    "盘坐", "踞蹲", "跪坐", "站立", "站", "立", "仰卧", "躺",
    "俯身", "逼近", "举灯", "持刀", "持匕首", "持剑", "提壶", "斟酒",
    "环住", "跃起", "跃上", "落在", "倒纵", "入水", "坐",
)
_HEAD_POSE_TOKENS = ("抬头", "低头", "仰面", "侧头", "转头", "回望")
_POSE_TOKENS = _BODY_POSE_TOKENS + _HEAD_POSE_TOKENS
_HOLD_WORDS = ("握着", "握住", "握刀", "手持", "持刀", "持匕首", "紧握", "扬起寒光")
_RELEASE_WORDS = ("丢", "脱手", "滑落", "掉落", "坠入", "放下", "松开")
_CHANGE_WORDS = ("丢", "脱手", "滑落", "掉落", "坠入", "放下", "松开",
                 "跃起", "跃上", "落在", "跳上", "倒下", "倒地", "起身", "坐起", "躺下")


def _name_variants(name: str) -> set[str]:
    name = str(name or "").strip()
    if not name:
        return set()
    canonical = CHARACTER_ALIASES.get(name, name)
    out = {name, canonical}
    for alias, target in CHARACTER_ALIASES.items():
        if target == canonical:
            out.add(alias)
    return {x for x in out if x}


def _clause_subject(clause: str, item_names: list[str]) -> tuple[str, int]:
    candidates = []
    for name in item_names:
        for variant in _name_variants(name):
            pos = clause.find(variant)
            if pos >= 0:
                candidates.append((pos, name))
    if not candidates:
        return "", -1
    pos, name = min(candidates, key=lambda x: x[0])
    return name, pos


def _owners_for_prop(prop: str, seed_props: dict[str, list]) -> list[str]:
    prop = str(prop or "")
    if not prop:
        return []
    return [
        name for name, props in seed_props.items()
        if any(any(ch in str(p) for ch in prop) for p in props)
    ]


def _state_owner(desc: str, prop: str, item_names: list[str],
                 owners_with_prop: list[str], state_words: tuple[str, ...]) -> str | None:
    prop = str(prop or "")
    if not prop or not state_words:
        return None
    last_subject = ""
    for clause, _delimiter in _split_clauses_for_state(desc):
        clause_subject, _pos = _clause_subject(clause, item_names)
        if clause_subject:
            last_subject = clause_subject
        if not any(ch in clause for ch in prop):
            continue
        if not any(w in clause for w in state_words):
            continue
        if clause_subject:
            return clause_subject
        if last_subject:
            return last_subject
        if len(owners_with_prop) == 1:
            return owners_with_prop[0]
        return None
    return None


def _prop_released_for(desc: str, prop: str, subject: str,
                       item_names: list[str], owners_with_prop: list[str]) -> bool:
    return _state_owner(desc, prop, item_names, owners_with_prop, _RELEASE_WORDS) == subject


def _prop_held_for(desc: str, prop: str, subject: str,
                   item_names: list[str], owners_with_prop: list[str]) -> bool:
    return _state_owner(desc, prop, item_names, owners_with_prop, _HOLD_WORDS) == subject


def _prop_mentioned(desc: str, prop: str) -> bool:
    return any(ch in desc for ch in str(prop or ""))


def _pose_assignments(desc: str, item_names: list[str], default_subject: str = "") -> dict[str, list[str]]:
    """Assign pose tokens only to the clause actor, never the action object."""
    assignments: dict[str, list[str]] = {}
    last_subject = default_subject
    for clause, _delimiter in _split_clauses_for_state(desc):
        subject, subject_pos = _clause_subject(clause, item_names)
        if subject:
            last_subject = subject
        found = []
        for token in _POSE_TOKENS:
            pos = clause.find(token)
            if pos >= 0:
                found.append((pos, token))
        if not found:
            continue
        found.sort()
        first_token_pos = found[0][0]
        actor = subject if subject else last_subject
        if not actor:
            continue
        if subject and subject_pos > first_token_pos:
            # The named character follows the action: object, not actor.
            continue
        assignments[actor] = [token for _pos, token in found]
        last_subject = actor
    return assignments


def _merge_pose(current: str, updates: list[str]) -> str:
    current = str(current or "")
    body = next((t for t in _BODY_POSE_TOKENS if t in current), "")
    head = next((t for t in _HEAD_POSE_TOKENS if t in current), "")
    for token in updates:
        if token in _HEAD_POSE_TOKENS:
            head = token
        else:
            body = token
    return body + head


def _normalize_chosen_layouts(chosen, ff_layout, ff_text: str = "") -> list:
    """State machine: keep confirmed state, update explicit events, leave ambiguity."""
    seed = {
        str(item.get("name") or ""): item
        for item in (ff_layout or [])
        if isinstance(item, dict)
    }
    item_names = list(seed.keys())
    seed_props = {
        name: list(item.get("props") or [])
        for name, item in seed.items()
    }
    released: set[tuple[str, str]] = set()
    active_props: dict[str, list] = {}
    for name, props in seed_props.items():
        kept = []
        for p in props:
            owners = _owners_for_prop(str(p), seed_props)
            if _prop_released_for(ff_text, str(p), name, item_names, owners):
                released.add((name, str(p)))
            else:
                kept.append(p)
        active_props[name] = kept
    active_pose: dict[str, str] = {
        name: str(item.get("pose") or "")
        for name, item in seed.items()
    }
    out = []
    for seg_i, kf, spec, created in chosen:
        desc = str(kf.get("desc") or "")
        new_spec = copy.deepcopy(spec) if isinstance(spec, list) and spec else _neutral_layout(ff_layout)
        item_names = [
            str(item.get("name") or "")
            for item in new_spec
            if isinstance(item, dict) and item.get("name")
        ]
        first_on_screen = next(
            (str(item.get("name") or "") for item in new_spec
             if isinstance(item, dict) and item.get("on_screen") is not False),
            "",
        )
        assignments = _pose_assignments(desc, item_names, first_on_screen)
        for item in new_spec:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "")
            if item.get("on_screen") is False:
                item["pose"] = ""
                item["gaze_target"] = "无"
                item["props"] = []
                continue
            props = list(dict.fromkeys(
                list(active_props.get(name) or [])
                + list(item.get("props") or [])
            ))
            for p in seed_props.get(name, []) or []:
                p_text = str(p)
                owners = _owners_for_prop(p_text, seed_props)
                if _prop_held_for(desc, p_text, name, item_names, owners):
                    if p not in props:
                        props.append(p)
                    released.discard((name, p_text))
            kept_props = []
            for p in props:
                p_text = str(p)
                owners = _owners_for_prop(p_text, seed_props)
                if _prop_released_for(desc, p_text, name, item_names, owners):
                    released.add((name, p_text))
                    continue
                if _prop_held_for(desc, p_text, name, item_names, owners):
                    released.discard((name, p_text))
                if (name, p_text) in released:
                    continue
                kept_props.append(p)
            props = kept_props
            pose = active_pose.get(name) or str(item.get("pose") or "")
            if name in assignments:
                pose = _merge_pose(pose, assignments[name])
            item["pose"] = pose
            item["props"] = props
            active_props[name] = props
            active_pose[name] = pose
        out.append((seg_i, kf, new_spec, created))
    return out

def _split_clauses_for_state(text: str) -> list[tuple[str, str]]:
    import re as _re
    parts = _re.split(r"(，|。|；|、|……)", str(text or ""))
    out = []
    for i in range(0, len(parts), 2):
        clause = parts[i].strip()
        delimiter = parts[i + 1] if i + 1 < len(parts) else ""
        if clause:
            out.append((clause, delimiter))
    return out


def _neutral_layout(spec) -> list:
    """Keep identity/placement, clear state copied from another frame."""
    if not isinstance(spec, list):
        return []
    out = []
    for item in spec:
        if not isinstance(item, dict):
            continue
        out.append({
            "name": item.get("name", ""),
            "on_screen": item.get("on_screen", True),
            "position": item.get("position", ""),
            "pose": "",
            "gaze_target": "无",
            "props": [],
        })
    return out


def _has_pose_contradiction(text: str) -> bool:
    text = _event_text(text)
    if any(w in text for w in ("从", "转为", "随后", "继而", "起身", "坐起", "躺下", "倒下", "翻倒")):
        return False
    sitting = any(w in text for w in ("坐", "跪", "盘膝", "踞蹲", "蹲"))
    lying = any(w in text for w in ("仰卧", "躺", "卧倒", "倒地", "倒伏", "平躺"))
    return sitting and lying


def _stage_for(seg_i: int, n_segments: int) -> str:
    if n_segments <= 1:
        return "after"
    if seg_i == 0:
        return "pre"
    if seg_i == n_segments - 1:
        return "after"
    return ("approach", "climax", "react", "exit")[
        min(seg_i - 1, 3)
    ]


def _in_window(t: float | None, a: float, b: float) -> bool:
    return t is not None and a - 0.01 <= t <= b + 0.01


def _closeness(t: float | None, a: float, b: float) -> float:
    if t is None:
        return 0.0
    length = max(b - a, 0.1)
    return max(0.0, 1.0 - abs(t - (a + b) / 2.0) / length)


class KfEventOrderConstraint(Constraint):
    name = "kf_event_order"
    severity = Severity.WARN

    def apply(self, ctx) -> list[RepairRecord]:
        """Audit motion/keyframe alignment without rewriting frame facts.

        ``motion_desc`` is not a complete frame source: it often omits poses,
        props, blood state, gaze, and action results.  Rewriting or deleting a
        keyframe from it loses information and fabricates semantics.  This
        constraint therefore reports conflicts only; semantic repair is left
        to the LLM repair round.
        """
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            motion = str(shot.get("motion_desc") or "")
            kfs = shot.get("keyframes") or []
            if not motion or not isinstance(kfs, list) or not kfs:
                continue
            try:
                total = float(shot.get("duration_sec") or shot.get("duration") or 0)
            except (TypeError, ValueError):
                total = 0.0
            segments = _segments(motion, total)
            if not segments:
                continue

            shot_index = None if is_hook else shot.get("index", idx)
            if len(kfs) != len(segments):
                ctx.record_violation(Violation(
                    constraint_name=self.name,
                    severity=Severity.HARD,
                    shot_index=shot_index,
                    field="keyframes",
                    message=(
                        f"motion 明确分为 {len(segments)} 段，但 keyframes 有 "
                        f"{len(kfs)} 帧；仅报告冲突，不由 motion 删除/补建关键帧"
                    ),
                    evidence=motion[:240],
                    repaired=False,
                ))

            layouts = shot.get("frame_layouts")
            old_layouts = list(layouts.get("kfs") or []) if isinstance(layouts, dict) else []
            if isinstance(layouts, dict) and len(old_layouts) != len(kfs):
                ctx.record_violation(Violation(
                    constraint_name=self.name,
                    severity=Severity.HARD,
                    shot_index=shot_index,
                    field="frame_layouts.kfs",
                    message=(
                        f"frame_layouts.kfs 有 {len(old_layouts)} 项，与 keyframes "
                        f"{len(kfs)} 帧不对齐；保留原 spec 并交 regen"
                    ),
                    repaired=False,
                ))

            assigned: dict[int, int] = {}
            for kf_i, kf in enumerate(kfs):
                if not isinstance(kf, dict):
                    continue
                desc = str(kf.get("desc") or "")
                t_sec = get_kf_t_sec(kf, shot)
                best_seg = 0
                best_score = -1.0
                best_match = 0.0
                best_in_window = False
                for seg_i, (a, b, segment_body) in enumerate(segments):
                    match = _match_score(desc, segment_body)
                    in_window = _in_window(t_sec, a, b)
                    score = match + (0.35 if in_window else 0.0)
                    if score > best_score:
                        best_seg = seg_i
                        best_score = score
                        best_match = match
                        best_in_window = in_window

                if t_sec is not None and not best_in_window:
                    ctx.record_violation(Violation(
                        constraint_name=self.name,
                        severity=Severity.HARD,
                        shot_index=shot_index,
                        field=f"keyframes[{kf_i}].t_sec",
                        message=(
                            f"关键帧时间 {t_sec:g}s 不在任何 motion 时段内；"
                            "保留原时间并交 LLM regen，禁止自动挪动"
                        ),
                        evidence=desc[:180],
                        repaired=False,
                    ))
                elif best_match < 0.12:
                    ctx.record_violation(Violation(
                        constraint_name=self.name,
                        severity=Severity.HARD,
                        shot_index=shot_index,
                        field=f"keyframes[{kf_i}].desc",
                        message=(
                            "关键帧描述与 motion 时段缺少明确对应证据；"
                            "保留原帧并交 LLM regen，禁止用 motion 重写 desc"
                        ),
                        evidence=desc[:180],
                        repaired=False,
                    ))

                if best_in_window or best_match >= 0.12:
                    if best_seg in assigned:
                        ctx.record_violation(Violation(
                            constraint_name=self.name,
                            severity=Severity.HARD,
                            shot_index=shot_index,
                            field="keyframes",
                            message=(
                                f"关键帧 {assigned[best_seg]} 与 {kf_i} 都指向 motion "
                                f"时段 {best_seg}；仅报告重复/塌缩，不自动删除"
                            ),
                            repaired=False,
                        ))
                    else:
                        assigned[best_seg] = kf_i

            for seg_i, (a, b, segment_body) in enumerate(segments):
                if seg_i not in assigned:
                    ctx.record_violation(Violation(
                        constraint_name=self.name,
                        severity=Severity.HARD,
                        shot_index=shot_index,
                        field="keyframes",
                        message=(
                            f"motion 时段 {a:g}-{b:g}s「{segment_body[:50]}」"
                            "缺少可证明对应的关键帧；不得由 motion 自动补建，交 regen"
                        ),
                        repaired=False,
                    ))

        return []


__all__ = ["KfEventOrderConstraint"]
