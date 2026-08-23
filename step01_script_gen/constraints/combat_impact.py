"""W3. 打斗/刺杀场景打击反馈约束（v6.42 迁移自 _hard_fix_combat_impact）.

扫打斗/刺杀/追逐/搏斗镜（_COMBAT_KEYWORDS 双通道），prompt 缺 _IMPACT_WORDS
任一词 → 追加 _IMPACT_BY_SCENE 按场景类型选的反馈短语。

场景类型：水上 / 陆地 / 夜 / 打斗 / 刺杀.

幂等：已含 _IMPACT_WORDS 任一词跳过。
"""
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

_IMPACT_BY_SCENE: dict[str, str] = {
    "水上": "水花四溅，水波激荡",
    "陆地": "衣袂翻飞，碎屑飞溅",
    "夜": "暗处残影，衣袂翻飞",
    "打斗": "冲击波，衣袂翻飞，动态模糊",
    "刺杀": "水花四溅，动态模糊",
}


def _detect_scene_type(shot: dict) -> str:
    """判定打斗镜场景类型（5 类）. 优先级：刺杀 > 水上 > 夜 > 陆地 > 打斗."""
    text = " ".join(
        [
            shot.get("prompt", "") or "",
            shot.get("motion_desc", "") or "",
            shot.get("ff_desc", "") or "",
            shot.get("lf_desc", "") or "",
        ]
    )
    narr = shot.get("narration_segment", "") or ""
    loc = shot.get("location", "") or shot.get("scene", "") or ""
    full = text + " " + narr + " " + loc
    # 刺杀（最高优先）
    if "刺" in full or "匕首" in full or "下刺" in full:
        return "刺杀"
    # 水上
    if any(w in full for w in ("水", "湖", "河", "湾", "舟", "船")):
        return "水上"
    # 夜
    if "夜" in full or "暗" in full:
        return "夜"
    return "打斗"


def _is_combat(shot: dict) -> bool:
    """双通道判定（prompt/motion/ff/lf + narration）含 _COMBAT_KEYWORDS."""
    text = " ".join(
        [
            shot.get("prompt", "") or "",
            shot.get("motion_desc", "") or "",
            shot.get("ff_desc", "") or "",
            shot.get("lf_desc", "") or "",
        ]
    )
    narr = shot.get("narration_segment", "") or ""
    return (
        any(kw in text for kw in _COMBAT_KEYWORDS)
        or any(kw in narr for kw in _COMBAT_KEYWORDS)
    )


class CombatImpactConstraint(Constraint):
    """W3. 打斗/刺杀场景打击反馈：缺"水花/冲击/动态模糊" → 按场景类型追加.

    severity=WARN.
    repair_marker="动态模糊": 已含任一 _IMPACT_WORDS 视为已修.
    """

    name = "combat_impact"
    severity = Severity.WARN
    repair_marker = "动态模糊"  # 通用锚词；实际检测是 _IMPACT_WORDS 任一
    fields = ("prompt",)

    def _already_repaired(self, shot: dict) -> bool:
        prompt = shot.get("prompt", "") or ""
        if not prompt:
            return False
        return any(w in prompt for w in _IMPACT_WORDS)

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            if not _is_combat(shot):
                continue
            prompt = shot.get("prompt", "") or ""
            if any(w in prompt for w in _IMPACT_WORDS):
                continue
            scene_type = _detect_scene_type(shot)
            impact = _IMPACT_BY_SCENE.get(scene_type, _IMPACT_BY_SCENE["打斗"])
            before = prompt
            shot["prompt"] = prompt.rstrip("。") + f"。{impact}"
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=idx,
                    action="append",
                    target="prompt",
                    before=before,
                    after=shot["prompt"],
                )
            )
        return repairs
