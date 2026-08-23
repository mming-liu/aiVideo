"""Q. lf_desc 动作进行态约束（v6.42 迁移自 _hard_fix_lf_midstate）.

lf_desc 命中"初现/即将/正在/渐渐/缓慢/慢慢/缓缓" → 替换为"已/已现"。

幂等：替换前后无变化则跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


# 与 validation._LF_MIDSTATE_TERMS 同源
_MIDSTATE_TERMS = {
    "初现": "已现",
    "即将": "已",
    "正在": "已",
    "渐渐": "已",
    "缓慢": "已",
    "慢慢": "已",
    "缓缓": "已",
}


class LfMidstateConstraint(Constraint):
    """Q. lf_desc 动作进行态：初现/即将/正在/渐渐/缓慢/慢慢/缓缓 → 已/已现.

    severity=WARN: 动作态可由后续 repair round 修.
    repair_marker="已现"（已现/已 都在替换后出现）.
    """

    name = "lf_midstate"
    severity = Severity.WARN
    repair_marker = "已现"
    fields = ("lf_desc",)

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            lf = shot.get("lf_desc", "") or ""
            if not lf:
                continue
            new_lf = lf
            for old, new in _MIDSTATE_TERMS.items():
                if old in new_lf:
                    new_lf = new_lf.replace(old, new)
            if new_lf != lf:
                shot["lf_desc"] = new_lf
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx,
                        action="rewrite",
                        target="lf_desc",
                        before=lf,
                        after=new_lf,
                    )
                )
        return repairs
