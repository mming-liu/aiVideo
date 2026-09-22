"""Resolve same-frame weapon contradictions by subject + weapon + time."""
from __future__ import annotations

import re

from shared.alias_map import CHARACTER_ALIASES
from shared.content_safety import WEAPON_WORDS
from shared.kf_time import get_kf_t_sec
from shared import motion_segments as _ms
from .base import Constraint
from .types import RepairRecord, Severity, Violation


_HOLD = ("握着", "手持", "持刀", "持匕首", "紧握", "右手持", "左手持", "扬起寒光")
_RELEASE = ("已脱手", "脱手", "滑落", "掉落", "坠入", "没入", "沉入")


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


def _known_names(script: dict, shot: dict) -> list[str]:
    names: list[str] = []
    for item in shot.get("char_layout") or []:
        if isinstance(item, dict):
            for variant in _name_variants(item.get("name", "")):
                if variant not in names:
                    names.append(variant)
    layouts = shot.get("frame_layouts")
    if isinstance(layouts, dict):
        for key in ("ff", "lf"):
            for item in layouts.get(key) or []:
                if isinstance(item, dict):
                    for variant in _name_variants(item.get("name", "")):
                        if variant not in names:
                            names.append(variant)
    for item in script.get("characters") or []:
        if isinstance(item, dict):
            for variant in _name_variants(item.get("identifier") or item.get("name") or ""):
                if variant not in names:
                    names.append(variant)
    return sorted(names, key=len, reverse=True)


def _split_clauses(text: str) -> list[tuple[str, str]]:
    parts = re.split(r"(，|。|；|、|……)", str(text or ""))
    out: list[tuple[str, str]] = []
    for i in range(0, len(parts), 2):
        clause = parts[i].strip()
        delimiter = parts[i + 1] if i + 1 < len(parts) else ""
        if clause:
            out.append((clause, delimiter))
    return out


def _weapon_in(clause: str) -> str:
    return next((w for w in WEAPON_WORDS if w in clause), "")


def _state_in(clause: str) -> str | None:
    if any(w in clause for w in _HOLD):
        return "hold"
    if any(w in clause for w in _RELEASE):
        return "release"
    return None


def _subject_in(clause: str, names: list[str]) -> str:
    for name in names:
        if name in clause:
            return name
    return ""


def _identity_match(a: str, b: str) -> bool:
    return bool(_name_variants(a) & _name_variants(b))


def _release_owner(text: str, prop: str, names: list[str], owners: list[str]) -> str:
    last_subject = ""
    for clause, _delimiter in _split_clauses(text):
        named_subject = _subject_in(clause, names)
        if named_subject:
            last_subject = named_subject
        if not any(ch in clause for ch in str(prop or "")):
            continue
        if _state_in(clause) != "release":
            continue
        if named_subject:
            return named_subject
        if last_subject:
            return last_subject
        return owners[0] if len(owners) == 1 else ""
    return ""


def _motion_segments(motion: str) -> list[tuple[float, float, str]]:
    """把 motion 切成 [(start, end, body), ...]（v6.77 走统一解析器）.

    解析改用 `shared.motion_segments`（分隔符并集、支持小数）。
    **保留本处特有语义**：无时间标签的原子段给 `(0.0, inf, body)`——表示
    「该状态适用于整镜」，查某时刻所属状态时不会漏。这一点与
    `kf_event_order._segments`（无标签段跳过）不同，属调用方语义，不下沉。
    """
    out: list[tuple[float, float, str]] = []
    for atom in _ms.atoms(motion):
        rng = _ms.atom_range(atom, 0.0)
        if rng is not None:
            a, b = rng
            out.append((a, b, _ms.strip_time_label(atom)))
        else:
            out.append((0.0, float("inf"), atom))
    return out


def _motion_state_at(motion: str, subject: str, weapon: str, names: list[str],
                     t_sec: float | None) -> str | None:
    states: list[tuple[float, float, str]] = []
    last_subject = ""
    for a, b, body in _motion_segments(motion):
        for clause, _delimiter in _split_clauses(body):
            named_subject = _subject_in(clause, names)
            current_subject = named_subject or last_subject
            if named_subject:
                last_subject = named_subject
            if current_subject != subject:
                continue
            clause_weapon = _weapon_in(clause)
            if clause_weapon and clause_weapon != weapon:
                continue
            state = _state_in(clause)
            if state:
                states.append((a, b, state))

    if not states:
        return None
    if t_sec is None:
        unique = {s for _a, _b, s in states}
        return next(iter(unique)) if len(unique) == 1 else None

    containing = [s for s in states if s[0] - 0.01 <= t_sec <= s[1] + 0.01]
    if containing:
        # At an exact segment boundary the later segment is the new state.
        return max(containing, key=lambda x: x[0])[2]
    preceding = [s for s in states if s[1] <= t_sec + 0.01]
    if preceding:
        return max(preceding, key=lambda x: x[1])[2]
    # No state exists at or before this moment.  A future release must not be
    # projected backward onto the first frame.
    return None


