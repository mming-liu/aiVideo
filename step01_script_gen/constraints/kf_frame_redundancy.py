"""K9. kf 帧级冗余检测（2026-09-13 新增）.

ff_lf_redundancy（K3）只查 ff/lf/motion 三态，不查 kf——09-13 hook 实测盲区：
kf0（climax）与 ff 只差几个字（相似度 0.508，图片近乎复刻、i2i 连 ff 里
的悬浮匕首都抄入）、frame_layouts.kfs[1] 原样复制 kfs[0]（spec 级冗余，
文本相似度抓不到）。

检测（全部 WARN-only、只读幂等）：
  1. mid 阶段 kf（approach/climax/react）desc 与 ff_desc difflib > 0.40
     → 中间帧没拉开动作幅度，图片必复刻 ff。
     校准（chapter_001，text_sim_ratio）：问题帧 0.508，正常 mid 帧 ≤0.193，阈值居中有余量；
     pre/exit/after 豁免（≈ff/≈lf 是 v6.34 阶段语义设计）；stage 缺失视为 mid。
  2. 相邻 kf desc 两两 difflib > 0.90 → 近字面复制。
  3. spec 级：frame_layouts.kfs[i] 与 kfs[j] 全部条目 pose+position 字面相同
     → spec 复制（hook kfs[1]==kfs[0] 实测）。
静态镜豁免（与 ff_lf_redundancy 同口径）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.split_similarity import (
    KF_FF_SIM_THRESHOLD as _MID_KF_FF_SIM_THRESHOLD,
    KF_PAIR_SIM_THRESHOLD as _KF_PAIR_SIM_THRESHOLD,
    text_sim_ratio,  # 单一事实源（09-13 审查：与 vimax_bridge 共用）
)

from .base import Constraint
from .ff_lf_redundancy import _STATIC_SHOT_MARKERS  # 同口径静态镜豁免（单一事实源）
from .types import RepairRecord, Severity

# 阈值已迁入 shared/split_similarity（v6.70.1 review：单一事实源 = split_similarity，kf_frame_redundancy + vimax_bridge 共用）
_MID_STAGES = ("approach", "climax", "react")


class KfFrameRedundancyConstraint(Constraint):
    """K9. kf 帧级冗余：mid-kf≈ff / kf≈kf / spec 复制检测."""

    name = "kf_frame_redundancy"
    severity = Severity.WARN
    fields = ("keyframes", "ff_desc", "frame_layouts")

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            kfs = [k for k in (shot.get("keyframes") or []) if isinstance(k, dict)]
            if not kfs:
                continue
            ff = shot.get("ff_desc", "") or ""
            motion = shot.get("motion_desc", "") or ""
            lf = shot.get("lf_desc", "") or ""
            all_text = f"{ff} {lf} {motion}"
            if any(m in all_text for m in _STATIC_SHOT_MARKERS):
                continue  # 静态镜 kf≈ff 正常
            shot_tag = f"镜头{idx}" if not is_hook else "钩子"

            # --- 检测 1：mid 阶段 kf 与 ff 高相似 ---
            for i, kf in enumerate(kfs):
                desc = (kf.get("desc") or "").strip()
                if not desc or not ff or len(desc) <= 10:
                    continue
                stage = (kf.get("stage") or "").strip().lower()
                if stage and stage not in _MID_STAGES:
                    continue  # pre/exit/after 豁免
                sim = text_sim_ratio(desc, ff)
                if sim > _MID_KF_FF_SIM_THRESHOLD:
                    repairs.append(RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx if not is_hook else None,
                        action="warn",
                        target=f"kfs[{i}]_vs_ff",
                        before=f"kf{i}[{stage or '?'}]: {desc[:40]}",
                        after=f"ff: {ff[:40]}",
                        detail=(f"{shot_tag} kf{i} 与 ff 相似度 {sim:.0%}（mid 阶段应拉开"
                                f"动作幅度）——中间帧信息量为零，图片必复刻 ff；"
                                f"拉开姿态/角度差异或删该 kf"),
                    ))

            # --- 检测 2：相邻 kf desc 近字面复制 ---
            for i in range(len(kfs) - 1):
                a = (kfs[i].get("desc") or "").strip()
                b = (kfs[i + 1].get("desc") or "").strip()
                if not a or not b or len(a) <= 10 or len(b) <= 10:
                    continue
                sim = text_sim_ratio(a, b)
                if sim > _KF_PAIR_SIM_THRESHOLD:
                    repairs.append(RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx if not is_hook else None,
                        action="warn",
                        target=f"kfs[{i}]_vs_kfs[{i + 1}]",
                        before=f"kf{i}: {a[:40]}",
                        after=f"kf{i + 1}: {b[:40]}",
                        detail=f"{shot_tag} 相邻 kf 相似度 {sim:.0%}（近字面复制）",
                    ))

            # --- 检测 3：spec 级复制（kfs[i] 与 kfs[j] pose+position 全同）---
            kfs_spec = (shot.get("frame_layouts") or {}).get("kfs") or []
            spec_sigs: list[str] = []
            for i in range(len(kfs)):
                entries = kfs_spec[i] if i < len(kfs_spec) else []
                if not isinstance(entries, list):
                    entries = []
                sig = "|".join(
                    f"{e.get('name', '')}:{e.get('position', '')}:{e.get('pose', '')}"
                    for e in entries if isinstance(e, dict)
                )
                spec_sigs.append(sig)
            for i in range(len(spec_sigs)):
                if not spec_sigs[i]:
                    continue
                for j in range(i + 1, len(spec_sigs)):
                    if spec_sigs[i] == spec_sigs[j]:
                        repairs.append(RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx if not is_hook else None,
                            action="warn",
                            target=f"spec_kfs[{i}]_vs_kfs[{j}]",
                            before=spec_sigs[i][:60],
                            after="（字面相同）",
                            detail=(f"{shot_tag} frame_layouts.kfs[{j}] 原样复制 kfs[{i}]"
                                    f"（pose/position 全同）——spec 级冗余，两帧生图必趋同"),
                        ))
                        break
        return repairs
