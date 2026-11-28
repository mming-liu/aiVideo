# -*- coding: utf-8 -*-
"""`motion_segment_kf_align` —— 只报告，不再合并时段（v6.103 方向反转）.

v6.102 的契约是「时段数 > KF 数 → 合并相邻时段直到对齐」。总计划 §2.2 判定那是
**掩盖**：合并让告警归零，动作过程帧一个都没多。新契约：

  1. 非空事件段没有 KF 覆盖时**只报告**，`motion_desc` 一个字节都不动；
  2. 归零只能靠**补有效 KF**（由修复事务完成）；
  3. 连「清理空段」也不做——任何合并都会加宽时间窗，从而吸收 orphan KF、
     消掉检测 2 的真实告警（模块 docstring 有完整论证）；
  4. 注册位与顺序契约不变（仍在审计段之前）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.step01_script_gen.conftest import make_ctx, make_script  # noqa: E402
from step01_script_gen.constraints.motion_segment_kf_align import (  # noqa: E402
    MotionSegmentKfAlignConstraint,
)


def _shot(index, motion, kf_ts, dur=6.0):
    return {"index": index, "duration": dur, "duration_sec": dur,
            "motion_desc": motion,
            "keyframes": [{"desc": "帧%d" % i, "t_sec": t, "stage": "approach"}
                          for i, t in enumerate(kf_ts, 1)],
            "prompt": "[中景] x", "ff_desc": "f", "lf_desc": "l",
            "narration_segment": "", "subtitle": ""}


def _warnings(script):
    """本类别（越窗/顺序）+ **缺段诊断**通道的全部文本.

    2026-09-27（用户裁决 3）：缺段文本移到了 `coverage_warnings`（非阻塞诊断，
    唯一阻塞是 `kf_segment_missing`）。本文件判的是「本约束不得靠合并让**报告**
    消失」，所以两个通道合起来读。
    """
    from step01_script_gen.validation import _check_kf_time_window_alignment
    _r = _check_kf_time_window_alignment(script["shots"], None)
    return list(_r["warnings"]) + list(_r["coverage_warnings"])


def _apply(script):
    return MotionSegmentKfAlignConstraint().apply(make_ctx(script))


def test_missing_kf_segment_is_reported_not_merged():
    """最小反例：3 段 2 KF，两个 KF 都落在前 4 秒.

    v6.102 会把后两段合并成一段让告警消失；新契约**必须保留告警**——
    尾段依然没有帧，动作证据没有增加。
    """
    script = make_script([_shot(1, "0-2秒：A；2-4秒：B；4-6秒：C", [1.0, 3.0])])
    before = script["shots"][0]["motion_desc"]
    assert len(_warnings(script)) == 1, "前置条件：本来有告警"

    assert _apply(script) == [], "不得产生任何修复"
    assert script["shots"][0]["motion_desc"] == before, "motion_desc 一字不动"
    assert len(_warnings(script)) == 1, "告警必须留着交修复事务"


def test_four_segments_three_kfs_is_reported_not_merged():
    script = make_script([_shot(1, "0-2秒：A；2-4秒：B；4-6秒：C；6-8秒：D",
                                [1.0, 3.0, 5.0], dur=8.0)])
    before = script["shots"][0]["motion_desc"]
    assert len(_warnings(script)) == 1
    assert _apply(script) == []
    assert script["shots"][0]["motion_desc"] == before
    assert len(_warnings(script)) == 1


def test_aligned_shot_is_untouched():
    """已对齐 → 一个字节都不动."""
    script = make_script([_shot(1, "0-2秒：A；2-4秒：B", [1.0, 3.0])])
    before = script["shots"][0]["motion_desc"]
    assert _warnings(script) == []
    assert _apply(script) == []
    assert script["shots"][0]["motion_desc"] == before


def test_every_shot_is_left_byte_identical():
    """**任意**镜头（含空段 / 隐式空尾段 / 无 KF）都不被改写."""
    cases = [
        _shot(1, "0-2秒：A；2-4秒：B；4-6秒：C", [3.0]),          # 1 KF
        _shot(2, "0-2秒：A；2-4秒：B；4-6秒：C", []),             # 无 KF
        _shot(3, "0-2秒：；2-4秒：B；4-6秒：C", [1.0, 3.0, 5.0]),  # 显式空段
        _shot(4, "0-2秒：A；2-4秒：B", [1.0, 3.0], dur=6.0),      # 隐式空尾段
        _shot(5, "0-2秒：；2-4秒：；4-6秒：", [1.0, 3.0, 5.0]),     # 全空（纯噪声）
    ]
    script = make_script(cases)
    before = [s["motion_desc"] for s in script["shots"]]
    assert _apply(script) == []
    assert [s["motion_desc"] for s in script["shots"]] == before


def test_warning_set_is_byte_identical_before_and_after():
    """核心不变式：本约束**不改变任何告警**（既不加也不减）.

    它是「只报告」的机器可验证版本——`motion_desc` 不动、`keyframes` 不动，
    检查器看到的输入完全相同，因此告警集合必然逐条相同。任何一条告警因为
    「窗口被加宽」而消失，都说明这里又偷偷改写了检测输入。
    """
    cases = [
        _shot(1, "0-2秒：A；2-4秒：B；4-6秒：C", [1.0, 3.0]),        # 尾段缺帧
        _shot(2, "0-2秒：A；2-4秒：B；4-6秒：C；6-8秒：D",
              [1.0, 3.0, 5.0], dur=8.0),                             # 尾段缺帧
        _shot(3, "0-2秒：A；4-6秒：B", [1.0, 3.0]),                  # 错窗帧
        _shot(4, "0-2秒：A；2-4秒：B", [1.0, 3.0], dur=6.0),         # 隐式空尾段
        _shot(5, "0-2秒：；2-4秒：；4-6秒：", [1.0, 3.0, 5.0]),       # 全空（纯噪声）
        _shot(6, "0-2秒：A；2-4秒：B；4-6秒：C", [3.0]),             # 单帧
    ]
    script = make_script(cases)
    before = _warnings(script)
    assert _apply(script) == []
    assert _warnings(script) == before


def test_idempotent():
    script = make_script([_shot(1, "0-2秒：A；2-4秒：B；4-6秒：C", [1.0, 3.0])])
    assert _apply(script) == []
    assert _apply(script) == []


def test_registered_before_the_audit_boundary():
    """保留注册位：顺序契约不因「不再写字段」而失效（将来重加写入仍受同一约束）."""
    from shared.semantic_barrier import AUDIT_SEGMENT_START
    from step01_script_gen.constraints import ALL_CONSTRAINTS

    names = [c.name for c in ALL_CONSTRAINTS]
    assert names.index("motion_segment_kf_align") < names.index(AUDIT_SEGMENT_START)
