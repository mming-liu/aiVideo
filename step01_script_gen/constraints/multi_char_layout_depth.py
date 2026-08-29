"""W5. 多人站位纵深约束（v6.42 迁移自 _hard_fix_multi_char_layout_depth）.

扫每镜 char_layout 全横向同侧（画左/画右）无纵深 → 把"次要/背景"角色 position
改为"画中甲板/船舷"等纵深词；保留主角色画左/画右以维持空间方向。

depth_pos 不再写死「画中甲板/船舷」，改为从 scene_anchors
按 location 动态选——
  - 横向场景（船/桥/街道/马车）：取 anchor 第 2 个（如 船中/桥中/街中）
  - 围合/抽象场景：画中/前景/后景

幂等：任一角色 position 已在 _DEPTH_POSITIONS 或含前/中/后字即视为已拉开纵深.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.scene_anchors import (
    is_horizontal_scene, scene_anchors_for,
)
from .base import Constraint
from .types import RepairRecord, Severity


# 深度词：从 scene_anchors BASE_POS_WORDS 派生（只取"画中/前景/后景/中景"4 个
# 纵深词；"画左/画右/画上/画下" 是横向方位词，不算纵深）+ SCENE_ANCHORS
# v6.43 重构：纵深位置由 scene_anchors 统一管理，新场景自动跟随
from step01_script_gen.scene_anchors import BASE_POS_WORDS, scene_anchors_for

_DEPTH_POSITIONS: tuple[str, ...] = (
    "画中", "前景", "后景", "中景",  # 4 个纵深词
    "画中甲板", "画中船头", "画中船尾", "画中水面",
    "画中前", "画中后",
    *scene_anchors_for("船"),  # 船头/船中/船尾/船舱/...
)


def _has_depth(positions: list[str]) -> bool:
    """任一 position 在 _DEPTH_POSITIONS 或含前/中/后字."""
    for p in positions:
        if not p:
            continue
        if p in _DEPTH_POSITIONS:
            return True
        if "前" in p or "中" in p or "后" in p:
            return True
    return False


def _all_lateral(positions: list[str]) -> bool:
    """所有 position 都是画左/画右."""
    return all(p in ("画左", "画右") for p in positions) and bool(positions)


def _choose_depth_pos(location: str) -> str:
    """按 location 选深度词.

    - 横向场景（船/桥/街道/马车）：取 anchor 第 2 个（如 船中/桥中/街中）—— 横向天然纵深
    - 围合/抽象场景：画中（基础深度词）
    """
    if is_horizontal_scene(location):
        anchors = scene_anchors_for(location)
        if len(anchors) >= 2:
            return anchors[1]  # 第 2 个 anchor（如 船中 / 桥中 / 街中 / 车辕）
    return "画中"


class MultiCharLayoutDepthConstraint(Constraint):
    """W5. 多人站位纵深：全同侧 → 次要角色改"画中甲板/船舷".

    severity=WARN: 站位纵深可由后续 repair round 修.
    repair_marker=None: 幂等由"再次扫描发现 has_depth=True"保证.
    """

    name = "multi_char_layout_depth"
    severity = Severity.WARN
    skip_hook = True

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            layout = shot.get("char_layout")
            if not isinstance(layout, list) or len(layout) < 2:
                continue
            positions = [
                str(item.get("position", "")).strip()
                for item in layout
                if isinstance(item, dict)
            ]
            if not positions:
                continue
            if not _all_lateral(positions):
                continue
            if _has_depth(positions):
                continue
            # 全横向同侧：把 role=次/背景 改纵深
            main_side = positions[0]  # 取第一个保持同侧
            # 按 location 选 depth_pos（不再写死"画中甲板"）
            location = str(shot.get("location", "") or shot.get("scene", "") or "")
            depth_pos = _choose_depth_pos(location)
            cnt = 0
            # v6.49（08-28）扩展：若所有角色都没标 role=次/背景（如 LLM 全写"主"或空），
            # 取第 2/3/... 个角色强制改纵深，避免「2 角色全画左」漏修（镜3 实测）。
            has_secondary = any(
                isinstance(it, dict) and str(it.get("role", "")).strip() in ("次", "背景")
                for it in layout
            )
            seen_sides: list[str] = []
            for item in layout:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role", "")).strip()
                pos = str(item.get("position", "")).strip()
                if pos in _DEPTH_POSITIONS:
                    continue
                # 判定「需要改」：role 是次/背景 OR（无次/背景角色 + 本角色非首个）
                is_target = (
                    role in ("次", "背景")
                    or (
                        not has_secondary
                        and pos in ("画左", "画右")
                        and pos in seen_sides
                    )
                )
                if not is_target:
                    seen_sides.append(pos)
                    continue
                new_pos = (
                    f"{main_side}{depth_pos}"
                    if cnt == 0
                    else f"{main_side}{depth_pos}{cnt + 1}"
                )
                if pos != new_pos and new_pos not in pos:
                    before = pos
                    item["position"] = new_pos
                    cnt += 1
                    # 记录一次修复（即使是改 dict 子字段也按 shot 维度上报）
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx,
                            action="set_field",
                            target=f"char_layout[{positions.index(pos) if pos in positions else '?'}].position",
                            before=before,
                            after=new_pos,
                        )
                    )
        return repairs
