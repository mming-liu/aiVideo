"""Shared, evidence-driven character state updates for Step01."""
from __future__ import annotations

import re

from shared.alias_map import CHARACTER_ALIASES


BODY_VALUES = (
    "跪坐", "站立", "俯身", "仰卧", "躺", "坐", "直起",
    "跃起", "跃上", "落在", "倒纵", "入水",
)
HEAD_VALUES = ("抬头", "低头", "仰面")
EXPRESSION_VALUES = ("啜泣", "哭泣", "大笑", "冷笑", "闭眼", "睁眼")
_BODY_ACTIONS = {
    "躺下": "仰卧", "倒下": "仰卧", "起身": "站立", "站起": "站立",
    "坐下": "坐", "跪下": "跪坐", "跃上": "跃上", "落在": "落在",
    "倒纵": "倒纵", "入水": "入水",
}
_NEGATIONS = ("没有", "未", "不再", "并未", "不")
_FIELD_VALUES = {
    "body_pose": BODY_VALUES,
    "head_direction": HEAD_VALUES,
    "expression": EXPRESSION_VALUES,
}


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


def _split_clauses(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"[，。；、……]", text or "") if x.strip()]


def _all_name_positions(clause: str, names: list[str]) -> list[tuple[int, str]]:
    found = []
    for name in names:
        for variant in _name_variants(name):
            for match in re.finditer(re.escape(variant), clause):
                found.append((match.start(), name))
    return found


def _occurrences(clause: str, values: tuple[str, ...]) -> list[tuple[int, int, str]]:
    out = []
    for value in values:
        for match in re.finditer(re.escape(value), clause):
            out.append((match.start(), match.end(), value))
    return out


def _dedupe_overlaps(events: list[dict]) -> list[dict]:
    out = []
    for event in sorted(events, key=lambda e: (e["start"], -(e["end"] - e["start"]), not e["is_action"])):
        covered = False
        for kept in out:
            if kept["field"] != event["field"]:
                continue
            if kept["start"] <= event["start"] and event["end"] <= kept["end"]:
                covered = True
                break
        if not covered:
            out.append(event)
    return out


def _actor_for_position(clause: str, names: list[str], pos: int,
                        last_subject: str) -> str:
    positions = _all_name_positions(clause, names)
    before = [item for item in positions if item[0] < pos]
    if before:
        return max(before, key=lambda item: item[0])[1]
    if positions:
        return min(positions, key=lambda item: item[0])[1]
    return last_subject


def _is_negated(clause: str, pos: int) -> bool:
    prefix = clause[max(0, pos - 4):pos]
    return any(word in prefix for word in _NEGATIONS)


def extract_events_with_status(text: str, names: list[str]) -> tuple[list[dict], list[dict]]:
    """Extract all field events in textual order and unresolved evidence."""
    events: list[dict] = []
    unresolved: list[dict] = []
    last_subject = ""
    for clause in _split_clauses(text):
        clause_events: list[dict] = []
        for field, values in _FIELD_VALUES.items():
            for start, end, value in _occurrences(clause, values):
                is_action = field in ("head_direction", "expression")
                clause_events.append({
                    "field": field, "value": value, "start": start, "end": end,
                    "is_action": is_action,
                })
        for action_word, target_value in _BODY_ACTIONS.items():
            for start, end, _word in _occurrences(clause, (action_word,)):
                clause_events.append({
                    "field": "body_pose", "value": target_value,
                    "start": start, "end": end, "is_action": True,
                })
        clause_events = _dedupe_overlaps(clause_events)
        if clause_events:
            for event in clause_events:
                actor = _actor_for_position(clause, names, event["start"], last_subject)
                if not actor:
                    unresolved.append({"text": clause, "reason": "subject_unknown", "field": event["field"]})
                    continue
                last_subject = actor
                if _is_negated(clause, event["start"]):
                    unresolved.append({
                        "text": clause, "reason": "negated_state",
                        "field": event["field"], "name": actor,
                    })
                    continue
                events.append({
                    "name": actor,
                    "field": event["field"],
                    "value": event["value"],
                    "text": clause,
                    "is_action": event["is_action"],
                })
            continue
        positions = _all_name_positions(clause, names)
        if positions:
            last_subject = max(positions, key=lambda item: item[0])[1]
    return events, unresolved


def extract_states_with_status(text: str, names: list[str]) -> tuple[dict[str, dict[str, str]], list[dict]]:
    events, unresolved = extract_events_with_status(text, names)
    states: dict[str, dict[str, str]] = {}
    for event in events:
        states.setdefault(event["name"], {})[event["field"]] = event["value"]
    return states, unresolved


def extract_states(text: str, names: list[str]) -> dict[str, dict[str, str]]:
    return extract_states_with_status(text, names)[0]


def merge_states(base: dict[str, dict[str, str]],
                 update: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    out = {name: dict(fields) for name, fields in (base or {}).items()}
    for name, fields in (update or {}).items():
        out.setdefault(name, {}).update(fields)
    return out


def diff_states(current: dict[str, dict[str, str]],
                target: dict[str, dict[str, str]]) -> dict[str, dict[str, tuple[str, str]]]:
    diff: dict[str, dict[str, tuple[str, str]]] = {}
    for name, target_fields in (target or {}).items():
        current_fields = (current or {}).get(name, {})
        for field, new_value in target_fields.items():
            old_value = current_fields.get(field, "")
            if old_value != new_value:
                diff.setdefault(name, {})[field] = (old_value, new_value)
    return diff


def render_delta(delta: dict[str, dict[str, tuple[str, str]]],
                 evidence_text: str = "") -> str:
    """Render only changes backed by a same-role, same-field positive event."""
    evidence_events, _unresolved = extract_events_with_status(evidence_text, list(delta))
    phrases = []
    for name, fields in (delta or {}).items():
        for field, (_old, new_value) in fields.items():
            supported = any(
                event["name"] == name
                and event["field"] == field
                and event["value"] == new_value
                and (event.get("is_action") or field != "body_pose")
                for event in evidence_events
            )
            if not supported:
                continue
            if field == "body_pose":
                phrase = f"{name}{new_value}"
            elif field == "head_direction":
                phrase = f"{name}{new_value}"
            elif field == "expression":
                phrase = f"{name}{new_value}"
            else:
                continue
            if phrase not in phrases:
                phrases.append(phrase)
    return "，".join(phrases)


def has_role_state_regression(earlier_states: list[dict[str, dict[str, str]]],
                              previous: dict[str, dict[str, str]],
                              candidate: dict[str, dict[str, str]]) -> bool:
    """True only for the same role/field chain A -> B -> A."""
    for name, fields in (candidate or {}).items():
        for field, candidate_value in fields.items():
            previous_value = previous.get(name, {}).get(field)
            if not previous_value or candidate_value != previous_value:
                continue
            for earlier in earlier_states:
                middle_value = earlier.get(name, {}).get(field)
                if middle_value and middle_value != previous_value:
                    return True
    return False


__all__ = [
    "extract_events_with_status",
    "extract_states",
    "extract_states_with_status",
    "merge_states",
    "diff_states",
    "render_delta",
    "has_role_state_regression",
]
