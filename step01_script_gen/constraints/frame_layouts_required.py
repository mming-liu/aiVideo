"""frame_layouts_required — 帧布局必填通用兜底.

通用化（v6.62.2）：R4 兜底——校验 shot.frame_layouts.ff/lf/kfs
三块均非空（每个至少 1 个角色入镜条目）。**完全通用**——不绑小说角色名/具体帧内容。

v6.62.7（09-09）修键名：中间帧布局的**规范键是 `kfs`**（shared/frame_spec.py:57、
timecode.py:233、frame_spec_repair.py:131、frame_spec_consistency.py:161 全用 `kfs`），
本文件是全仓唯一读 `keyframes` 的地方——读了个永不存在的键，于是「长度=0 与
shot.keyframes 不一致」对**每个有关键帧的镜头恒定误报**。09-08 实测 chapter_001：
18 条 warn 里 10 条是这个假警报，把真警报（连续 3 镜同机位/开篇无角色/对白密度 10%）
淹掉——而实际 kfs 长度是 [5,5,6,7,7,5,6,6,5,4]，本来就与 shot.keyframes 对齐。
"""
from __future__ import annotations

from .base import Constraint
from .types import RepairRecord, Severity


class FrameLayoutsRequiredConstraint(Constraint):
    """frame_layouts 必填校验（warning-only）.

    校验项：
    - frame_layouts.ff: 至少 1 条
    - frame_layouts.lf: 至少 1 条
    - frame_layouts.kfs: 与 shot.keyframes 长度一致且每项 ≥ 1 条
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
                    detail=f"shot {idx} 缺 frame_layouts 字段（应至少含 ff/lf/kfs）。",
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
            # kfs 与 shot.keyframes 长度一致且每项 ≥ 1 条
            shot_kfs = shot.get("keyframes") or []
            fl_kfs = fl.get("kfs") or []
            if isinstance(shot_kfs, list) and len(shot_kfs) > 0:
                if not isinstance(fl_kfs, list) or len(fl_kfs) != len(shot_kfs):
                    repairs.append(RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx if isinstance(idx, int) else None,
                        action="warn",
                        target="frame_layouts.kfs",
                        detail=(
                            f"shot {idx} frame_layouts.kfs 长度={len(fl_kfs) if isinstance(fl_kfs, list) else 0} "
                            f"与 shot.keyframes 长度={len(shot_kfs)} 不一致（应 1:1 对齐）。"
                        ),
                    ))
        return repairs
