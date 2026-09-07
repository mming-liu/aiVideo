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

            # v6.62（09-06）：先修越窗——kf 物理时刻不在任一段内 → 拉到最近
            # 段中点（确定性，幂等：修完在窗内不再触发）。09-05 实测 12/13 镜
            # 存在越窗 kf（t_sec > duration_sec / t 落段外），检查器每轮都报
            # 「N 个 kf 物理时刻不在时段内」但没有任何修复层管。
            # 拉回目标优先「未被占用的段」：若最近段的 kf 已 ≥2 帧，改拉第二近
            # 段——避免拉完后同段堆 2 帧而缺段判定又补 1 帧（重复 3 帧同段）。
            existing_t = [
                t for kf in kfs if isinstance(kf, dict)
                for t in [get_kf_t_sec(kf, shot)] if t is not None
            ]

            def _seg_load(a: float, b: float) -> int:
                return sum(1 for t0 in existing_t if a - 0.01 <= t0 <= b + 0.01)

            orphans_fixed = 0
            for kf in kfs:
                if not isinstance(kf, dict):
                    continue
                t = get_kf_t_sec(kf, shot)
                if t is None:
                    continue
                if any(a - 0.01 <= t <= b + 0.01 for a, b in segs):
                    continue
                # 按距中点排序取段：优先「时间最近且未占用」的段
                ranked = sorted(segs, key=lambda ab: abs((ab[0] + ab[1]) / 2 - t))
                best = ranked[0]
                for cand in ranked:
                    if _seg_load(*cand) < 1:
                        best = cand
                        break
                mid = round((best[0] + best[1]) / 2, 3)
                old_t = kf.get("t_sec")
                kf["t_sec"] = mid
                if kf.get("time_ratio") is not None:
                    kf.pop("time_ratio", None)  # 直写秒数，双字段并存会被旧 reader 读错
                orphans_fixed += 1
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx,
                        action="clamp",
                        target="keyframes[*].t_sec",
                        before=str(old_t),
                        after=str(mid),
                    )
                )
            if orphans_fixed:
                # 修完后重算 existing_t
                existing_t = [
                    t for kf in kfs if isinstance(kf, dict)
                    for t in [get_kf_t_sec(kf, shot)] if t is not None
                ]

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
                    # v6.62.1（09-06）：implicit 尾段无 motion 文本 → 回退最后
                    # 非空段文本 + 延续后缀（与 validation._hard_fix_kf_time_
                    # window_alignment 同口径；否则缺段永修不掉，检查器每轮报）。
                    _last_text = next(
                        (t for t in reversed(seg_texts) if t) if seg_texts else "", ""
                    )
                    if _last_text:
                        seg_text = f"{_last_text}（画面延续，保持该状态）"
                    else:
                        seg_text = motion
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
