"""Outline pre-validation (P2.2).
Validate outline structural quality. Issues go into GAP messages.

Rules (R10):
1. 3-5 beats
2. position_pct strictly increasing, last beat >= 70%
3. At least 1 beat with has_dialogue=True
4. Intensity has variation (not all low / not all high)
"""
from typing import List, Tuple


def validate_outline(script: dict, novel_text: str = "") -> Tuple[List[str], List[str]]:
    """Validate outline quality.

    Returns:
        (fatal_messages, warning_messages)
        - fatal: issues that trigger GAP retry
        - warning: hints only, no retry
    """
    fatal = []
    warning = []

    outline = script.get("outline") or []

    # R0: outline exists (only doubao path provides it; deepseek scripts skip)
    if not outline:
        # Not a fatal error - deepseek path scripts don't have outline.
        # Return empty lists so GAP validation passes for non-doubao paths.
        return [], []

    # R1: 3-5 beats
    n = len(outline)
    if n < 3:
        fatal.append(f"outline too few beats: {n} beats, need at least 3")
    elif n > 5:
        fatal.append(f"outline too many beats: {n} beats, max 5")

    # extract beat fields
    beats = []
    for i, b in enumerate(outline):
        if not isinstance(b, dict):
            fatal.append(f"outline[{i}] format error: not an object")
            continue
        beats.append({
            "idx": i,
            "pct": b.get("position_pct"),
            "summary": b.get("beat_summary", ""),
            "has_dialogue": b.get("has_dialogue"),
            "intensity": b.get("intensity"),
        })

    if not beats:
        return fatal, warning

    # R2: position_pct strictly increasing, last >= 70
    pcts = [b["pct"] for b in beats if isinstance(b["pct"], (int, float))]
    if len(pcts) == len(beats):
        for i in range(1, len(pcts)):
            if pcts[i] <= pcts[i - 1]:
                msg = (f"outline position_pct not increasing: "
                       f"beat[{i}]={pcts[i]} <= beat[{i-1}]={pcts[i-1]}; "
                       f"must be strictly ascending")
                fatal.append(msg)
                break
        if pcts[-1] < 70:
            msg = (f"outline last beat too early: at {pcts[-1]}%, "
                   f"need >= 70% (climax/reversal should be in second half)")
            fatal.append(msg)
    else:
        fatal.append("outline position_pct missing or wrong type: all beats need integer position_pct")

    # R3: at least 1 beat with dialogue
    dialogue_count = sum(1 for b in beats if b["has_dialogue"] is True)
    if dialogue_count == 0:
        msg = ("outline zero dialogue beats: all beats have has_dialogue=false; "
               "consider adding at least one beat with character dialogue")
        warning.append(msg)

    # R4: intensity variation
    inten = [b["intensity"] for b in beats if isinstance(b["intensity"], str)]
    if len(inten) == len(beats):
        inten_set = set(inten)
        if inten_set == {"low"}:
            warning.append("outline all-low intensity: no conflict arc; ramp up mid/late beats")
        elif inten_set == {"high"}:
            warning.append("outline all-high intensity: no build-up; lower early beats for progression")
        rank = {"low": 1, "medium": 2, "high": 3}
        first_r = rank.get(inten[0], 0)
        last_r = rank.get(inten[-1], 0)
        if last_r < first_r:
            msg = (f"outline intensity decreasing: first={inten[0]} -> last={inten[-1]}; "
                   f"last beat should normally be the strongest point")
            warning.append(msg)

    return fatal, warning
