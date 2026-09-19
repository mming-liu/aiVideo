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
from .types import RepairRecord, Severity, Violation


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
    severity = Severity.WARN
    skip_hook = True

    def _check_pair(self, prev, curr, ctx, prev_idx, curr_idx):
        if not isinstance(prev, dict) or not isinstance(curr, dict):
            return None
        if _is_scene_switch(prev, curr):
            return None
        t1 = " ".join([prev.get("prompt", "") or "", prev.get("ff_desc", "") or ""])
        t2 = " ".join([curr.get("prompt", "") or "", curr.get("ff_desc", "") or ""])
        d1 = _extract_shot_directions(t1)
        d2 = _extract_shot_directions(t2)
        if not d1 or not d2:
            return None
        location = str(curr.get("location", "") or curr.get("scene", "") or
                       prev.get("location", "") or prev.get("scene", "") or "")
        conflict = None
        anchor_pair = _get_horizontal_anchor_pair(location)
        if anchor_pair:
            a_first, a_last = anchor_pair
            if _has_only_direction(d1, a_first) and _has_only_direction(d2, a_last):
                conflict = (a_first, a_last)
            elif _has_only_direction(d1, a_last) and _has_only_direction(d2, a_first):
                conflict = (a_last, a_first)
        if conflict is None and _has_only_direction(d1, "画左") and _has_only_direction(d2, "画右"):
            conflict = ("画左", "画右")
        if conflict is None and _has_only_direction(d1, "画右") and _has_only_direction(d2, "画左"):
            conflict = ("画右", "画左")
        if conflict is None:
            return None
        ctx.record_violation(Violation(
            constraint_name=self.name,
            severity=Severity.WARN,
            shot_index=curr.get("index", curr_idx),
            field="prompt,char_layout",
            message=(
                f"同场景相邻镜方向由 {conflict[0]} 翻转为 {conflict[1]}，"
                "但缺少机位/轴线切换证据；保留原文，禁止自动镜像站位。"
            ),
            evidence=f"prev={t1[-120:]}; curr={t2[-120:]}",
            repaired=False,
        ))
        return RepairRecord(
            constraint_name=self.name,
            shot_index=curr.get("index", curr_idx),
            action="warn",
            target="prompt,char_layout",
            before=f"{conflict[0]}→{conflict[1]}",
            after=f"{conflict[0]}→{conflict[1]}",
            detail="方向翻转缺乏机位证据，保留原文交由 audit",
        )
