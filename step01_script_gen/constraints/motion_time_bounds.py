"""G2. motion_desc 时段与 duration_sec 一致性约束.

LLM 常把动作写成 ``0-2秒 / 2-4秒 / 4-6秒``，却把镜头时长写成 4 秒。
这不是可忽略的告警：视频模型会按 4 秒提交请求，第三段动作必然被截断。
本约束只做一件事——当动作显式结束时间超过镜头时长时，把镜头时长扩到
动作结束时间（不超过当前模型时长上限）。若模型上限不足，保留原值并交给
最终质量门阻塞，绝不静默裁剪动作。
"""
from __future__ import annotations

import re

from .base import Constraint
from .types import RepairRecord, Severity

_RANGE_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:-|~|至|到)\s*(\d+(?:\.\d+)?)\s*(?:秒|s)?"
)
_SINGLE_RE = re.compile(r"(?:前|第)\s*(\d+(?:\.\d+)?)\s*秒")


class MotionTimeBoundsConstraint(Constraint):
    """动作分段最大结束秒数 > duration_sec 时扩时长，避免生成端截断."""

    name = "motion_time_bounds"
    severity = Severity.WARN
    fields = ("motion_desc",)

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        try:
            from shared.model_caps import active_video_duration_cap
            cap = float(active_video_duration_cap() or 0)
        except Exception:
            cap = 0.0

        for shot, _is_hook, idx in self.iter_shots_and_hook(ctx):
            motion = str(shot.get("motion_desc") or "")
            if not motion:
                continue
            try:
                duration = float(shot.get("duration_sec") or shot.get("duration") or 0)
            except (TypeError, ValueError):
                continue
            ends: list[float] = []
            for match in _RANGE_RE.finditer(motion):
                try:
                    ends.append(float(match.group(2)))
                except ValueError:
                    pass
            for match in _SINGLE_RE.finditer(motion):
                try:
                    ends.append(float(match.group(1)))
                except ValueError:
                    pass
            if not ends:
                continue
            required = max(ends)
            if required <= duration + 0.01:
                continue
            if cap and required > cap + 0.01:
                # Cannot make the action fit without violating model limits;
                # leave it for the visible final quality gate.
                continue
            new_duration = round(required, 3)
            before = shot.get("duration_sec")
            shot["duration_sec"] = new_duration
            if "duration" in shot:
                shot["duration"] = new_duration
            repairs.append(RepairRecord(
                constraint_name=self.name,
                shot_index=idx,
                action="set_field",
                target="duration_sec",
                before=str(before),
                after=str(new_duration),
                detail=f"motion_desc 最大结束 {required:g}s，延长镜头避免生成端截断",
            ))
        return repairs


__all__ = ["MotionTimeBoundsConstraint"]