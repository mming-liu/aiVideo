"""E. kf 时段缺段约束（v6.42 迁移自 _hard_fix_kf_time_window_alignment）.

motion_desc 显式分 N 段时，缺段（无 kf 覆盖）应派生补帧.
已含 kf 覆盖全部段的镜头跳过（幂等）.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.validation import _extract_scene_prefix  # OK（无循环）
from shared.kf_time import get_kf_t_sec
from .base import Constraint
from .types import RepairRecord, Severity


def _get_duration(shot: dict) -> float:
    dur = shot.get("duration_sec")
    try:
        if dur and float(dur) > 0:
            return float(dur)
    except (TypeError, ValueError):
        pass
    tc = shot.get("timecode") or {}
    try:
        start = float(tc.get("start", 0) or 0)
        end = float(tc.get("end", 0) or 0)
        if end > start:
            return end - start
    except (TypeError, ValueError):
        pass
    return 0.0


class KfTimeWindowAlignmentConstraint(Constraint):
    """E. kf 物理时刻 = motion_desc 时段铁律（派生补帧）.

    severity=WARN: 派生补帧确定性，剩余违例由 repair round 修.
    幂等：已有 kf 覆盖全部段 → 跳过.
    """

    name = "kf_time_window_alignment"
    severity = Severity.WARN
    fields = ()  # 不扫文本字段，处理的是 keyframes

    def apply(self, ctx) -> list[RepairRecord]:
        # Lazy import: validation.py 顶部 6510 行 import constraints 包，会回到
        # 本模块；本模块顶层 import validation 函数会形成循环（partially initialized）。
        from step01_script_gen.validation import (
            _parse_motion_time_segments,
            _split_motion_to_seg_texts,
        )
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            motion = (shot.get("motion_desc") or "").strip()
            if not motion:
                continue
            dur = _get_duration(shot)
            if dur <= 0:
                continue

            segs = _parse_motion_time_segments(motion, dur)
            if not segs:
                continue

            kfs = shot.get("keyframes") or []
            if not isinstance(kfs, list):
                continue

            # 收集已有 kf 物理时刻
            existing_t = []
            for kf in kfs:
                if not isinstance(kf, dict):
                    continue
                t = get_kf_t_sec(kf, shot)
                if t is not None:
                    existing_t.append(t)

            # 找缺段
            missing_segs = []
            for i, (a, b) in enumerate(segs):
                covered = any(a - 0.01 <= t <= b + 0.01 for t in existing_t)
                if not covered:
                    missing_segs.append((i, (a, b)))

            # F1.4 增强：motion_desc 无显式时段（仅 1 段 = (0, dur)），
            # 若 kfs < 1，补 1 个 after 帧
            if len(segs) < 2 and not missing_segs and len(existing_t) < 1:
                missing_segs = [(0, segs[0])]

            if not missing_segs:
                continue

            # 切分 motion_desc 为段文本
            seg_texts = _split_motion_to_seg_texts(motion, segs, dur)
            ff_text = (shot.get("ff_desc") or "").strip()
            scene_prefix = _extract_scene_prefix(ff_text)

            # 派生 kf
            for i, (a, b) in missing_segs:
                seg_text = (seg_texts[i] if (seg_texts and i < len(seg_texts)) else motion)
                if not seg_text:
                    continue
                desc = (scene_prefix + seg_text).strip("，。； ")
                t_sec = (a + b) / 2
                new_kf = {"desc": desc, "t_sec": round(t_sec, 3), "stage": "after"}
                kfs.append(new_kf)
                existing_t.append(t_sec)
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx,
                        action="set_field",
                        target=f"keyframes[{len(kfs) - 1}]",
                        before="",
                        after=f"desc={desc[:40]}..., t_sec={t_sec:.3f}, stage=after",
                    )
                )
        return repairs
