"""W3. Combat impact is stored in fx_effect, not the image prompt."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.validation import _COMBAT_KEYWORDS
from .base import Constraint
from .types import RepairRecord, Severity

_IMPACT_WORDS = (
    "水花四溅", "水花", "冲击波", "冲击", "火星四溅", "火星",
    "碎屑飞溅", "飞溅", "衣袂翻飞", "衣袂", "动态模糊", "残影",
    "血雾", "血溅", "浪花翻涌", "水波炸开", "水波激荡",
)
_IMPACT_BY_SCENE = {
    "水上": "水花四溅，水波激荡",
    "陆地": "衣袂翻飞，碎屑飞溅",
    "夜": "暗处残影，衣袂翻飞",
    "打斗": "冲击波，衣袂翻飞，动态模糊",
    "刺杀": "水花四溅，动态模糊",
}


def _detect_scene_type(shot: dict) -> str:
    text = " ".join(str(shot.get(f) or "") for f in ("prompt", "motion_desc", "ff_desc", "lf_desc"))
    narr = str(shot.get("narration_segment") or "")
    loc = str(shot.get("location") or shot.get("scene") or "")
    full = text + " " + narr + " " + loc
    if "刺" in full or "匕首" in full or "下刺" in full:
        return "刺杀"
    if any(w in full for w in ("水", "湖", "河", "湾", "舟", "船")):
        return "水上"
    if "夜" in full or "暗" in full:
        return "夜"
    return "打斗"


def _is_combat(shot: dict) -> bool:
    text = " ".join(str(shot.get(f) or "") for f in ("prompt", "motion_desc", "ff_desc", "lf_desc"))
    narr = str(shot.get("narration_segment") or "")
    return any(kw in text for kw in _COMBAT_KEYWORDS) or any(kw in narr for kw in _COMBAT_KEYWORDS)


class CombatImpactConstraint(Constraint):
    """Add impact feedback to fx_effect only."""

    name = "combat_impact"
    severity = Severity.WARN
    repair_marker = "动态模糊"
    fields = ("fx_effect",)

    def _already_repaired(self, shot: dict) -> bool:
        fx = str(shot.get("fx_effect") or "")
        return bool(fx) and any(w in fx for w in _IMPACT_WORDS)

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict) or not _is_combat(shot):
                continue
            fx = str(shot.get("fx_effect") or "").strip()
            if any(w in fx for w in _IMPACT_WORDS):
                continue
            impact = _IMPACT_BY_SCENE.get(_detect_scene_type(shot), _IMPACT_BY_SCENE["打斗"])
            new_fx = fx.rstrip("。.，,;； ") + ("；" if fx else "") + impact
            shot["fx_effect"] = new_fx
            repairs.append(RepairRecord(
                constraint_name=self.name,
                shot_index=idx,
                action="set_field",
                target="fx_effect",
                before=fx,
                after=new_fx,
            ))
        return repairs