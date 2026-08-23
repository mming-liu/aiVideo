"""R. hook 时长下限约束（v6.42 迁移自 _hard_fix_hook_duration）.

hook duration_sec < 4.0 → 扩到 4.0；若 kfs 数 < 2 → 补 1 个 after 帧。

幂等：duration_sec ≥ 4.0 且 kfs ≥ 2 → 跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


class HookDurationConstraint(Constraint):
    """R. hook 时长下限：< 4s → 扩 4s；kfs < 2 → 补 1 个 after 帧.

    severity=WARN: 时长可由后续 repair round 修.
    无 repair_marker（按 duration_sec/kfs 实际值检测）.
    """

    name = "hook_duration"
    severity = Severity.WARN
    fields = ()
    skip_hook = False  # 仅处理 hook

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        hook = ctx.hook
        if not hook or not isinstance(hook, dict):
            return repairs
        try:
            dur = float(hook.get("duration_sec") or hook.get("duration") or 0)
        except (TypeError, ValueError):
            dur = 0
        if dur >= 4.0:
            return repairs
        # 扩到 4.0
        before_dur = dur
        hook["duration"] = 4.0
        hook["duration_sec"] = 4.0
        repairs.append(
            RepairRecord(
                constraint_name=self.name,
                shot_index=None,
                action="set_field",
                target="hook.duration_sec",
                before=f"{before_dur:.3f}",
                after="4.000",
            )
        )
        # 补 1 个 after 帧
        kfs = hook.get("keyframes")
        if isinstance(kfs, list) and len(kfs) < 2:
            kfs.append({"desc": "画面定格，所有角色定锚", "stage": "after", "t_sec": 3.5})
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=None,
                    action="set_field",
                    target="hook.keyframes[1]",
                    before="",
                    after="stage=after, t_sec=3.5, desc=画面定格，所有角色定锚",
                )
            )
        return repairs
