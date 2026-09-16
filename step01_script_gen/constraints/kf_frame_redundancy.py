"""K9. kf frame redundancy detection (warning-only, safe)."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.split_similarity import (
    KF_FF_SIM_THRESHOLD as _MID_KF_FF_SIM_THRESHOLD,
    KF_PAIR_SIM_THRESHOLD as _KF_PAIR_SIM_THRESHOLD,
    text_sim_ratio,
)
from .base import Constraint
from .ff_lf_redundancy import _STATIC_SHOT_MARKERS
from .types import RepairRecord, Severity

_MID_STAGES = ("approach", "climax", "react")


class KfFrameRedundancyConstraint(Constraint):
    """Detect kf redundancy without deleting frames that carry time coverage."""

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
                continue
            shot_tag = f"镜头{idx}" if not is_hook else "钩子"

            for i, kf in enumerate(kfs):
                desc = (kf.get("desc") or "").strip()
                if not desc or not ff or len(desc) <= 10:
                    continue
                stage = (kf.get("stage") or "").strip().lower()
                if stage and stage not in _MID_STAGES:
                    continue
                sim = text_sim_ratio(desc, ff)
                if sim > _MID_KF_FF_SIM_THRESHOLD:
                    repairs.append(RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx if not is_hook else None,
                        action="warn",
                        target=f"kfs[{i}]_vs_ff",
                        before=f"kf{i}[{stage or '?'}]: {desc[:40]}",
                        after=f"ff: {ff[:40]}",
                        detail=(f"{shot_tag} kf{i} 与 ff 相似度 {sim:.0%}; "
                                "拉开姿态/角度或下一轮重生（否则图片必复刻 ff）"),
                    ))

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