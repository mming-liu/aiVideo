"""L. 爆点密度约束（v6.42 迁移自 _hard_fix_punch_density）.

每 30 秒应有 1 爆点。若总数 < 目标，给最后一镜 prompt 追加"局势骤变，N个
爆点叠加"。

幂等：已含"局势骤变" → 跳过；全片达标 → 跳过。
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


class PunchDensityConstraint(Constraint):
    """L. 爆点密度：每 30s 应有 1 爆点 → 不足则末镜追加'局势骤变'.

    severity=WARN: 爆点可由后续 repair round 修.
    repair_marker="局势骤变".
    """

    name = "punch_density"
    severity = Severity.WARN
    repair_marker = "局势骤变"
    fields = ("prompt",)

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = ctx.shots
        if not shots or not isinstance(shots, list):
            return repairs

        punch_count = 0
        total_dur = 0.0
        for s in shots:
            if not isinstance(s, dict):
                continue
            text = " ".join([
                s.get("prompt", ""),
                s.get("narration_segment", ""),
                s.get("main_subject", ""),
                s.get("motion_desc", ""),
            ])
            if any(p in text for p in _PUNCH_WORDS):
                punch_count += 1
            try:
                total_dur += float(s.get("duration_sec") or s.get("duration") or 0)
            except (TypeError, ValueError):
                pass
        if total_dur <= 0:
            total_dur = 60.0
        needed = max(1, int(total_dur // 30) + (1 if total_dur % 30 else 0))
        if punch_count >= needed:
            return repairs

        shortage = needed - punch_count
        s = shots[-1]
        if not isinstance(s, dict):
            return repairs
        prompt = s.get("prompt", "") or ""
        if "局势骤变" in prompt:
            return repairs
        before = prompt
        s["prompt"] = prompt.rstrip("。") + f"。局势骤变，{shortage}个爆点叠加"
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
