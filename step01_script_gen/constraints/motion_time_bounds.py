"""G2. motion_desc 时段与 duration_sec 一致性约束（v6.79 起只报告）.

LLM 常把动作写成 ``0-2秒 / 2-4秒 / 4-6秒``，却把镜头时长写成 4 秒。
这不是可忽略的告警：视频模型会按 4 秒提交请求，第三段动作必然被截断。

## v6.79（2026-09-20）：停止「按文本最大秒数延长 duration_sec」

原实现检测到超窗就把 `duration_sec` 扩到动作提到的最晚时刻。三个问题
（见 `docs/step01-temporal-writers-implementation-review-2026-09-19.md` 发现 1）：

1. **夹取无效**：本约束序号 62，`runtime_extend` 的单镜上限夹取在序号 36
   ——链前的夹取可以被链后的写入撤销，最终脚本仍可能带单镜超 cap 时长落盘。
2. **上限口径不一致**：原实现只取**模型 cap**，没有同时取项目 8s 硬上限的
   较小值。当活动模型允许 12 秒、动作文本提到第 10 秒时，它可以在
   `runtime_extend` 之后把镜头重新扩到 10 秒。
3. **越权**：延长时间轴是**结构决定**，不是文本清洗。动作写不下应该拆镜或
   精简动作，而不是让一个清洗类约束悄悄改镜头长度——而且它同样发生在
   KF 时间检查（序号 8-11）之后，使那些检查看到的时长不是最终时长。

现在只报告：HARD 违例 + 未改写记录。每段报告带 `min(项目上限, 模型上限)`
的判定结果，明确指出是「拆镜」还是「精简动作」。blocking 判定在
`quality_issues._shot_duration_issues` / `_action_time_issues`。

幂等：纯检测，无副作用。
"""
from __future__ import annotations

from shared import motion_segments as _ms
from .base import Constraint
from .types import RepairRecord, Severity, Violation

# 项目单镜硬上限（08-23 用户规则），与 timecode.MAX_SHOT_DURATION 同值。
# 这里独立常量会漂移——导入放在函数内以避免模块级循环依赖，见 `_project_shot_cap()`。
def _project_shot_cap() -> float:
    """生效单镜上限（v6.97：委托唯一源；此前只取项目值，与本文件下方的取严重复）."""
    # v6.98（2026-09-22 审核 P1-1）：**删掉异常回退 8.0**——唯一源拿不到时应当
    # 明确失败（引擎会打出 `[CONSTRAINT] <name> 异常`），而不是悄悄按旧规则算。
    from step01_script_gen.timecode import active_shot_cap
    return active_shot_cap()


class MotionTimeBoundsConstraint(Constraint):
    """动作分段最大结束秒数 > duration_sec 时**报告**（不再自动扩时长）."""

    name = "motion_time_bounds"
    severity = Severity.WARN
    fields = ("motion_desc",)

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        try:
            from shared.model_caps import active_video_duration_cap
            model_cap = float(active_video_duration_cap() or 0)
        except Exception:
            model_cap = 0.0
        # v6.97：`_project_shot_cap()` 现在就是「项目值与模型 cap 取严」的唯一源
        project_cap = _project_shot_cap()
        hard_cap = project_cap

        for shot, _is_hook, idx in self.iter_shots_and_hook(ctx):
            motion = str(shot.get("motion_desc") or "")
            if not motion:
                continue
            try:
                duration = float(shot.get("duration_sec") or shot.get("duration") or 0)
            except (TypeError, ValueError):
                continue
            # v6.76：提取逻辑与 quality_issues._action_time_issues 合并到
            # shared.motion_segments.max_mentioned_second（原先各写一遍）
            _max_sec = _ms.max_mentioned_second(motion)
            if _max_sec is None:
                continue
            required = _max_sec
            if required <= duration + 0.01:
                continue

            if required > hard_cap + 0.01:
                fix_hint = (
                    f"动作需 {required:g}s 但单镜上限是 {hard_cap:g}s"
                    f"（项目 {project_cap:g}s 与模型 {model_cap:g}s 取严）——"
                    "必须拆成多镜或精简动作，不能延长时间轴。"
                )
            else:
                fix_hint = (
                    f"应把动作链拆成 ≤{hard_cap:g}s 的子镜，或精简动作描述；"
                    "本约束不再自动延长 duration_sec。"
                )
            ctx.record_violation(Violation(
                constraint_name=self.name,
                severity=Severity.HARD,
                shot_index=idx,
                field="duration_sec",
                repaired=False,
                message=(
                    f"motion_desc 提到第 {required:g}s，镜头时长仅 {duration:g}s"
                    f"——生成端会截断后半段动作。{fix_hint}"
                ),
                evidence=f"required={required:g} duration={duration:g} hard_cap={hard_cap:g}",
            ))
            repairs.append(RepairRecord(
                constraint_name=self.name,
                shot_index=idx,
                action="detect",
                target="duration_sec",
                before=str(duration),
                after=f"(HARD, 未改写；动作需 {required:g}s)",
                detail="动作超窗只报告，不延长时长",
            ))
        return repairs


__all__ = ["MotionTimeBoundsConstraint"]
