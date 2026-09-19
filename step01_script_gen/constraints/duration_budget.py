"""Duration budget: enforce the project's 60-90s acceptance range.

The prompt plans for 70-80s and the final gate used to treat the 60-90s
standard as advisory.  This constraint only trims *headroom* above the
minimum required by dialogue speed and explicit motion segments.  It never
shortens a shot below those minima.  If the sum of minima itself exceeds
90s, the issue remains unresolved and hard-fails the final gate; the story
must be simplified or shots merged instead of silently truncating audio.
"""
from __future__ import annotations

from .base import Constraint
from .types import RepairRecord, Severity, Violation

_MIN_TOTAL = 60.0
_MAX_TOTAL = 90.0
_SHOT_CAP = 8.0


class DurationBudgetConstraint(Constraint):
    name = "duration_budget"
    severity = Severity.HARD
    skip_hook = True

    def apply(self, ctx) -> list[RepairRecord]:
        shots = [s for s in (ctx.shots or []) if isinstance(s, dict)]
        if not shots:
            return []

        from step01_script_gen.timecode import (
            MIN_SHOT_DURATION,
            NARRATION_FIXED_DURATION,
            _estimate_duration,
            _extract_motion_duration,
        )
        from shared.dialogue import has_dialogue
        from shared.model_caps import active_video_duration_cap

        model_cap = float(active_video_duration_cap() or 0)
        shot_cap = min(_SHOT_CAP, model_cap) if model_cap else _SHOT_CAP

        durations: list[float] = []
        minima: list[float] = []
        for shot in shots:
            try:
                duration = float(shot.get("duration_sec") or shot.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0.0
            narration = str(shot.get("narration_segment") or "")
            motion_min = _extract_motion_duration(str(shot.get("motion_desc") or "")) or 0.0
            if has_dialogue(narration):
                speech_min = _estimate_duration(narration)
                minimum = max(motion_min, speech_min, MIN_SHOT_DURATION)
            elif narration:
                minimum = max(motion_min, NARRATION_FIXED_DURATION, MIN_SHOT_DURATION)
            else:
                minimum = max(motion_min, MIN_SHOT_DURATION)
            durations.append(duration)
            minima.append(minimum)

        total = sum(durations)
        if total <= _MAX_TOTAL + 0.01:
            return []

        # A shot whose real motion/dialogue minimum exceeds the single-shot cap
        # must be split or simplified.  Never pretend its minimum is 8s and
        # compress it below the explicit action duration.
        oversized_min = [
            (i, m) for i, m in enumerate(minima) if m > shot_cap + 0.01
        ]
        if oversized_min:
            detail = ", ".join(
                f"shot{shots[i].get('index', i)}={m:.1f}s"
                for i, m in oversized_min[:5]
            )
            ctx.record_violation(Violation(
                constraint_name=self.name,
                severity=Severity.HARD,
                field="duration_sec",
                message=(
                    f"单镜最低需求超过 {shot_cap:.1f}s：{detail}；"
                    "必须拆镜或精简动作，duration_budget 不做低于真实最低需求的压缩。"
                ),
                evidence=f"oversized_min={oversized_min[:5]}",
                repaired=False,
            ))
            return []

        total_min = sum(minima)
        if total_min > _MAX_TOTAL + 0.01:
            ctx.record_violation(Violation(
                constraint_name=self.name,
                severity=Severity.HARD,
                field="duration_sec",
                message=(
                    f"正片总时长 {total:.1f}s > {_MAX_TOTAL:.0f}s，"
                    f"按台词语速/动作分段计算的最低总时长仍有 {total_min:.1f}s；"
                    "必须精简剧情、合并镜头或重写动作，禁止静默截断台词/动作。"
                ),
                evidence=f"shots={len(shots)}, min_total={total_min:.1f}s",
                repaired=False,
            ))
            return []

        need = total - _MAX_TOTAL
        reducible = sum(max(0.0, d - m) for d, m in zip(durations, minima))
        if reducible <= 0:
            return []

        repairs: list[RepairRecord] = []
        for i, shot in enumerate(shots):
            headroom = max(0.0, durations[i] - minima[i])
            if headroom <= 0:
                continue
            cut = need * (headroom / reducible)
            new_duration = max(minima[i], durations[i] - cut)
            new_duration = round(new_duration, 3)
            if abs(new_duration - durations[i]) < 0.001:
                continue
            before = shot.get("duration_sec")
            shot["duration_sec"] = new_duration
            if "duration" in shot:
                shot["duration"] = new_duration
            repairs.append(RepairRecord(
                constraint_name=self.name,
                shot_index=shot.get("index", i),
                action="set_field",
                target="duration_sec",
                before=str(before),
                after=str(new_duration),
                detail="压缩冗余时长到台词/动作最低需求，收敛至 90s 内",
            ))
        if repairs:
            ctx.record_violation(Violation(
                constraint_name=self.name,
                severity=Severity.HARD,
                field="duration_sec",
                message=f"正片总时长 {total:.1f}s 超出 90s，已压缩冗余时长至合法区间",
                evidence=f"shots={len(shots)}, reduced={total - sum(float(s.get('duration_sec') or 0) for s in shots):.1f}s",
                repaired=True,
            ))
        return repairs


__all__ = ["DurationBudgetConstraint"]