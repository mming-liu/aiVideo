"""W4. 转场关键词约束（v6.42 迁移自 _hard_fix_transition_keywords）.

打斗/特写/场景切换镜的 prompt 缺"转场时长/动作连贯/快速跟拍"等词 →
追加 _TRANSITION_KEYWORDS_TAIL 对应短语。

幂等：已含"转场时长"或对应关键词 → 跳过。
"""
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
    """W4. 转场关键词：打斗/特写/场景切换镜缺转场词 → 追加 tail.

    severity=WARN: 转场词可由后续 repair round 修.
    repair_marker="转场时长".
    """

    name = "transition_keywords"
    severity = Severity.WARN
    repair_marker = "转场时长"
    fields = ("prompt",)

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        # 构造 targets 列表（shots + hook），与旧实现保持一致
        targets: list = list(ctx.shots or [])
        hook = ctx.hook
        if hook and isinstance(hook, dict):
            targets.append(hook)

        for i, s in enumerate(targets):
            if not isinstance(s, dict):
                continue
            prompt = s.get("prompt", "") or ""
            text = " ".join([
                prompt,
                s.get("motion_desc", "") or "",
                s.get("ff_desc", "") or "",
                s.get("lf_desc", "") or "",
            ])
            narr = s.get("narration_segment", "") or ""
            is_combat = any(kw in text for kw in _COMBAT_KEYWORDS) or any(
                kw in narr for kw in _COMBAT_KEYWORDS
            )
            is_closeup = "特写" in prompt or "大特写" in prompt
            is_scene_switch = False
            if i < len(targets) - 1 and isinstance(targets[i + 1], dict):
                loc1 = s.get("location", "") or s.get("scene", "")
                loc2 = targets[i + 1].get("location", "") or targets[i + 1].get("scene", "")
                is_scene_switch = bool(loc1 and loc2 and loc1 != loc2)
            scene_type = None
            if is_combat:
                scene_type = "打斗"
            elif is_closeup:
                scene_type = "特写"
            elif is_scene_switch:
                scene_type = "场景切换"
            if not scene_type:
                continue
            if "转场时长" in prompt or "动作连贯" in prompt or "快速跟拍" in prompt:
                continue
            before = prompt
            if scene_type == "打斗":
                s["prompt"] = prompt.rstrip("。") + _TRANSITION_KEYWORDS_TAIL[scene_type]
            elif scene_type == "特写" and "转场" not in prompt:
                s["prompt"] = prompt.rstrip("。") + _TRANSITION_KEYWORDS_TAIL[scene_type]
            elif scene_type == "场景切换" and "淡入" not in prompt and "淡出" not in prompt:
                s["prompt"] = prompt.rstrip("。") + _TRANSITION_KEYWORDS_TAIL[scene_type]
            if s["prompt"] != before:
                idx = s.get("index", i) if s is not hook else None
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx,
                        action="append",
                        target="prompt",
                        before=before,
                        after=s["prompt"],
                    )
                )
        return repairs
