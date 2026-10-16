"""Conservative, shared evidence for explicit weapon possession conflicts."""
from __future__ import annotations

import re

from shared.content_safety import WEAPON_WORDS
from .subject_attribution import leading_named_span

HOLD_MARKERS = ("握着", "手持", "持刀", "持匕首", "紧握", "右手持", "左手持")
# Penetrating a target does not establish that the wielder released the weapon.
RELEASE_MARKERS = ("已脱手", "脱手", "滑落", "掉落", "坠入", "沉入")


def asserted_marker(text: str, markers: tuple[str, ...]) -> bool:
    if re.search(r"如果|倘若|一旦|若是|即将|将要|准备|可能|似乎", text):
        return False
    for marker in markers:
        for match in re.finditer(re.escape(marker), text):
            prefix = text[:match.start()]
            if not re.search(r"(?:不|未|没|没有|不曾|并未|尚未)(?:曾经|已经|已|曾)?$", prefix):
                return True
    return False


def prop_mentioned(text: str, prop: str) -> bool:
    """Whole prop identity, or its explicit weapon type; never one shared glyph."""
    prop = str(prop or '').strip()
    return bool(prop) and (prop in text or any(
        weapon in prop and weapon in text for weapon in WEAPON_WORDS))


def prop_state_owner(text: str, prop: str, names: list[str], owners: list[str],
                     markers: tuple[str, ...]) -> str:
    last_subject = ''
    for clause in re.split(r"[，。；、！？\n]", str(text or '')):
        subject, _ = leading_named_span(clause, names)
        if subject:
            last_subject = subject
        if not prop_mentioned(clause, prop) or not asserted_marker(clause, markers):
            continue
        if subject:
            return subject
        if any(name and name in clause for name in names):
            return ''  # A named recipient cannot establish who released it.
        return last_subject or (owners[0] if len(owners) == 1 else '')
    return ''


def explicit_weapon_conflicts(text: str, names: list[str]) -> list[tuple[str, str]]:
    """Require an asserted actor and the same weapon in both state claims.

    Unattributed clauses and clauses involving several weapons cannot prove
    an ownership conflict. They remain available to semantic audit.
    """
    facts: dict[tuple[str, str], set[str]] = {}
    for clause in re.split(r"[，。；、！？\n]", str(text or "")):
        subject, owned = leading_named_span(clause, names)
        if not subject:
            continue
        weapons = [weapon for weapon in WEAPON_WORDS if weapon in owned]
        if len(weapons) != 1:
            continue
        states = facts.setdefault((subject, weapons[0]), set())
        if asserted_marker(owned, HOLD_MARKERS):
            states.add('hold')
        if asserted_marker(owned, RELEASE_MARKERS):
            states.add('release')
    return [key for key, states in facts.items() if states == {'hold', 'release'}]
