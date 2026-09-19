"""Repair visual fields when core keywords are missing."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.gap_analysis import calculate_keyword_coverage
from .base import Constraint
from .types import RepairRecord, Severity, Violation


class CoreKeywordCoverageConstraint(Constraint):
    """Report missing core keywords without mutating visual descriptions.

    Missing keywords may be abstract, malformed, or non-visual fragments.  A
    previous version appended them as ``，<keyword>可见。``, which produced
    pollution such as ``庄仆、举灯盏扬可见`` and ``旭心口暗可见`` in ff_desc.
    The safe contract is: diagnose only; the LLM or a dedicated visual
    normalizer must decide whether and where a real visual concept belongs.
    """

    name = "core_keyword_coverage"
    severity = Severity.WARN
    fields = ("ff_desc",)

    def apply(self, ctx) -> list[RepairRecord]:
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            missing = calculate_keyword_coverage(shot, ctx.novel_text).get("missing") or []
            if not missing:
                continue
            preview = "、".join(str(x) for x in missing[:5])
            ctx.record_violation(Violation(
                constraint_name=self.name,
                severity=Severity.WARN,
                shot_index=None if is_hook else shot.get("index", idx),
                field="ff_desc",
                message=(
                    f"core_keywords 覆盖不足（missing={preview}）——"
                    "只记录，不自动拼接；请通过正式重生成或专门视觉归一化补齐"
                ),
                evidence=str(shot.get("core_keywords") or ""),
                repaired=False,
            ))
        return []
