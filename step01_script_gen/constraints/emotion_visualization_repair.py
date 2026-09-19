"""Final deterministic repair for abstract emotion words in model-visible fields."""
from __future__ import annotations

from .base import Constraint
from .types import RepairRecord, Severity
from .kf_timeline_normalize import _strip_frame_label


def _strip_frame_state_wrapper(text: str) -> str:
    return _strip_frame_label(text or "")


class EmotionVisualizationRepairConstraint(Constraint):
    name = "emotion_visualization_repair"
    severity = Severity.HARD
    fields = ()

    def apply(self, ctx) -> list[RepairRecord]:
        from step01_script_gen.validation import _fix_emotion_visualization
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            for field in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
                value = str(shot.get(field) or "")
                fixed = _fix_emotion_visualization(value)
                if fixed and fixed != value:
                    shot[field] = fixed
                    repairs.append(RepairRecord(
                        constraint_name=self.name,
                        shot_index=None if is_hook else shot.get("index", idx),
                        action="rewrite",
                        target=field,
                        before=value[-120:],
                        after=fixed[-120:],
                        detail="抽象情绪词转可视化动作（最终确定性门）",
                    ))
            for i, kf in enumerate(shot.get("keyframes") or []):
                if not isinstance(kf, dict):
                    continue
                value = str(kf.get("desc") or "")
                fixed = _strip_frame_state_wrapper(value)
                fixed = _fix_emotion_visualization(fixed)
                if fixed and fixed != value:
                    kf["desc"] = fixed
                    repairs.append(RepairRecord(
                        constraint_name=self.name,
                        shot_index=None if is_hook else shot.get("index", idx),
                        action="rewrite",
                        target=f"keyframes[{i}].desc",
                        before=value[-120:],
                        after=fixed[-120:],
                        detail="抽象情绪词转可视化动作（最终确定性门）",
                    ))
        return repairs


__all__ = ["EmotionVisualizationRepairConstraint"]