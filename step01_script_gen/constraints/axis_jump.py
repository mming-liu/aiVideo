"""W11. 180° 轴线越轴约束（v6.42 迁移自 _hard_fix_axis_jump）.

8-22 跑批实测：shot 5→6 同场景「画右」→「画左」朝向翻转报"轴线越轴"。

修法：扫同场景相邻镜方向词冲突（画左↔画右），把后镜 char_layout 第一角色
position 强制改为前镜同侧（保持轴线一致），并把后镜 prompt 中"画左"或"画右"
字样替换为前镜方向。

横向场景（船/桥/街道）用 scene_anchors 的「首尾 anchor 对」做翻转
（船头↔船尾 / 桥头↔桥尾 / 街口↔巷尾）——不再写死"画左/画右"。

豁免：插空镜 / 场景切换。
幂等：方向一致时跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.scene_anchors import (
    is_horizontal_scene, scene_anchors_for, get_scene_type,
)
from step01_script_gen.validation import _extract_shot_directions
from ._base_classes import InterShotConstraint
from .types import RepairRecord, Severity


def _is_scene_switch(s1: dict, s2: dict) -> bool:
    loc1 = s1.get("location", "") or s1.get("scene", "")
    loc2 = s2.get("location", "") or s2.get("scene", "")
    return bool(loc1 and loc2 and loc1 != loc2)


def _has_only_direction(directions: list[str], target: str) -> bool:
    return set(directions) == {target}


def _get_horizontal_anchor_pair(location: str) -> tuple[str, str] | None:
    """横向场景下，返回「首尾 anchor 对」（船头↔船尾 / 桥头↔桥尾 / 街口↔巷尾）.

    横向场景 anchor 字典按"前后关系"排序（船头→船中→船尾；桥头→桥中→桥尾；
    街口→街中→巷尾），取首尾 2 个作为「轴线两端」.
    """
    anchors = scene_anchors_for(location)
    if not anchors:
        return None
    # 找「前/后」语义最清晰的一对：扫描 anchor 列表挑两个最像"前/后"的
    # 简化策略：取第 1 个和最后 1 个（船头↔船尾、桥头↔桥尾、街口↔巷尾）
    # 例外：马车 anchors=("车头","车辕","车板","车尾",...)，第1=车头 最后=车尾
    if len(anchors) >= 2:
        return (anchors[0], anchors[-1])
    return None


class AxisJumpConstraint(InterShotConstraint):
    """W11. 180° 轴线越轴：同场景方向翻转 → 后镜统一方向.

    通用翻转：
      - 围合/抽象场景：画左 ↔ 画右
      - 横向场景（船/桥/街道/马车）：anchor 首 ↔ anchor 尾
        （船头↔船尾 / 桥头↔桥尾 / 街口↔巷尾 / 车头↔车尾）

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
        # 公共 location（取 curr 的；prev/curr 场景未切换时一致）
        location = str(curr.get("location", "") or curr.get("scene", "") or
                       prev.get("location", "") or prev.get("scene", "") or "")
        before = curr.get("prompt", "") or ""
        # 1) 横向场景：anchor 首尾翻转
        anchor_pair = _get_horizontal_anchor_pair(location)
        if anchor_pair:
            a_first, a_last = anchor_pair
            # 翻 1：a_first → a_last（后镜从前端翻到末端）
            if _has_only_direction(d1, a_first) and _has_only_direction(d2, a_last):
                new_prompt = before.replace(a_last, a_first)
                if new_prompt != before:
                    curr["prompt"] = new_prompt
                for item in (curr.get("char_layout") or []):
                    if not isinstance(item, dict):
                        continue
                    pos = str(item.get("position", "")).strip()
                    if a_last in pos:
                        item["position"] = pos.replace(a_last, a_first)
                return RepairRecord(
                    constraint_name=self.name,
                    shot_index=curr_idx,
                    action="rewrite",
                    target="prompt",
                    before=before,
                    after=curr.get("prompt", before),
                )
            # 翻 2：a_last → a_first
            if _has_only_direction(d1, a_last) and _has_only_direction(d2, a_first):
                new_prompt = before.replace(a_first, a_last)
                if new_prompt != before:
                    curr["prompt"] = new_prompt
                for item in (curr.get("char_layout") or []):
                    if not isinstance(item, dict):
                        continue
                    pos = str(item.get("position", "")).strip()
                    if a_first in pos:
                        item["position"] = pos.replace(a_first, a_last)
                return RepairRecord(
                    constraint_name=self.name,
                    shot_index=curr_idx,
                    action="rewrite",
                    target="prompt",
                    before=before,
                    after=curr.get("prompt", before),
                )
        # 2) 通用：画左 ↔ 画右
        if _has_only_direction(d1, "画左") and _has_only_direction(d2, "画右"):
            new_prompt = before.replace("画右", "画左")
            if new_prompt != before:
                curr["prompt"] = new_prompt
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