def _repair_text(text: str, motion: str, names: list[str], *, time_sec: float | None,
                 label: str):
    clauses = _split_clauses(text)
    if not clauses:
        return text, []

    entries: list[dict] = []
    for i, (clause, _delimiter) in enumerate(clauses):
        weapon = _weapon_in(clause)
        if not weapon:
            continue
        named_subject = _subject_in(clause, names)
        state = _state_in(clause)
        if state and named_subject:
            entries.append({
                "i": i,
                "weapon": weapon,
                "subject": named_subject,
                "state": state,
            })

    conflicts: dict[tuple[str, str], dict[str, list[int]]] = {}
    for entry in entries:
        if entry["subject"]:
            conflicts.setdefault((entry["subject"], entry["weapon"]), {}).setdefault(
                entry["state"], []
            ).append(entry["i"])

    violations: list[tuple[str, str, str]] = []
    for (subject, weapon), states in conflicts.items():
        if "hold" in states and "release" in states:
            violations.append((subject, weapon, label))
    return text, violations


class WeaponStateConsistencyConstraint(Constraint):
    name = "weapon_state_consistency"
    severity = Severity.HARD
    fields = ()

    def apply(self, ctx) -> list[RepairRecord]:
        """Report contradictions without deleting prose or layout props.

        Subject omission, repeated prop nouns, and unsegmented motion text make
        deterministic deletion unsafe.  This constraint therefore only emits
        explicit same-subject/same-weapon conflicts; the LLM repair round owns
        any semantic rewrite.
        """
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            names = _known_names(ctx.script, shot)
            motion = str(shot.get("motion_desc") or "")
            shot_index = None if is_hook else shot.get("index", idx)
            try:
                duration = float(shot.get("duration_sec") or shot.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0.0

            field_times = {
                "prompt": None,
                "ff_desc": 0.0,
                "lf_desc": duration if duration > 0 else None,
            }
            for field, time_sec in field_times.items():
                text = str(shot.get(field) or "")
                if not text:
                    continue
                _new_text, violations = _repair_text(
                    text, motion, names, time_sec=time_sec, label=field
                )
                for subject, weapon, label in violations:
                    ctx.record_violation(Violation(
                        constraint_name=self.name,
                        severity=Severity.HARD,
                        shot_index=shot_index,
                        field=label,
                        message=(
                            f"{subject} 的武器 {weapon} 在该帧时间出现持有/释放"
                            "互斥描述；仅报告冲突，禁止按最近主体推断后删除分句"
                        ),
                        evidence=text[-240:],
                        repaired=False,
                    ))

            kfs = shot.get("keyframes") or []
            for kf_i, kf in enumerate(kfs):
                if not isinstance(kf, dict):
                    continue
                text = str(kf.get("desc") or "")
                if not text:
                    continue
                _new_text, violations = _repair_text(
                    text, motion, names,
                    time_sec=get_kf_t_sec(kf, shot),
                    label=f"keyframes[{kf_i}].desc",
                )
                for subject, weapon, label in violations:
                    ctx.record_violation(Violation(
                        constraint_name=self.name,
                        severity=Severity.HARD,
                        shot_index=shot_index,
                        field=label,
                        message=(
                            f"{subject} 的武器 {weapon} 在该关键帧出现持有/释放"
                            "互斥描述；仅报告冲突，禁止按最近主体推断后删除分句"
                        ),
                        evidence=text[-240:],
                        repaired=False,
                    ))

            layouts = shot.get("frame_layouts")
            if isinstance(layouts, dict):
                frame_specs = [("ff_desc", "ff"), ("lf_desc", "lf")]
                for kf_i, kf in enumerate(kfs):
                    if isinstance(kf, dict):
                        frame_specs.append((f"keyframes[{kf_i}].desc", "kf", kf_i))
                for item_spec in frame_specs:
                    field = item_spec[0]
                    text = str(shot.get(field) if field.endswith("_desc") else (
                        kfs[item_spec[2]].get("desc")
                        if len(item_spec) > 2 and isinstance(kfs[item_spec[2]], dict)
                        else ""
                    ) or "")
                    if not text:
                        continue
                    spec = (
                        layouts.get(item_spec[1])
                        if len(item_spec) == 2
                        else (layouts.get("kfs") or [None])[item_spec[2]]
                    )
                    if not isinstance(spec, list):
                        continue
                    for item in spec:
                        if not isinstance(item, dict):
                            continue
                        props = list(item.get("props") or [])
                        for prop in props:
                            owners = [
                                str(other.get("name") or "")
                                for other in spec
                                if isinstance(other, dict)
                                and any(
                                    str(p) in str(prop) or str(prop) in str(p)
                                    for p in (other.get("props") or [])
                                )
                            ]
                            owner = _release_owner(text, str(prop), names, owners)
                            if owner and _identity_match(owner, str(item.get("name") or "")):
                                ctx.record_violation(Violation(
                                    constraint_name=self.name,
                                    severity=Severity.HARD,
                                    shot_index=shot_index,
                                    field=f"frame_layouts.{item_spec[1]}.props",
                                    message=(
                                        f"{owner} 的 {prop} 正文出现释放描述，"
                                        "layout 仍列出该道具；保留 layout 并交 regen"
                                    ),
                                    evidence=text[-240:],
                                    repaired=False,
                                ))

        return repairs


__all__ = ["WeaponStateConsistencyConstraint"]
