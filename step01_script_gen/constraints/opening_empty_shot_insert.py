"""Insert an opening environment shot when the first scene lacks one."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


_EMPTY_HINTS = ("空镜", "环境", "远景", "特写", "水波", "树影")


class OpeningEmptyShotInsertConstraint(Constraint):
    """Keep an establishing shot in the opening window and compensate duration."""

    name = "opening_empty_shot_insert"
    severity = Severity.WARN
    fields = ("main_subject",)
    skip_hook = True

    def apply(self, ctx) -> list[RepairRecord]:
        shots = ctx.shots
        if len(shots) < 3:
            return []
        window = shots[:3]
        if not all(isinstance(shot, dict) for shot in window):
            return []
        if any(
            any(hint in str(shot.get("main_subject") or "") for hint in _EMPTY_HINTS)
            or "环境空镜" in str(shot.get("ff_desc") or "")
            or "无人无角色" in str(shot.get("lf_desc") or "")
            for shot in window
        ):
            return []
        # A generic establishing shot is not evidence that this story needs
        # one.  The previous template inserted a warm-sun daylight shot into
        # night scenes and changed story order/duration.  Report the missing
        # establishing beat and let audit/LLM add a context-correct shot.
        return [RepairRecord(
            constraint_name=self.name,
            shot_index=1,
            action="warn",
            target="shots[0:3]",
            detail=(
                "开篇前三镜缺少建立镜头；保留原镜头/时长，禁止自动插入"
                "固定“暖金阳光”空镜模板。"
            ),
        )]


__all__ = ["OpeningEmptyShotInsertConstraint"]
