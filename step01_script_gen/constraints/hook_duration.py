"""R. hook 时长下限约束（v6.42 迁移自 _hard_fix_hook_duration；v6.76 全降为只报告）.

检测两类缺陷：
  1. hook `duration_sec` < 4.0（红果开场下限）
  2. hook `keyframes` 数 < 2

历史修法（v6.42~v6.75）：
  - 时长 < 4.0 → 直接把 `duration`/`duration_sec` 写成 4.0
  - kfs < 2 → append `{"desc": "画面定格，所有角色定锚", "stage": "after", "t_sec": 3.5}`

v6.76（2026-09-19）两项**全部降为只报告**，不再以任何方式造时长、造帧：
  1. 「扩到 4.0」是在无内容支撑下增加时长——与 runtime_extend 的按比例拉伸同一
     性质（拉伸只增时间不增事件，尾部会变成无 KF 覆盖的伪时段，再被下游补成
     「画面延续」假帧）。hook 时长短的根因是 motion/narration 内容不足，
     正确解法是补内容而非补时长。
  2. 补的 KF 有两个硬伤：`t_sec=3.5` 是**硬编码常量**，不随新的 duration_sec 推导
     （换任何时长都不变）；触发条件只看 `len(kfs) < 2` 不看内容，补出的是没有
     任何故事证据的「画面定格」帧——把占位符写成了事实。

  两项检查**解耦**：原实现 `if dur >= 4.0: return` 会让「时长达标但 KF 只有 1 个」
  的 hook 完全逃过检查；现各自独立上报。

  严重度用类声明级（WARN）。若判定 hook 不足应直接触发 hook regen，
  把下方 `_report(..., severity=Severity.HARD)` 即可（一行改动）。

幂等：只报告，天然幂等。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity, Violation

# 红果开场下限（秒）
_HOOK_MIN_SEC = 4.0
# hook 关键帧数下限
_HOOK_MIN_KFS = 2


class HookDurationConstraint(Constraint):
    """R. hook 时长下限 / 关键帧数下限：只检测并上报（v6.76 起）.

    severity=WARN: 时长可由后续 repair round 修.
    无 repair_marker（按 duration_sec/kfs 实际值检测）.
    """

    name = "hook_duration"
    severity = Severity.WARN
    fields = ()
    skip_hook = False  # 仅处理 hook

    def _report(
        self, ctx, repairs: list[RepairRecord], *, field: str,
        message: str, evidence: str, detail: str,
    ) -> None:
        ctx.record_violation(Violation(
            constraint_name=self.name,
            severity=self.severity,
            shot_index=None,
            field=field,
            message=message,
            evidence=evidence[:200],
            repaired=False,
        ))
        repairs.append(RepairRecord(
            constraint_name=self.name,
            shot_index=None,
            action="warn",
            target=field,
            before=evidence[:80],
            after=evidence[:80],
            detail=detail,
        ))

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        hook = ctx.hook
        if not hook or not isinstance(hook, dict):
            return repairs
        try:
            dur = float(hook.get("duration_sec") or hook.get("duration") or 0)
        except (TypeError, ValueError):
            dur = 0

        # 1) 时长下限 —— 只报告（v6.76：不再扩到 4.0）
        if dur < _HOOK_MIN_SEC:
            self._report(
                ctx, repairs, field="hook.duration_sec",
                message=(
                    f"hook 时长 {dur:.2f}s < 红果开场下限 {_HOOK_MIN_SEC:.1f}s；"
                    "保留原值未扩展——扩时长是在无内容支撑下增加时长，"
                    "会让尾部变成无 KF 覆盖的伪时段（被下游补成「画面延续」假帧）。"
                    "hook 时长短的根因是 motion/narration 内容不足，应补内容或走 hook regen。"
                ),
                evidence=f"hook duration_sec={dur:.3f}",
                detail="hook 时长不足下限，保留原值交由 audit/regen",
            )

        # 2) 关键帧数下限 —— 只报告（v6.76：不再补建固定 3.5s 定格帧）
        kfs = hook.get("keyframes")
        if isinstance(kfs, list) and len(kfs) < _HOOK_MIN_KFS:
            self._report(
                ctx, repairs, field="hook.keyframes",
                message=(
                    f"hook 仅 {len(kfs)} 个关键帧（< {_HOOK_MIN_KFS}）；保留原样未补建——"
                    "原实现补的「画面定格，所有角色定锚」t_sec=3.5 是硬编码常量，"
                    "与原故事无证据关系。hook 内容不足应走完整 hook regen。"
                ),
                evidence=f"keyframes len={len(kfs)}",
                detail="hook 关键帧不足，保留原样交由 audit/regen",
            )

        return repairs
