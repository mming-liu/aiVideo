"""Validate motion time prefixes; never guess a replacement timeline."""
from __future__ import annotations

import re

from .base import Constraint
from .types import Severity, Violation


_TIME_PREFIX_RE = re.compile(
    r"^\s*(?P<a>-?\d+(?:\.\d+)?)\s*(?:-|~|至|到)\s*(?P<b>-?\d+(?:\.\d+)?)\s*(?:秒|s)?\s*[:：]?\s*"
)


class MotionTimeIntegrityConstraint(Constraint):
    name = "motion_time_integrity"
    severity = Severity.HARD
    fields = ("motion_desc",)

    def apply(self, ctx) -> list:
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            motion = str(shot.get("motion_desc") or "")
            if not motion:
                continue
            try:
                duration = float(shot.get("duration_sec") or shot.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0.0
            if not re.search(r"\d+\s*(?:-|~|至|到)\s*\d+", motion):
                # Static/non-timed motion descriptions are valid and must not
                # be forced into an invented timeline.
                continue
            invalid = False
            for atom in [a.strip() for a in re.split(r"[;；]", motion) if a.strip()]:
                prefix = atom.split("：", 1)[0] if "：" in atom else atom
                if not re.search(r"\d", prefix):
                    invalid = True
                    break
                m = _TIME_PREFIX_RE.match(atom)
                if not m:
                    invalid = True
                    break
                a, b = float(m.group("a")), float(m.group("b"))
                if a < 0 or b < 0 or b <= a or (duration > 0 and b > duration + 0.05):
                    invalid = True
                    break
            if invalid:
                ctx.record_violation(Violation(
                    constraint_name=self.name,
                    severity=Severity.HARD,
                    shot_index=None if is_hook else shot.get("index", idx),
                    field="motion_desc",
                    message="motion 时间前缀非法或损坏；缺少修复前可靠快照，保留原文并阻止进入下游",
                    evidence=motion[:180],
                    repaired=False,
                ))
        return []


__all__ = ["MotionTimeIntegrityConstraint"]
