# -*- coding: utf-8 -*-
"""motion_segment_kf_align — 时段数 > KF 数时**合并相邻时段**（v6.102）.

## 为什么是这个方向

规则 16.4「kf 物理时刻 = motion_desc 时段铁律，**kf 个数 ≥ 时段数**」写在
`prompts/*.md` 里，但模型稳定违反：第十二～十五次每轮都有 5-14 个镜头违反
（第十五次 27 KF / 34 段，7/11 镜有缺口）。提示词约束不了它——又一次主线 A。

`CLAUDE.md` 明确禁止「**代码派生 kf desc**」：

> 不派生 kf desc 硬修复——超出确定性兜底范围（需语义理解从 motion_desc 切出
> kf desc），应触发 LLM regen 而非代码层硬填

所以确定性手段只能往**反方向**走：不动 KF，动 `motion_desc`——把时段**合并**到与
现有 KF 数一致。这个方向是安全的，因为：

  - 合并只**加宽**时间窗，文本一字不丢地拼接（`A` + `B` → `A；B`）；
  - 落在原时段内的 KF，落在合并后的时段内**恒成立**（窗口是超集）；
  - **不制造任何新描述**——没有一行 `kf.desc` 是代码编出来的。

## 与「删内容」的界限

合并不删字：两段文本用「；」连接。它改变的是**动作分段的时间粒度**，
而分段本来就是叙事的视觉切分方式，不是原文事实。

## 保守边界

  - **无 KF 的镜头不碰**（那是「缺帧」，交修复轮/收敛轮，合并只会掩盖它）；
  - 目标段数**不低于 2**（检查器对 `< 2` 段的镜头整段跳过——压到 1 段等于
    把告警藏起来，不做）；
  - 因此 `kf 数 = 1` 的镜头**修不掉**，告警保留、仍交修复轮（如实，不掩盖）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.kf_time import get_kf_t_sec
from shared.motion_segments import parse_segments, split_segment_texts

from .base import Constraint
from .types import RepairRecord, Severity

_MIN_SEGMENTS = 2          # 目标段数下限（低于 2 会被检查器整段跳过 → 等于藏告警）
_MAX_MERGE_STEPS = 12      # 防御性上限：一段最多合并多少次


def _join(a: str, b: str) -> str:
    """拼接两段文本（去重空段，保留原有措辞）."""
    a, b = str(a or "").strip(), str(b or "").strip()
    if not a:
        return b
    if not b:
        return a
    return a.rstrip("；;") + "；" + b


def _join_all(parts: list) -> str:
    """把一组段文本拼成一段（逐段 `_join`，一字不丢）."""
    out = ""
    for p in parts:
        out = _join(out, p)
    return out


def _required_count(texts: list) -> int:
    """检查器口径的「必需段数」= 文本非空的段数（`_check_kf_time_window_alignment` 同源）."""
    return len([1 for t in texts if str(t or "").strip()])


class MotionSegmentKfAlignConstraint(Constraint):
    """时段数 > KF 数 → 合并最短的相邻时段，直到段数 ≤ KF 数（不删字、不派生 KF）."""

    name = "motion_segment_kf_align"
    severity = Severity.WARN
    fields = ("motion_desc", "keyframes")

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        targets = list(getattr(ctx, "shots", None) or [])
        hook = getattr(ctx, "hook", None)
        if isinstance(hook, dict):
            targets.append(hook)
        for shot in targets:
            rec = self._align_one(shot)
            if rec is not None:
                repairs.append(rec)
        return repairs

    def _align_one(self, shot) -> RepairRecord | None:
        if not isinstance(shot, dict):
            return None
        motion = str(shot.get("motion_desc") or "")
        if not motion.strip():
            return None
        try:
            dur = float(shot.get("duration_sec") or shot.get("duration") or 0)
        except (TypeError, ValueError):
            return None
        if dur <= 0:
            return None
        kfs = [k for k in (shot.get("keyframes") or []) if isinstance(k, dict)]
        if not kfs:
            # 缺帧是另一类问题（修复轮/收敛轮），合并只会把它掩盖掉
            return None
        try:
            segs = parse_segments(motion, dur, implicit_tail=True, clamp=True)
            if len(segs) < 2:
                return None
            texts = [str(t or "") for t in split_segment_texts(motion, segs, dur)]
        except Exception:
            return None
        while len(texts) < len(segs):
            texts.append("")
        texts = texts[: len(segs)]

        _target = max(len(kfs), _MIN_SEGMENTS)

        # **按 KF 位置分组**，不是「合并最短相邻对」。
        #
        # 自测发现：只按最短相邻对合并解决不了问题——KF 往往**挤在前几段**
        # （实测 3 段 2 KF，两个 KF 都落在前 4 秒），合并掉最短的那对之后
        # 尾段仍然没有帧，告警照旧。**唯一可行解是让每个合并后的窗口各含至少一个 KF**：
        # 于是分组边界必须由 KF 落点决定（最后一组吸收尾巴）。
        #
        # 覆盖判定与检查器**同源**（`a-0.01 <= t <= b+0.01`，边界帧同时覆盖相邻两段）。
        def _covered(shot_, segs_, dur_):
            cov = set()
            for kf in kfs:
                t = get_kf_t_sec(kf, shot_)
                if t is None or t < 0 or (dur_ > 0 and t > dur_ + 0.01):
                    continue
                for i, (a, b) in enumerate(segs_):
                    if a - 0.01 <= t <= b + 0.01:
                        cov.add(i)
            return cov

        hit = sorted(_covered(shot, segs, dur))
        # **只在该修的时候动手**：触发判据与检查器**同源**——存在「必需但无帧」的段。
        # 已对齐的镜头一个字节都不动（否则会平白改写 motion_desc、还会丢掉隐式尾段）。
        _required = [i for i, t in enumerate(texts) if str(t or "").strip()]
        if not [i for i in _required if i not in set(hit)]:
            return None
        if not hit or len(hit) > _target:
            # 无 KF 落在任何时段内（是「时段归属」问题，不是本约束能修的），
            # 或 KF 落在同一段/段数不够分 → 合并不动它，交修复轮（如实，不掩盖）
            return None
        if len(hit) < _MIN_SEGMENTS:
            # 只剩一个「含帧窗口」→ 合并到 1 段会被检查器整段跳过（等于藏告警），不做
            return None

        # 每组 = [prev .. hit_k]，最后一组延伸到末尾
        groups: list[tuple[int, int]] = []
        prev = 0
        for k, h in enumerate(hit):
            if k == len(hit) - 1:
                groups.append((prev, len(segs) - 1))
            else:
                groups.append((prev, h))
                prev = h + 1
        if len(groups) < _MIN_SEGMENTS or len(groups) >= len(segs):
            return None
        segs2 = [[float(segs[a][0]), float(segs[b][1])] for a, b in groups]
        texts2 = [_join_all(texts[a:b + 1]) for a, b in groups]
        _before = len(segs)
        while len(texts2) < len(segs2):
            texts2.append("")

        new_motion = " / ".join(
            "%g-%g秒：%s" % (a, b, t) for (a, b), t in zip(segs2, texts2))
        if not new_motion.strip() or new_motion == motion:
            return None
        shot["motion_desc"] = new_motion
        return RepairRecord(
            constraint_name=self.name,
            shot_index=shot.get("index"),
            action="merge_motion_segments",
            target="motion_desc",
            before="%d 段 / %d 个 kf：%s" % (_before, len(kfs), motion[:60]),
            after="%d 段 / %d 个 kf：%s" % (len(segs2), len(kfs), new_motion[:60]),
            detail=("时段数 %d > kf 数 %d → 合并最短相邻时段到 %d 段"
                    "（只加宽时间窗、文本一字不丢；不派生任何 kf）"
                    % (_before, len(kfs), len(segs2))),
        )


__all__ = ["MotionSegmentKfAlignConstraint"]
