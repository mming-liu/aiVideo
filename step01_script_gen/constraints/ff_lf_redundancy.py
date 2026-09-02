"""K3. FF/LF/Motion 三态冗余检测（v6.51 新增）.

检测单镜内三态的内容冗余：
  1. ff_desc 与 lf_desc 高度相似（>70%）→ 说明 FF/LF 没拉开时间差
  2. motion_desc 首句与 ff_desc 高度重复 → 说明 FF 与 motion 起点重叠
  3. motion_desc 末句与 lf_desc 高度重复 → 说明 LF 与 motion 终点重叠

WARN-only：冗余不一定是 bug（静态镜 FF/LF 本来就像），只标记不硬改。
幂等：标记式，第二次跑结果不变。
"""
from __future__ import annotations

import difflib
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


# 静态镜判定词（静态镜 FF/LF 相似是正常的，不报冗余）
_STATIC_SHOT_MARKERS = (
    "空镜", "静止", "静态", "固定机位", "定格", "特写",
    "静止不动", "一动不动", "静立", "静坐", "静卧",
)


class FfLfRedundancyConstraint(Constraint):
    """K3. 三态冗余检测：FF/LF/Motion 内容重复检查."""

    name = "ff_lf_redundancy"
    severity = Severity.WARN
    fields = ("ff_desc", "lf_desc", "motion_desc")

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            ff = shot.get("ff_desc", "") or ""
            lf = shot.get("lf_desc", "") or ""
            motion = shot.get("motion_desc", "") or ""

            # 跳过静态镜（FF/LF 相似是正常的）
            if self._is_static_shot(ff, lf, motion):
                continue

            # --- 检测 0 (P1-1 字面相同硬判): FF/LF/MOT 完全相同 → HARD WARN ---
            if ff and lf and ff == lf:
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx if not is_hook else None,
                        action="warn",
                        target="ff_equals_lf",
                        before=f"FF: {ff[:40]}",
                        after=f"LF: {lf[:40]}",
                        detail="FF 与 LF 字面完全相同（拆分镜未改文案 / B3 派生副作用）",
                    )
                )
            if motion and ff and motion == ff:
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx if not is_hook else None,
                        action="warn",
                        target="motion_equals_ff",
                        before=f"FF: {ff[:40]}",
                        after=f"motion: {motion[:40]}",
                        detail="motion 与 FF 字面完全相同（拆分镜未按截取点切）",
                    )
                )
            if motion and lf and motion == lf:
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx if not is_hook else None,
                        action="warn",
                        target="motion_equals_lf",
                        before=f"LF: {lf[:40]}",
                        after=f"motion: {motion[:40]}",
                        detail="motion 与 LF 字面完全相同",
                    )
                )

            # --- 检测 1: FF vs LF 相似度 ---
            if ff and lf and len(ff) > 10 and len(lf) > 10:
                sim = difflib.SequenceMatcher(None, ff, lf).ratio()
                if sim > 0.75:
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx if not is_hook else None,
                            action="warn",
                            target="ff_vs_lf",
                            before=f"FF[:40]: {ff[:40]}",
                            after=f"LF[:40]: {lf[:40]}",
                            detail=f"FF/LF 相似度 {sim:.0%}（时间差不足）",
                        )
                    )

            # --- 检测 2: motion 首段 vs FF ---
            if motion and ff and len(motion) > 10 and len(ff) > 10:
                # 取 motion 前 1/3 作为"首段"
                motion_head = motion[: len(motion) // 3]
                sim = difflib.SequenceMatcher(None, ff, motion_head).ratio()
                if sim > 0.6:
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx if not is_hook else None,
                            action="warn",
                            target="ff_vs_motion_head",
                            before=f"FF[:40]: {ff[:40]}",
                            after=f"motion_head[:40]: {motion_head[:40]}",
                            detail=f"FF 与 motion 首段相似度 {sim:.0%}（起点重叠）",
                        )
                    )

            # --- 检测 3: motion 末段 vs LF ---
            if motion and lf and len(motion) > 10 and len(lf) > 10:
                motion_tail = motion[-(len(motion) // 3):]
                sim = difflib.SequenceMatcher(None, lf, motion_tail).ratio()
                if sim > 0.6:
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx if not is_hook else None,
                            action="warn",
                            target="lf_vs_motion_tail",
                            before=f"LF[:40]: {lf[:40]}",
                            after=f"motion_tail[:40]: {motion_tail[:40]}",
                            detail=f"LF 与 motion 末段相似度 {sim:.0%}（终点重叠）",
                        )
                    )

        return repairs

    @staticmethod
    def _is_static_shot(ff: str, lf: str, motion: str) -> bool:
        """判断是否为静态镜（静态镜 FF/LF 相似是正常的）."""
        all_text = f"{ff} {lf} {motion}"
        return any(m in all_text for m in _STATIC_SHOT_MARKERS)
