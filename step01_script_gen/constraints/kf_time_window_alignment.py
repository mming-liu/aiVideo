"""E. kf 时段缺段约束（v6.42 迁移自 _hard_fix_kf_time_window_alignment；v6.76 只报告）.

检测两类缺陷：
  1. 越窗——kf 物理时刻不在 motion_desc 的任何显式时段内
  2. 缺段——某时段没有任何 kf 覆盖

历史修法（v6.42~v6.73）：
  - 越窗 → 拉到「最近且未被占用」的段中点
  - 缺段 → 从 `_split_motion_to_seg_texts` 取段文本，派生新 kf 并 **append 到列表末尾**

v6.76（2026-09-19）降为**只报告**，不再搬时刻、不再补帧。原因：
  1. append 不排序。补出的帧落在列表末尾，而唯一做「降序 → 重整」的
     kf_monotonic 跑在本约束**之前**，于是补出的早时刻帧逃过单调性修复，
     直接产出逆序 kf（实测 chapter_001 index 11.5：kf4=2.548s, kf5=0.189s）。
  2. 「段中点」没有任何原文依据——motion_desc 的段是叙述顺序，不等于事件的
     真实物理时刻；用它当 kf 时刻等于把推断写成事实。
  3. 缺段的**根因在上游**：`runtime_extend` 拉伸 duration_sec 而不同步事件区间，
     人为制造出「没有内容的尾部时段」，本约束再把它补成「（画面延续，保持该状态）」
     的伪 hold 帧。在链尾补帧只是把症状固化。
  4. 段文本经 `_split_motion_to_seg_texts` 剥离时间标签，而该函数的正则
     **只认整数**（`\\d+`，不支持小数点），会把 `0-0.378378秒：...` 剥成
     `.378378秒：...` 残片直接写进 kf.desc —— 实测产出的残缺时间文本即此路径。
     本约束改为只报告后，该写路径随之消失。

现状：record_violation(repaired=False) + RepairRecord(action="warn")，
保留原值交 audit / regen。

幂等：只报告，天然幂等。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.kf_time import get_kf_t_sec
from .base import Constraint
from .types import RepairRecord, Severity, Violation


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
    """E. kf 物理时刻 = motion_desc 时段：只检测并上报（v6.76 起）.

    severity=WARN: 缺段/越窗的根因在上游（时长拉伸、拆镜），交 regen 处理.
    幂等：只报告，不改字段.
    """

    name = "kf_time_window_alignment"
    severity = Severity.WARN
    fields = ()  # 不扫文本字段，处理的是 keyframes

    def _report(
        self, ctx, repairs: list[RepairRecord], idx, *, target: str,
        message: str, evidence: str, detail: str,
    ) -> None:
        ctx.record_violation(Violation(
            constraint_name=self.name,
            severity=self.severity,
            shot_index=idx,
            field=target,
            message=message,
            evidence=evidence[:200],
            repaired=False,
        ))
        repairs.append(RepairRecord(
            constraint_name=self.name,
            shot_index=idx,
            action="warn",
            target=target,
            before=evidence[:80],
            after=evidence[:80],
            detail=detail,
        ))

    def apply(self, ctx) -> list[RepairRecord]:
        # Lazy import: validation.py 顶部会 import constraints 包，形成循环。
        from step01_script_gen.validation import _parse_motion_time_segments

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

            existing_t = [
                t for kf in kfs if isinstance(kf, dict)
                for t in [get_kf_t_sec(kf, shot)] if t is not None
            ]

            # ---- 1. 越窗 ----
            orphans: list[tuple[int, float]] = []
            for j, kf in enumerate(kfs):
                if not isinstance(kf, dict):
                    continue
                t = get_kf_t_sec(kf, shot)
                if t is None:
                    continue
                if any(a - 0.01 <= t <= b + 0.01 for a, b in segs):
                    continue
                orphans.append((j, t))
            if orphans:
                _txt = ", ".join(f"keyframes[{j}]={t:.3f}s" for j, t in orphans)
                _segs = "/".join(f"{a:g}-{b:g}s" for a, b in segs)
                self._report(
                    ctx, repairs, idx, target="keyframes[*].t_sec",
                    message=(
                        f"{len(orphans)} 个关键帧的物理时刻不在 motion 任何时段内"
                        f"（{_txt}；时段 {_segs}）；保留原值未搬到段中点——"
                        "段中点是推断不是证据，交 regen 重写该镜时间轴。"
                    ),
                    evidence=_txt,
                    detail="关键帧越出 motion 时段，保留原值交由 audit/regen",
                )

            # ---- 2. 缺段 ----
            missing_segs: list[tuple[int, tuple[float, float]]] = []
            for i, (a, b) in enumerate(segs):
                covered = any(a - 0.01 <= t <= b + 0.01 for t in existing_t)
                if not covered:
                    missing_segs.append((i, (a, b)))

            # F1.4 增强（保留原判定）：motion_desc 无显式时段（仅 1 段 = (0, dur)），
            # 若 kfs < 1，视段 0 为缺段
            if len(segs) < 2 and not missing_segs and len(existing_t) < 1:
                missing_segs = [(0, segs[0])]

            if missing_segs:
                _txt = ", ".join(f"段{i}({a:g}-{b:g}s)" for i, (a, b) in missing_segs)
                self._report(
                    ctx, repairs, idx, target="keyframes",
                    message=(
                        f"{len(missing_segs)} 个 motion 时段无关键帧覆盖（{_txt}）；"
                        "保留原样未派生补帧——补帧时刻取段中点无原文依据，且 append "
                        "到列表末尾会产出逆序帧（逃过 kf_monotonic）。"
                        "缺段根因通常是时长拉伸产生了无内容的尾部时段，交 regen 处理。"
                    ),
                    evidence=_txt,
                    detail="motion 时段缺关键帧，保留原样交由 audit/regen",
                )

        return repairs
