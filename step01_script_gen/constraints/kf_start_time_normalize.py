"""kf 起点时刻归一：`t_sec <= 0` → 首个动作时段中点（v6.84，阶段 1）.

## 为什么需要它

项目契约（prompt 规则 16.4 / `shared/kf_time.py` / 各校验器）要求
`0 < t_sec <= duration_sec`。但 2026-09-21 重跑实测：**钩子的首个 KF 稳定写成
`t_sec=0.0`**——挂了钩子质量门（`kf_at_shot_start`），门内重生 **2 次**仍是 0.0，
最后带着 blocking 落盘。

`t_sec=0` 的语义是「首帧 = 开场态」，而那正是 `ff_desc` 自己——契约禁止它是有
道理的（同一时刻两帧，图片模型收到重复指令）。

## 归一到哪

规则 16.4 已经写好了答案：**kf 物理时刻 = 所属时段中点**。所以取 `motion_desc`
第一个时段的中点即可——这不是新造时间，而是把非法值改成该规则本来要求的值。
无可用时段时退到 `min(0.5, duration/2)`。

## 位置：第 1 段（`PRE_TEMPORAL_NORMALIZE`）

它写 `keyframes[*].t_sec`，属**时间归一**，必须发生在 KF 时间审计（第 3 段）之前。
放在 `motion_time_integrity`（修非法时段）之后，这样时段表本身已经是干净的。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.kf_time import get_kf_t_sec, set_kf_t_sec
from shared.motion_segments import parse_segments

from .base import Constraint
from .types import RepairRecord, Severity


class KfStartTimeNormalizeConstraint(Constraint):
    """`t_sec <= 0` 的关键帧归一到首个动作时段中点（只动非法值，幂等）."""

    name = "kf_start_time_normalize"
    severity = Severity.WARN
    fields = ()

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            kfs = shot.get("keyframes")
            if not isinstance(kfs, list) or not kfs:
                continue
            try:
                dur = float(shot.get("duration_sec") or shot.get("duration") or 0)
            except (TypeError, ValueError):
                dur = 0.0
            if dur <= 0:
                continue
            target = self._target_time(str(shot.get("motion_desc") or ""), dur)
            if target is None:
                continue
            for j, kf in enumerate(kfs):
                if not isinstance(kf, dict):
                    continue
                t = get_kf_t_sec(kf, shot)
                if t is None or t > 0:
                    continue
                set_kf_t_sec(kf, target)
                repairs.append(RepairRecord(
                    constraint_name=self.name,
                    shot_index=None if is_hook else shot.get("index", idx),
                    action="rewrite",
                    target=f"keyframes[{j}].t_sec",
                    before=str(t),
                    after=str(target),
                    detail=(
                        "t_sec<=0 违反 0 < t_sec <= duration_sec（首帧与开场态重复）；"
                        "按规则 16.4「kf 时刻 = 时段中点」归一到首个动作时段中点"
                    ),
                ))
        return repairs

    @staticmethod
    def _target_time(motion_desc: str, dur: float):
        """首个动作时段中点；无可用时段 → `min(0.5, dur/2)`."""
        segs = parse_segments(motion_desc, dur) if motion_desc else []
        if segs:
            start, end = segs[0]
            mid = round((start + end) / 2, 2)
            if 0 < mid <= dur:
                return mid
        fallback = round(min(0.5, dur / 2), 2)
        return fallback if 0 < fallback <= dur else None
