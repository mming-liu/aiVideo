"""frame_layouts_required — 帧布局必填通用兜底.

通用化（v6.62.2）：R4 兜底——校验 shot.frame_layouts.ff/lf/keyframes
三块均非空（每个至少 1 个角色入镜条目）。**完全通用**——不绑小说角色名/具体帧内容。
"""
from __future__ import annotations

from .base import Constraint
from .types import RepairRecord, Severity


class FrameLayoutsRequiredConstraint(Constraint):
    """frame_layouts 必填校验（warning-only）.

    校验项：
    - frame_layouts.ff: 至少 1 条
    - frame_layouts.lf: 至少 1 条
    - frame_layouts.keyframes: 与 shot.keyframes 长度一致且每项 ≥ 1 条
    """

    name = "frame_layouts_required"
    severity = Severity.WARN
    fields = ("frame_layouts",)
    skip_hook = True

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = ctx.shots
        if not shots:
            return repairs
        for shot in shots:
            if not isinstance(shot, dict):
                continue
            idx = shot.get("index", 0)
            fl = shot.get("frame_layouts") or {}
            if not isinstance(fl, dict):
                repairs.append(RepairRecord(
                    constraint_name=self.name,
                    shot_index=idx if isinstance(idx, int) else None,
                    action="warn",
                    target="frame_layouts",
                    detail=f"shot {idx} 缺 frame_layouts 字段（应至少含 ff/lf/keyframes）。",
                ))
                continue
            # ff/lf 至少 1 条
            for sub in ("ff", "lf"):
                arr = fl.get(sub)
                if not isinstance(arr, list) or len(arr) == 0:
                    repairs.append(RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx if isinstance(idx, int) else None,
                        action="warn",
                        target=f"frame_layouts.{sub}",
                        detail=f"shot {idx} frame_layouts.{sub} 为空（应至少 1 条角色入镜条目）。",
                    ))
            # keyframes 与 shot.keyframes 长度一致且每项 ≥ 1 条
            shot_kfs = shot.get("keyframes") or []
            fl_kfs = fl.get("keyframes") or []
            if isinstance(shot_kfs, list) and len(shot_kfs) > 0:
                if not isinstance(fl_kfs, list) or len(fl_kfs) != len(shot_kfs):
                    repairs.append(RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx if isinstance(idx, int) else None,
                        action="warn",
                        target="frame_layouts.keyframes",
                        detail=(
                            f"shot {idx} frame_layouts.keyframes 长度={len(fl_kfs) if isinstance(fl_kfs, list) else 0} "
                            f"与 shot.keyframes 长度={len(shot_kfs)} 不一致（应 1:1 对齐）。"
                        ),
                    ))
        return repairs
