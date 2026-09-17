"""W4. Transition keywords now live only in clip_transition."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.validation import _COMBAT_KEYWORDS, _TRANSITION_KEYWORDS_TAIL
from .base import Constraint
from .types import RepairRecord, Severity


class TransitionKeywordsConstraint(Constraint):
    """Add transition guidance to the structured clip_transition field."""

    name = "transition_keywords"
    severity = Severity.WARN
    repair_marker = "转场时长"
    fields = ("clip_transition",)

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        targets: list = list(ctx.shots or [])
        hook = ctx.hook
        if hook and isinstance(hook, dict):
            targets.append(hook)

        for i, shot in enumerate(targets):
            if not isinstance(shot, dict):
                continue
            prompt = str(shot.get("prompt") or "")
            text = " ".join([
                prompt,
                str(shot.get("motion_desc") or ""),
                str(shot.get("ff_desc") or ""),
                str(shot.get("lf_desc") or ""),
            ])
            narration = str(shot.get("narration_segment") or "")
            is_combat = any(kw in text for kw in _COMBAT_KEYWORDS) or any(
                kw in narration for kw in _COMBAT_KEYWORDS
            )
            is_closeup = "特写" in prompt or "大特写" in prompt
            is_scene_switch = False
            if i < len(targets) - 1 and isinstance(targets[i + 1], dict):
                loc1 = shot.get("location", "") or shot.get("scene", "")
                loc2 = targets[i + 1].get("location", "") or targets[i + 1].get("scene", "")
                is_scene_switch = bool(loc1 and loc2 and loc1 != loc2)
            scene_type = "打斗" if is_combat else ("特写" if is_closeup else ("场景切换" if is_scene_switch else ""))
            if not scene_type:
                continue
            current = str(shot.get("clip_transition") or "").strip()
            if "转场时长" in current:
                continue
            tail = _TRANSITION_KEYWORDS_TAIL[scene_type].lstrip("。")
            new_value = current.rstrip("。；; ") + ("；" if current else "") + tail
            shot["clip_transition"] = new_value
            repairs.append(RepairRecord(
                constraint_name=self.name,
                shot_index=None if shot is hook else shot.get("index", i),
                action="set_field",
                target="clip_transition",
                before=current,
                after=new_value,
            ))
        return repairs