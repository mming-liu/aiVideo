"""W5. 多人站位纵深约束（v6.42 迁移自 _hard_fix_multi_char_layout_depth）.

扫每镜 char_layout 全横向同侧（画左/画右）无纵深 → 把"次要/背景"角色 position
改为"画中甲板/船舷"等纵深词；保留主角色画左/画右以维持空间方向。

幂等：任一角色 position 已在 _DEPTH_POSITIONS 或含前/中/后字即视为已拉开纵深.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


_DEPTH_POSITIONS = (
    "画中", "船头", "船尾", "船舷", "画中甲板",
    "画中船头", "画中船尾", "画中水面",
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
            is_boat = "船" in str(shot.get("scene", "") or "")
            depth_pos = "画中甲板" if is_boat else "画中"
            cnt = 0
            for item in layout:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role", "")).strip()
                if role not in ("次", "背景"):
                    continue
                pos = str(item.get("position", "")).strip()
                if pos in _DEPTH_POSITIONS:
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
