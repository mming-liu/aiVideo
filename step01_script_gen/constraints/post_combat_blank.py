"""F6. Post-combat blank-shot transition is stored in clip_transition."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity

_COMBAT_HINTS = (
    "打斗", "战斗", "格斗", "刺杀", "刺入", "中刀", "中剑",
    "挥刀", "挥剑", "挥砍", "砍杀", "挥拳", "搏斗", "交手",
    "追逐", "追击", "奔驰", "奔逃", "疾奔", "疾驰", "狂奔", "飞驰", "冲出",
    "破水", "跃上", "猛刺", "扑出", "翻身跃",
)
_BLANK_SHOT_HINTS = (
    "空镜", "空无一人", "大远景", "极光", "月夜", "远山", "远景", "水墨", "山水",
    "残阳", "夕照", "落日", "星夜", "夜色", "叠化", "空无", "无人", "无人物",
    "无边的", "天地间", "苍穹", "云海",
)


def _has_combat(text: str) -> bool:
    return any(w in text for w in _COMBAT_HINTS)


def _has_blank_shot(text: str) -> bool:
    return any(w in text for w in _BLANK_SHOT_HINTS)


class PostCombatBlankConstraint(Constraint):
    """Add a post-combat blank-shot transition to clip_transition only."""

    name = "post_combat_blank"
    severity = Severity.WARN
    skip_hook = True
    fields = ("clip_transition",)

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        if not isinstance(ctx.shots, list):
            return repairs
        for i, shot in enumerate(ctx.shots):
            if not isinstance(shot, dict):
                continue
            text = " ".join(str(shot.get(f) or "") for f in ("prompt", "motion_desc", "ff_desc", "lf_desc"))
            if not _has_combat(text):
                continue
            if i + 1 >= len(ctx.shots):
                continue
            next_shot = ctx.shots[i + 1]
            if not isinstance(next_shot, dict):
                continue
            next_text = " ".join(str(next_shot.get(f) or "") for f in ("prompt", "motion_desc", "ff_desc", "lf_desc"))
            if _has_blank_shot(next_text):
                continue
            current = str(next_shot.get("clip_transition") or "").strip()
            if "叠化" in current:
                continue
            new_value = current.rstrip("。.，,;； ") + ("；" if current else "") + "叠化，0.5s"
            next_shot["clip_transition"] = new_value
            repairs.append(RepairRecord(
                constraint_name=self.name,
                shot_index=next_shot.get("index"),
                action="set_field",
                target="clip_transition",
                before=current,
                after=new_value,
            ))
        return repairs