"""Repair visual fields when core keywords are missing."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.gap_analysis import calculate_keyword_coverage
from .base import Constraint
from .types import RepairRecord, Severity


class CoreKeywordCoverageConstraint(Constraint):
    """Append at most three missing keywords to the first-frame description."""

    name = "core_keyword_coverage"
    severity = Severity.WARN
    fields = ("ff_desc",)

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            missing = calculate_keyword_coverage(shot, ctx.novel_text).get("missing") or []
            if not missing:
                continue
            inject = "、".join(missing[:3])
            ff_desc = str(shot.get("ff_desc") or "")
            suffix = f"，{inject}可见。"
            if inject in ff_desc:
                continue
            shot["ff_desc"] = ff_desc.rstrip("。. ") + suffix
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=shot.get("index", idx),
                    action="append",
                    target="ff_desc",
                    before=ff_desc,
                    after=shot["ff_desc"],
                )
            )
        return repairs
