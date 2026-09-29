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
import re

from shared.alias_map import CHARACTER_ALIASES
from shared.kf_time import get_kf_t_sec, set_kf_t_sec

# 关键帧 ↔ 时段的**唯一判据**（2026-09-27 用户裁决后抽出，见该模块文档）。
# `_segments` / `_match_score` 保持原名可导入：`quality_issues` 既有 import 依赖它们。
from step01_script_gen.kf_coverage import (
    analyze as _analyze_kf_coverage,
    clean as _clean,
    closeness as _closeness,
    event_overlap as _event_overlap,
    event_text as _event_text,
    in_window as _in_window,
    match_score as _match_score,
    segments as _segments,
)

from .base import Constraint
from .types import RepairRecord, Severity, Violation


_MIN_GAP = 0.08


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


def _best_score(slot) -> float:
    """该帧对所有时段的最高文案相似度（无评分 → 0.0）."""
    if slot.best is None or not slot.scores:
        return 0.0
    return slot.scores[slot.best]


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
            _cov = _analyze_kf_coverage(shot)
            segments = _cov.segments
            if not segments:
                continue
            # 2026-09-27（用户裁决）：帧数口径是 **≥ 时段数**，不是「恰好相等」。
            # 额外帧只要有叙事价值就允许（CLAUDE.md v6.33 的源头规则；
            # `validation._check_temporal_plan` 的 docstring 也早已写明这一条）。
            # 只有**少于**段数才是缺陷。

            shot_index = None if is_hook else shot.get("index", idx)
            if _cov.shortfall:
                ctx.record_violation(Violation(
                    constraint_name=self.name,
                    severity=Severity.HARD,
                    shot_index=shot_index,
                    field="keyframes",
                    message=(
                        f"motion 有 {len(_cov.required)} 个时段需要独立关键帧，但 "
                        f"keyframes 只有 {len(kfs)} 帧（帧数须 ≥ 段数；额外叙事帧允许）；"
                        "仅报告冲突，不由 motion 删除/补建关键帧"
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

            # 一帧只归属一个明确事件（边界帧不得同时充当相邻两段的证据）：
            # 归属、重复、越窗、内容错位、缺段全部由 `kf_coverage.analyze` 判定。
            for slot in _cov.slots:
                _t = slot.t_sec
                if slot.out_of_window:
                    ctx.record_violation(Violation(
                        constraint_name=self.name,
                        severity=Severity.HARD,
                        shot_index=shot_index,
                        field=f"keyframes[{slot.index}].t_sec",
                        message=(
                            f"关键帧时间 {_t:g}s 不在任何 motion 时段内；"
                            "保留原时间并交 LLM regen，禁止自动挪动"
                        ),
                        evidence=slot.desc[:180],
                        repaired=False,
                    ))
                elif _best_score(slot) < 0.12:
                    ctx.record_violation(Violation(
                        constraint_name=self.name,
                        severity=Severity.HARD,
                        shot_index=shot_index,
                        field=f"keyframes[{slot.index}].desc",
                        message=(
                            "关键帧描述与 motion 时段缺少明确对应证据；"
                            "保留原帧并交 LLM regen，禁止用 motion 重写 desc"
                        ),
                        evidence=slot.desc[:180],
                        repaired=False,
                    ))
                elif slot.mismatched:
                    # **时间**与**内容**分开报：旧实现把「最佳匹配段不在窗口内」
                    # 报成「时间不在任何 motion 时段内」——文案说的是时间问题，
                    # 实际是内容写错段（冻结稿 shot 8 kf@2.5：写的是 1-2s 的
                    # 「反手一掌」，t 却落在 2-4s）。判据阈值与质量门的
                    # `kf_event_window_mismatch` 逐字相同，不在这里顺带改严重度。
                    _own, _bst = slot.own, slot.best
                    ctx.record_violation(Violation(
                        constraint_name=self.name,
                        severity=Severity.HARD,
                        shot_index=shot_index,
                        field=f"keyframes[{slot.index}].desc",
                        message=(
                            f"关键帧描述更像 {_cov.segments[_bst][0]:g}-"
                            f"{_cov.segments[_bst][1]:g}s 的事件"
                            f"「{str(_cov.segments[_bst][2])[:24]}」，但它位于 "
                            f"{_cov.segments[_own][0]:g}-{_cov.segments[_own][1]:g}s"
                            f"（「{str(_cov.segments[_own][2])[:24]}」）；"
                            "保留原帧并交 LLM regen，禁止用 motion 重写 desc"
                        ),
                        evidence=slot.desc[:180],
                        repaired=False,
                    ))

                if (slot.duplicate_of is not None
                        and (slot.best == slot.own
                             or _best_score(slot) >= 0.12)):
                    ctx.record_violation(Violation(
                        constraint_name=self.name,
                        severity=Severity.HARD,
                        shot_index=shot_index,
                        field="keyframes",
                        message=(
                            f"关键帧 {slot.duplicate_of} 与 {slot.index} 都指向 motion "
                            f"时段 {slot.own}；仅报告重复/塌缩，不自动删除"
                        ),
                        repaired=False,
                    ))

            for seg_i in _cov.missing:
                a, b, segment_body = _cov.segments[seg_i]
                ctx.record_violation(Violation(
                    constraint_name=self.name,
                    severity=Severity.HARD,
                    shot_index=shot_index,
                    field="keyframes",
                    message=(
                        f"motion 时段 {a:g}-{b:g}s「{str(segment_body)[:50]}」"
                        "缺少可证明对应的关键帧；不得由 motion 自动补建，交 regen"
                    ),
                    repaired=False,
                ))

        return []


__all__ = ["KfEventOrderConstraint"]
