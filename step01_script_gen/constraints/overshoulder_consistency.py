"""Break monotonous consecutive dialogue framing with an over-shoulder shot."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


_DIALOGUE_HINTS = ("说", "道", "问", "答", "喊", "吼", "质问", "怒喝", "低声", "冷声")
_OVERSHOULDER = "过肩"


def _is_dialogue(shot: dict) -> bool:
    subject = str(shot.get("main_subject") or "")
    narration = str(shot.get("narration_segment") or "")
    return any(hint in subject for hint in _DIALOGUE_HINTS) or bool(narration.strip())


class OvershoulderConsistencyConstraint(Constraint):
    """Use an over-shoulder frame for three consecutive static dialogue shots."""

    name = "overshoulder_consistency"
    severity = Severity.WARN
    fields = ("camera_movement",)
    skip_hook = True

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = ctx.shots
        if len(shots) < 3:
            return repairs
        for i in range(len(shots) - 2):
            group = shots[i:i + 3]
            if not all(isinstance(shot, dict) for shot in group):
                continue
            if not all(_is_dialogue(shot) for shot in group):
                continue
            locations = {
                str(shot.get("location") or shot.get("scene") or "").strip()
                for shot in group
            }
            locations.discard("")
            cameras = {str(shot.get("camera_movement") or "").strip() for shot in group}
            cameras.discard("")
            if len(locations) != 1 or len(cameras) != 1:
                continue

            middle = group[1]
            camera = str(middle.get("camera_movement") or "")
            if _OVERSHOULDER in camera:
                continue
            middle["camera_movement"] = "过肩镜头"



            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=middle.get("index", i + 2),
                    action="rewrite",
                    target="camera_movement",
                    before=camera,
                    after="过肩镜头",
                )
            )
        return repairs
