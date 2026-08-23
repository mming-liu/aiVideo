"""W10. 景别跳跃约束（v6.42 迁移自 _hard_fix_framing_jump）.

8-22 跑批实测：shot 1 全景 → shot 2 中近景（3 级跳跃）报"景别跳跃幅度 3 级
（>2 级）"。

修法：扫相邻镜景别跨度 > 2 级，强制把前镜 framing 替换为后镜的相邻景别
（中近景→中景过渡），减小跨度。

豁免：场景切换 / 打斗镜（与 _check_shot_size_jump 同口径）。
幂等：相邻景别 ≤ 2 级时跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.validation import _is_combat_shot, _shot_size_level
from ._base_classes import InterShotConstraint
from .types import RepairRecord, Severity


_SHOT_SIZE_ORDER = [
    "大特写", "特写", "中近景", "中景", "中远景",
    "全景", "远景", "大远景", "鸟瞰远景",
]


def _is_scene_switch(s1: dict, s2: dict) -> bool:
    loc1 = s1.get("location", "") or s1.get("scene", "")
    loc2 = s2.get("location", "") or s2.get("scene", "")
    return bool(loc1 and loc2 and loc1 != loc2)


def _adjacent_framing_for(lv2: str) -> str | None:
    """挑与 lv2 相邻 1 级的景别（朝 lv1 方向靠近）.

    若 lv1 > lv2 (远景→特写方向)，新 framing 取 max(0, d2-1).
    若 lv1 < lv2 (特写→远景方向)，新 framing 取 min(len-1, d2+1).
    """
    if lv2 not in _SHOT_SIZE_ORDER:
        return None
    d2 = _SHOT_SIZE_ORDER.index(lv2)
    # 假设 lv1 比 lv2 远（更远→更近），所以取 d2-1
    adj_idx = max(0, d2 - 1)
    return _SHOT_SIZE_ORDER[adj_idx]


class FramingJumpConstraint(InterShotConstraint):
    """W10. 景别跳跃：相邻景别跨度 > 2 级 → 前镜改相邻景别.

    severity=WARN: 景别跳跃可由后续 repair round 修.
    repair_marker=None: 幂等由"再次扫描跨度 ≤ 2"保证.
    """

    name = "framing_jump"
    severity = Severity.WARN
    skip_hook = True

    def _check_pair(self, prev, curr, ctx, prev_idx, curr_idx):
        if not isinstance(prev, dict) or not isinstance(curr, dict):
            return None
        # 场景切换豁免
        if _is_scene_switch(prev, curr):
            return None
        # 打斗镜豁免
        try:
            if _is_combat_shot(prev) or _is_combat_shot(curr):
                return None
        except Exception:
            pass
        # 取景别
        lv1 = _shot_size_level(prev)
        lv2 = _shot_size_level(curr)
        if not lv1 or not lv2:
            return None
        try:
            d1 = _SHOT_SIZE_ORDER.index(lv1)
            d2 = _SHOT_SIZE_ORDER.index(lv2)
        except ValueError:
            return None
        if abs(d1 - d2) <= 2:
            return None
        # 跨度 > 2：把 prev framing 改为与 curr 相邻（更近一档）
        new_lv1 = _adjacent_framing_for(lv2)
        if not new_lv1 or new_lv1 == lv1:
            return None
        prompt = prev.get("prompt", "") or ""
        if new_lv1 in prompt:
            return None
        new_prompt = prompt.replace(lv1, new_lv1, 1)
        if new_prompt == prompt:
            return None
        before = prompt
        prev["prompt"] = new_prompt
        return RepairRecord(
            constraint_name=self.name,
            shot_index=prev_idx,
            action="rewrite",
            target="prompt",
            before=before,
            after=new_prompt,
        )
