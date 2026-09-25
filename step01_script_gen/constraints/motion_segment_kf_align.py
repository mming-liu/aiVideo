# -*- coding: utf-8 -*-
"""motion_segment_kf_align — **只报告，不再合并时段**（v6.103，方向反转）.

## 为什么反转

v6.102 的实现是「时段数 > KF 数 → 合并相邻时段，直到段数 ≤ KF 数」。它当时
自称安全（只加宽时间窗、文本一字不丢、不派生任何 kf），但总计划 §2.2 把它列为
**P1**：

> `motion_segment_kf_align` 合并非空时段掩盖缺 KF → 告警归零但动作证据没有增加

实测形态：一个镜头 motion 分了 3 段、只有 2 个 KF，且 KF 全挤在前段 —— 合并后
告警消失、`step01_quality.json` 变干净，可**动作过程帧一个都没多**。下游视频
模型拿到的是「更粗的时间粒度 + 同样缺的过程帧」。「告警归零」被当成了「问题解决」。

## 为什么连「清理空段」也不做

清理空段看起来无害（空段不进检查器的 `_required_segments`），但**任何合并都会
加宽时间窗**，而 `_check_kf_time_window_alignment` 的检测 2（orphan KF）判的是
「kf 物理时刻是否落在某个已声明时段内」——窗口一宽，原本落在「描述空区」的
kf 就被新窗口吸收，一条真实的「帧没有对应动作文本」告警随之消失。也就是说：
对检测 1 中性，对检测 2 不中性。总计划的验收口径是「**只有补上有效 KF 后才
归零**」，所以这里一个字节都不写。

## 现在的职责

本约束**保留注册位**（它在审计段之前，顺序契约由
`test_registered_before_the_audit_boundary` 锁住），但 `apply()` 不产生任何修复。
「非空事件段缺 KF」的检测由确定性层负责、修复由修复事务负责：

  - 检测：`validation._check_kf_time_window_alignment`（检测 1 缺段 / 检测 2 错窗 /
    检测 3 倒装）与 `_check_temporal_plan` 的 `event_uncovered`；
  - 归零方式：**加帧**。两类告警都在
    `main._QUALITY_BLOCKING_CATEGORIES` 里，告警文本点名 `kf` / `时段`，因此
    `repair_fields.allowed_repair_fields` 授权 `keyframes.*.t_sec` /
    `keyframes.*.desc`（`kf_time_window_alignment` 另有 code 级映射）——修复轮
    据此补 KF，而不是改 `motion_desc`；
  - 修复轮的验收会**重新跑**这两个检查（总计划阶段 3），所以「换措辞骗过
    字符串判据」不成立。

`CLAUDE.md` 的「不派生 kf desc 硬修复」决定了确定性层**不可能**安全补帧：
从 `motion_desc` 切出 `kf.desc` 需要语义理解。所以这里没有可做的确定性修复。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


class MotionSegmentKfAlignConstraint(Constraint):
    """**只报告**：非空事件段缺 KF 时不改写 `motion_desc`（缺帧交修复事务补帧）.

    `apply()` 刻意返回空列表——见模块 docstring「为什么连清理空段也不做」。
    """

    name = "motion_segment_kf_align"
    severity = Severity.WARN
    fields = ("motion_desc", "keyframes")

    def apply(self, ctx) -> list[RepairRecord]:  # noqa: D102 - 见类 docstring
        return []


__all__ = ["MotionSegmentKfAlignConstraint"]
