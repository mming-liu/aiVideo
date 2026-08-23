"""F. kf t_sec 越界约束（v6.42 迁移自 _hard_fix_kf_t_sec_bounds）.

t > dur × 0.9 → clamp 到 0.9×dur
t < 0.1 且 dur > 0.2 → 拉到 0.1s
幂等：clamp 后值与原值相同则跳过.
"""
from __future__ import annotations

from shared.kf_time import get_kf_t_sec, set_kf_t_sec
from .base import Constraint
from .types import RepairRecord, Severity


class KfTSecBoundsConstraint(Constraint):
    """F. kf t_sec 越界 clamp 到 dur × 0.9（保留 10% 余量给 lf 帧衔接）."""

    name = "kf_t_sec_bounds"
    severity = Severity.WARN
    fields = ()

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            try:
                dur = float(shot.get("duration_sec") or shot.get("duration") or 0)
            except (TypeError, ValueError):
                dur = 0
            if dur <= 0:
                continue
            kfs = shot.get("keyframes") or []
            if not isinstance(kfs, list):
                continue
            for j, kf in enumerate(kfs):
                if not isinstance(kf, dict):
                    continue
                t = get_kf_t_sec(kf, shot)
                if t is None:
                    continue
                new_t = t
                if new_t > dur * 0.9:
                    new_t = dur * 0.9
                if new_t < 0.1 and dur > 0.2:
                    new_t = 0.1
                if abs(new_t - t) > 0.001:
                    set_kf_t_sec(kf, new_t)
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx,
                            action="clamp",
                            target=f"keyframes[{j}].t_sec",
                            before=f"{t:.3f}",
                            after=f"{new_t:.3f}",
                        )
                    )
        return repairs
