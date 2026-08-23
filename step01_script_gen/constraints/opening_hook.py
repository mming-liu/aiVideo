"""K. 开篇冲突约束（v6.42 迁移自 _hard_fix_opening_hook）.

前 1/3 镜（=max(1, len//3)）都不含 _PUNCH_WORDS 词 → 在第一镜 prompt 末尾
追加"危机一触即发，暗流涌动，惊觉对峙"。

幂等：已含"惊觉"或"对峙"或任一 _PUNCH_WORDS 词 → 跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.validation import _PUNCH_WORDS
from .base import Constraint
from .types import RepairRecord, Severity


_TAIL = "。危机一触即发，暗流涌动，惊觉对峙"


class OpeningHookConstraint(Constraint):
    """K. 开篇冲突：前 1/3 段缺爆点词 → 追加'危机一触即发'组合.

    severity=WARN: 爆点可由后续 repair round 修.
    repair_marker="惊觉"（含"对峙"和"危机"亦视为已修）.
    """

    name = "opening_hook"
    severity = Severity.WARN
    repair_marker = "惊觉"
    fields = ("prompt",)

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = ctx.shots
        if not shots or not isinstance(shots, list):
            return repairs
        head = shots[: max(1, len(shots) // 3)]
        head_text = " ".join(
            s.get("prompt", "") + s.get("main_subject", "") + s.get("motion_desc", "")
            for s in head if isinstance(s, dict)
        )
        if any(p in head_text for p in _PUNCH_WORDS):
            return repairs
        s = shots[0]
        if not isinstance(s, dict):
            return repairs
        prompt = s.get("prompt", "") or ""
        if "惊觉" in prompt or "对峙" in prompt:
            return repairs
        before = prompt
        s["prompt"] = prompt.rstrip("。") + _TAIL
        repairs.append(
            RepairRecord(
                constraint_name=self.name,
                shot_index=s.get("index", 0),
                action="append",
                target="prompt",
                before=before,
                after=s["prompt"],
            )
        )
        return repairs
