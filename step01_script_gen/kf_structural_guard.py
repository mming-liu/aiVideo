# -*- coding: utf-8 -*-
"""KF 结构修复的确定性验收闸门（v6.104，2026-09-24 总计划 §1.1）.

## 为什么需要

`repair_fields` 从 v6.104 起，对「必须**增删 KF**才能修」的问题（时段覆盖缺段 /
LF 结果动作缺过程段）授权 **KF 列表本身**（`keyframes` / `frame_layouts.kfs`）。
不授权列表时，`merge_authorized` 的规则是「列表路径未被授权 → 长度变化一律回填
原值」，于是模型按修复 prompt 决策树给出的正解
（「报『kf 时段缺段』→ 按 motion_desc 时段表补 1 帧定格」）被**静默回填**：

    实测（第二十三次重跑前的确定性验证）：
      allowed = {keyframes.*.t_sec, keyframes.*.desc, keyframes.*.stage}
      模型返回 3 个 kf → 合并后 len=2（整表被回填）

后果是 `kf_time_window_alignment`（检测 1：kfs 数 < 非空时段数）在旧授权面下
**结构性不可解**——三轮重跑都停在 `resolved=0`，并被误记成「模型补帧能力不足」。

## 授权列表的代价由本模块补回

允许增删 KF 之后，必须防止「靠增删/改写时段让覆盖检测闭嘴」。总计划 §1.1 写死了
两条验收，本模块就是它们：

  1. `kf_order_violations`       —— KF 必须落在镜内且**按时段单调**（增删后不得乱序）；
  2. `motion_event_preservation` —— `motion_desc` 的原事件文本必须**仍在、顺序不变**。
     这就是 §1.1 的「motion_desc + keyframes 原子候选」验收：模型**可以**补事件
     （shot 7 的正解就是补「张十三丢下灯盏，纵身跃上船梢」），但不可以删事件或
     换顺序——那是「靠改检测输入让告警归零」，不是修复。

两条都**只读**：不写任何字段，只比较「本轮之前 vs 本轮之后」的镜头对象。
"""
from __future__ import annotations

import re

from shared.kf_time import get_kf_t_sec
from shared.motion_segments import parse_segments, split_segment_texts

# 归一后仍短于该长度的事件片段不参与「是否仍在」判定（避免标点残片/单字残渣
# 产生假违例）。取 3：中文事件短语通常 >= 3 字，而解析残留几乎都是 1-2 字；
# 阈值定高了会把「乙闷哼」这类短事件漏掉，等于给「删事件」留后门。
_MIN_EVENT_CHARS = 3
_KEEP_RE = re.compile(r"[^\w]+", re.UNICODE)
_TOL = 0.011


def _norm(text: str) -> str:
    """只留字词（去标点/空白/时间标签残渣）——判「同一段话还在不在」."""
    return _KEEP_RE.sub("", str(text or ""))


def _shot_duration(shot: dict) -> float:
    try:
        dur = float(shot.get("duration_sec") or shot.get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0
    return dur if dur > 0 else 0.0


def _kf_times(shot: dict) -> list:
    """按**列表顺序**取每帧物理时刻（缺 t_sec → None，非 dict 元素跳过）."""
    kfs = shot.get("keyframes")
    if not isinstance(kfs, list):
        return []
    out: list = []
    for kf in kfs:
        if not isinstance(kf, dict):
            continue
        t = get_kf_t_sec(kf, shot)
        out.append(None if t is None else float(t))
    return out


def kf_order_violations(shot: dict) -> list:
    """KF 物理时刻必须落在 (0, 镜长] 且按列表顺序**非递减**（总计划 §1.1）."""
    out: list = []
    dur = _shot_duration(shot)
    times = _kf_times(shot)
    for i, t in enumerate(times):
        if t is None:
            out.append("kf%d 缺 t_sec" % i)
        elif dur > 0 and (t <= 0 or t > dur + _TOL):
            out.append("kf%d t_sec=%g 越出镜长 %g" % (i, t, dur))
    known = [t for t in times if t is not None]
    for i in range(1, len(known)):
        if known[i] < known[i - 1] - _TOL:
            out.append("KF 物理时刻非单调：第 %d 帧 %g 早于前一帧 %g"
                       % (i, known[i], known[i - 1]))
            break
    return out


def motion_event_preservation(before_shot, after_shot) -> list:
    """`motion_desc` 的原事件文本必须仍在、顺序不变（总计划 §1.1）.

    只在原 `motion_desc` **显式分时段**（>= 2 段）时判定——未分时段时没有
    「事件文本」可锁，返回空表（不影响非动作镜）。
    """
    if not isinstance(before_shot, dict) or not isinstance(after_shot, dict):
        return []
    before_motion = str(before_shot.get("motion_desc") or "")
    if not before_motion.strip():
        return []
    dur = _shot_duration(before_shot) or _shot_duration(after_shot)
    if dur <= 0:
        return []
    try:
        segs = parse_segments(before_motion, dur, implicit_tail=True, clamp=True)
    except Exception:
        return []
    if len(segs) < 2:
        return []
    try:
        texts = split_segment_texts(before_motion, segs, dur)
    except Exception:
        return []
    hay = _norm(after_shot.get("motion_desc") or "")
    out: list = []
    last = -1
    for i, text in enumerate(texts):
        key = _norm(text)
        if len(key) < _MIN_EVENT_CHARS:
            continue
        pos = hay.find(key)
        if pos < 0:
            out.append("原事件文本丢失（第 %d 段）：「%s」" % (i, str(text)[:30]))
            continue
        if pos < last:
            out.append("事件顺序变化（第 %d 段）：「%s」" % (i, str(text)[:30]))
        last = max(last, pos)
    return out


def kf_structural_violations(before_shot, after_shot) -> list:
    """增删 KF 之后必须同时满足的两条确定性验收（本模块对外唯一入口）."""
    return (kf_order_violations(after_shot or {})
            + motion_event_preservation(before_shot, after_shot))
