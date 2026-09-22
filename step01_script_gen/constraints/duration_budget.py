"""Duration budget: enforce the project's 60-90s acceptance range.

The prompt plans for 70-80s and the final gate used to treat the 60-90s
standard as advisory.

## v6.79（2026-09-20）：停止「按比例压缩镜头」

原实现在总量超 90s 时，把超额部分**按各镜 headroom 比例摊派**到每个镜头的
`duration_sec` 上。这同样是链尾改写（本约束序号 80，KF 时间检查在序号 8-11）：

  - 压缩后的时长不再被任何 KF 时间检查看过，`kf_time_window_alignment` 的
    「kf 物理时刻 = 时段铁律」在最终对象上可能已经不成立；
  - 「按比例摊」没有语义依据——它不知道哪个镜头的余量是真正冗余的，
    只会把每个镜头各削一刀；
  - 它撤销了 `runtime_extend` 之前的单镜 cap 夹取。

现在只报告：把**需要重写/拆分的镜头集合**输出给完整镜头事务
（`repaired=False` 的 HARD 违例），由 LLM regen 精简剧情或合并镜头。

仍然保留的两条硬判据（原本就是报告、不写入）：
  - 单镜真实最低需求即超过 `min(项目 8s, 模型 cap)` → 必须拆镜/精简动作；
  - 所有镜头最低需求之和已 > 90s → 精简剧情，禁止静默截断台词/动作。

幂等：纯检测，无副作用。
"""
from __future__ import annotations

from .base import Constraint
from .types import RepairRecord, Severity, Violation

_MIN_TOTAL = 60.0
_MAX_TOTAL = 90.0
# v6.97：删除写死的 `_SHOT_CAP = 8.0`——它让本约束按 8s 报 `Severity.HARD`
# 「必须拆镜」，把当前上限（模型 12s）下**合法**的 8-12s 镜头判成超载。改走唯一源。


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
        try:
            from step01_script_gen.timecode import active_shot_cap as _asc
            shot_cap = _asc()
        except Exception:
            shot_cap = min(8.0, model_cap) if model_cap else 8.0

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

        # v6.79：有可压缩余量也**不再自动压缩**——按比例摊派没有语义依据，
        # 且改写发生在 KF 时间检查之后。改为列出候选镜头交完整镜头事务。
        need = total - _MAX_TOTAL
        reducible = sum(max(0.0, d - m) for d, m in zip(durations, minima))
        if reducible <= 0:
            return []

        candidates = [
            (i, durations[i] - minima[i])
            for i in range(len(shots))
            if durations[i] - minima[i] > 0.01
        ]
        candidates.sort(key=lambda x: -x[1])
        detail = ", ".join(
            f"shot{shots[i].get('index', i)}（余量 {h:.1f}s）"
            for i, h in candidates[:5]
        )
        ctx.record_violation(Violation(
            constraint_name=self.name,
            severity=Severity.HARD,
            field="duration_sec",
            message=(
                f"正片总时长 {total:.1f}s 超出 {_MAX_TOTAL:.0f}s {need:.1f}s，"
                f"可压缩余量 {reducible:.1f}s（{detail}）。"
                "本约束不再按比例压缩镜头——压缩发生在 KF 时间检查之后，"
                "会让已审计的时长失效。请由 LLM 重写/合并上述镜头。"
            ),
            evidence=f"shots={len(shots)}, need={need:.1f}s, reducible={reducible:.1f}s",
            repaired=False,
        ))
        return [
            RepairRecord(
                constraint_name=self.name,
                shot_index=shots[i].get("index", i),
                action="detect",
                target="duration_sec",
                before=str(durations[i]),
                after="(HARD, 未改写；候选压缩镜)",
                detail="总时长超 90s：只报告候选，交完整镜头事务重写",
            )
            for i, _h in candidates[:5]
        ]


__all__ = ["DurationBudgetConstraint"]
