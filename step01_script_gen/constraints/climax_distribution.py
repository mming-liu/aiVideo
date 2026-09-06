"""Climax distribution repair shared by all text providers."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.climax_words import (
    CLIMAX_WORDS_GAP_INJECT,
    climax_words_for_gap,
)
from step01_script_gen.gap_analysis import normalize_core_keywords
from .base import Constraint
from .types import RepairRecord, Severity


class ClimaxDistributionConstraint(Constraint):
    """Ensure climax shots are sufficient, distributed, and GAP-visible."""

    name = "climax_distribution"
    severity = Severity.WARN
    fields = ("main_subject", "motion_desc", "prompt", "narration_segment", "subtitle", "core_keywords")
    skip_hook = True

    @staticmethod
    def _is_climax(shot: dict, words: list[str]) -> bool:
        text = " ".join(
            [
                str(shot.get("narration_segment") or ""),
                str(shot.get("subtitle") or ""),
                *normalize_core_keywords(shot.get("core_keywords")),
            ]
        )
        return any(word in text for word in words)

    @staticmethod
    def _append_text(value: object, text: str) -> object:
        if not isinstance(value, str) or text in value:
            return value
        return value.rstrip("。. ") + text

    @staticmethod
    def _append_keywords(shot: dict, words: tuple[str, ...]) -> bool:
        current = normalize_core_keywords(shot.get("core_keywords"))
        missing = [word for word in words if word not in current]
        if not missing:
            return False
        shot["core_keywords"] = current + missing
        return True

    def _inject(self, shot: dict, idx: int, suffix: str, repairs: list[RepairRecord]) -> None:
        changed = False
        for field in ("narration_segment", "subtitle", "prompt", "main_subject"):
            before = shot.get(field)
            after = self._append_text(before, suffix)
            if after != before:
                shot[field] = after
                changed = True
        changed = self._append_keywords(shot, CLIMAX_WORDS_GAP_INJECT) or changed
        if changed:
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=shot.get("index", idx),
                    action="append",
                    target="narration_segment+subtitle+prompt+main_subject+core_keywords",
                    before="",
                    after=suffix,
                )
            )

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = [s for s in ctx.shots if isinstance(s, dict)]
        if len(shots) < 3:
            return repairs

        words = climax_words_for_gap(ctx.script)
        readable = [
            (i, shot, self._is_climax(shot, words))
            for i, shot in enumerate(shots)
        ]
        missing = 3 - sum(state for _, _, state in readable)
        if missing <= 0:
            return repairs

        suffix = "。" + "，".join(CLIMAX_WORDS_GAP_INJECT) + "，局势骤变"
        half = len(shots) // 2
        candidates = [item for item in readable[1:] if not item[2]]
        candidates.sort(key=lambda item: item[0] >= half, reverse=True)
        for i, shot, _ in candidates[:missing]:
            self._inject(shot, i, suffix, repairs)
        return repairs
