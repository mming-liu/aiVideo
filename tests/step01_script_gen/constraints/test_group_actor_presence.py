# -*- coding: utf-8 -*-
"""群体在场状态：约束**不得**把描述判定为「已离场」的条目翻回在场（v6.106）.

实测（第二十七次重跑，目录 12 的 shot 1）：
  `frame_presence_consistency` 报「描述称老农已走远，布局仍 on_screen=true」→
  修复轮把它改成 false → 同一轮后处理里 `group_actor_continuity` 又按
  `pose not in (退下/退场/离场)` 翻回 true → 同一处修复连做 4 轮、
  `resolved=0`，阻塞永远清不掉（BLOCK 的直接成因之一）。

事实源优先级（总计划 §1.2）：**帧描述 > frame_layouts**。布局是描述派生的，
用 3 个姿态词去推翻描述是把这个优先级倒过来。修法：对**已有条目**只降不升——
pose 明确是退场词才写 False；非退场 pose 不构成在场证据。
"""
from __future__ import annotations

from step01_script_gen.constraints.group_actor_continuity import (
    GroupActorContinuityConstraint, _apply_presence,
)


def test_apply_presence_only_demotes():
    entry = {"name": "群演", "on_screen": False, "pose": "牵牛行走"}
    assert _apply_presence(entry, "牵牛行走") is False
    assert entry["on_screen"] is False          # 非退场 pose 不是在场证据

    entry2 = {"name": "群演", "on_screen": True, "pose": "退场"}
    assert _apply_presence(entry2, "退场") is True
    assert entry2["on_screen"] is False         # 明确退场 → 可以安全降级

    entry3 = {"name": "群演", "on_screen": False, "pose": "退场"}
    assert _apply_presence(entry3, "退场") is False   # 已是 False → 幂等


def _shot(lf_on_screen: bool):
    return {
        "index": 1,
        "duration_sec": 5.0,
        "ff_desc": "黄昏荷塘，老农牵牛在远处缓缓走过。",
        "lf_desc": "船上两人交谈，老农已走远，画面只剩水面。",
        "motion_desc": "前2秒：老农牵牛走远。2-5秒：船上两人交谈。",
        "keyframes": [
            {"t_sec": 1.0, "desc": "老农牵牛走在远处田埂。", "stage": "pre"},
            {"t_sec": 3.5, "desc": "老农已走远，画面只剩船上两人。", "stage": "after"},
        ],
        "frame_layouts": {
            "ff": [{"name": "群演", "on_screen": True, "position": "远处",
                    "pose": "牵牛行走", "gaze_target": "", "props": ["牛"]}],
            "lf": [{"name": "群演", "on_screen": lf_on_screen, "position": "远处",
                    "pose": "牵牛行走", "gaze_target": "", "props": ["牛"]}],
            "kfs": [[{"name": "群演", "on_screen": True, "position": "远处",
                      "pose": "牵牛行走", "gaze_target": "", "props": ["牛"]}],
                    [{"name": "群演", "on_screen": lf_on_screen, "position": "远处",
                      "pose": "牵牛行走", "gaze_target": "", "props": ["牛"]}]],
        },
    }


def _run(script):
    from step01_script_gen.constraints.context import ConstraintContext
    ctx = ConstraintContext(script=script)
    GroupActorContinuityConstraint().apply(ctx)
    return script


def test_departed_group_entry_is_not_resurrected():
    script = {"shots": [_shot(lf_on_screen=False)]}
    _run(script)
    lf = script["shots"][0]["frame_layouts"]["lf"]
    assert all(e["on_screen"] is False for e in lf if e.get("name") == "群演")
    kf1 = script["shots"][0]["frame_layouts"]["kfs"][1]
    assert all(e["on_screen"] is False for e in kf1 if e.get("name") == "群演")


def test_present_group_entry_is_left_alone():
    """对照：本来就在场（True）时，约束仍按原逻辑工作，不会误降级."""
    script = {"shots": [_shot(lf_on_screen=True)]}
    _run(script)
    lf = script["shots"][0]["frame_layouts"]["lf"]
    assert all(e["on_screen"] is True for e in lf if e.get("name") == "群演")
