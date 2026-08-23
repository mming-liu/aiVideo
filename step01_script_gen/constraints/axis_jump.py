"""W11. 180° 轴线越轴约束（v6.42 迁移自 _hard_fix_axis_jump）.

8-22 跑批实测：shot 5→6 同场景「画右」→「画左」朝向翻转报"轴线越轴"。

修法：扫同场景相邻镜方向词冲突（画左↔画右），把后镜 char_layout 第一角色
position 强制改为前镜同侧（保持轴线一致），并把后镜 prompt 中"画左"或"画右"
字样替换为前镜方向。

豁免：插空镜 / 场景切换。
幂等：方向一致时跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.validation import _extract_shot_directions
from ._base_classes import InterShotConstraint
from .types import RepairRecord, Severity


def _is_scene_switch(s1: dict, s2: dict) -> bool:
    loc1 = s1.get("location", "") or s1.get("scene", "")
    loc2 = s2.get("location", "") or s2.get("scene", "")
    return bool(loc1 and loc2 and loc1 != loc2)


def _has_only_direction(directions: list[str], target: str) -> bool:
    return set(directions) == {target}


class AxisJumpConstraint(InterShotConstraint):
    """W11. 180° 轴线越轴：同场景画左↔画右 → 后镜统一方向.

    severity=HARD: 轴线越轴是结构性错误（structural，180° flip 导致角色左右颠倒）.
    repair_marker=None: 幂等由"再次扫描方向一致"保证.
    """

    name = "axis_jump"
    severity = Severity.HARD
    skip_hook = True

    def _check_pair(self, prev, curr, ctx, prev_idx, curr_idx):
        if not isinstance(prev, dict) or not isinstance(curr, dict):
            return None
        # 场景切换豁免
        if _is_scene_switch(prev, curr):
            return None
        # 方向判定
        t1 = " ".join([
            prev.get("prompt", "") or "",
            prev.get("ff_desc", "") or "",
        ])
        t2 = " ".join([
            curr.get("prompt", "") or "",
            curr.get("ff_desc", "") or "",
        ])
        d1 = _extract_shot_directions(t1)
        d2 = _extract_shot_directions(t2)
        if not d1 or not d2:
            return None
        # 冲突：画左↔画右
        before = curr.get("prompt", "") or ""
        if _has_only_direction(d1, "画左") and _has_only_direction(d2, "画右"):
            new_prompt = before.replace("画右", "画左")
            if new_prompt != before:
                curr["prompt"] = new_prompt
            # 同步 char_layout
            for item in (curr.get("char_layout") or []):
                if not isinstance(item, dict):
                    continue
                pos = str(item.get("position", "")).strip()
                if "画右" in pos:
                    item["position"] = pos.replace("画右", "画左")
            return RepairRecord(
                constraint_name=self.name,
                shot_index=curr_idx,
                action="rewrite",
                target="prompt",
                before=before,
                after=curr.get("prompt", before),
            )
        if _has_only_direction(d1, "画右") and _has_only_direction(d2, "画左"):
            new_prompt = before.replace("画左", "画右")
            if new_prompt != before:
                curr["prompt"] = new_prompt
            for item in (curr.get("char_layout") or []):
                if not isinstance(item, dict):
                    continue
                pos = str(item.get("position", "")).strip()
                if "画左" in pos:
                    item["position"] = pos.replace("画左", "画右")
            return RepairRecord(
                constraint_name=self.name,
                shot_index=curr_idx,
                action="rewrite",
                target="prompt",
                before=before,
                after=curr.get("prompt", before),
            )
        return None
