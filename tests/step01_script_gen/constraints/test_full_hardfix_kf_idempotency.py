# -*- coding: utf-8 -*-
from __future__ import annotations

import copy

from step01_script_gen.constraints.context import ConstraintContext
from step01_script_gen.constraints.kf_event_order import KfEventOrderConstraint
from step01_script_gen.constraints.kf_blood_monotonic import KfBloodMonotonicConstraint
from shared.quality_gate import compute_script_revision


def _run_kf_joint(script):
    """KF 链的联合不变量（链序子集）.

    v6.76（09-19）：原 delete→fill→blood 三环里
      - delete（`KfEventOrderConstraint`）与 fill（`KfTimeWindowAlignmentConstraint`）
        均已降为只报告；
      - 链尾重复实例 `_KfTimeWindowAlignmentFinal` 已删除（纯重复）。
    只剩 blood 一环仍会写 `kf.desc`，故保留在联合链路里。
    v6.77 起用**基类**实例（链尾重复实例 _KfBloodMonotonicFinal 已删除）。
    """
    ctx = ConstraintContext.from_script(script)
    KfEventOrderConstraint().apply(ctx)
    KfBloodMonotonicConstraint().apply(ctx)


def test_kf_report_and_blood_pipeline_is_idempotent():
    script = {
        "characters": [{"identifier": "甲"}],
        "hook": None,
        "shots": [{
            "index": 8,
            "duration_sec": 4.0,
            "prompt": "甲在湾堤",
            "ff_desc": "湾堤，甲举灯",
            "lf_desc": "甲跃上船，心口暗红洇痕",
            "motion_desc": "0-1秒：门扉打开；1-3秒：举灯询问；3-4秒：纵身跃上船",
            "main_subject": "甲",
            "char_layout": [{"name": "甲", "position": "画中", "facing": "站立", "role": "主", "surface": ""}],
            "frame_layouts": {
                "ff": [{"name": "甲", "on_screen": True, "position": "画中", "pose": "站立", "gaze_target": "画左", "props": ["灯"]}],
                "lf": [{"name": "甲", "on_screen": True, "position": "画中", "pose": "跃上船", "gaze_target": "画左", "props": []}],
                "kfs": [
                    [{"name": "甲", "on_screen": True, "position": "画中", "pose": "跃上船", "gaze_target": "画左", "props": []}],
                    [{"name": "甲", "on_screen": True, "position": "画中", "pose": "跃上船", "gaze_target": "画左", "props": []}],
                    [{"name": "甲", "on_screen": True, "position": "画中", "pose": "举灯", "gaze_target": "画左", "props": ["灯"]}],
                ],
            },
            "keyframes": [
                {"desc": "甲已经跃上船", "t_sec": 1.1, "stage": "pre"},
                {"desc": "甲已经跃上船，船只摇晃", "t_sec": 1.7, "stage": "react"},
                {"desc": "甲举灯询问", "t_sec": 2.2, "stage": "after"},
            ],
        }],
    }
    first = copy.deepcopy(script)
    _run_kf_joint(first)
    second = copy.deepcopy(first)
    _run_kf_joint(second)
    a = first["shots"][0]
    b = second["shots"][0]
    assert a["keyframes"] == b["keyframes"]
    assert a["frame_layouts"]["kfs"] == b["frame_layouts"]["kfs"]
    assert compute_script_revision(first) == compute_script_revision(second)

def test_full_hardfix_revision_is_stable_after_final_kf_normalization():
    from step01_script_gen.constraints import run_hard_fix

    script = {
        "characters": [{"identifier": "甲"}],
        "hook": None,
        "shots": [{
            "index": 8,
            "duration_sec": 4.0,
            "prompt": "甲在湾堤",
            "ff_desc": "湾堤，甲举灯",
            "lf_desc": "甲跃上船，心口暗红洇痕",
            "motion_desc": "0-1秒：门扉打开；1-3秒：举灯询问；3-4秒：纵身跃上船",
            "main_subject": "甲",
            "char_layout": [{"name": "甲", "position": "画中", "facing": "站立", "role": "主", "surface": ""}],
            "frame_layouts": {
                "ff": [{"name": "甲", "on_screen": True, "position": "画中", "pose": "站立", "gaze_target": "画左", "props": ["灯"]}],
                "lf": [{"name": "甲", "on_screen": True, "position": "画中", "pose": "跃上船", "gaze_target": "画左", "props": []}],
                "kfs": [
                    [{"name": "甲", "on_screen": True, "position": "画中", "pose": "跃上船", "gaze_target": "画左", "props": []}],
                    [{"name": "甲", "on_screen": True, "position": "画中", "pose": "跃上船", "gaze_target": "画左", "props": []}],
                    [{"name": "甲", "on_screen": True, "position": "画中", "pose": "举灯", "gaze_target": "画左", "props": ["灯"]}],
                ],
            },
            "keyframes": [
                {"desc": "甲已经跃上船", "t_sec": 1.1, "stage": "pre"},
                {"desc": "甲已经跃上船，船只摇晃", "t_sec": 1.7, "stage": "react"},
                {"desc": "甲举灯询问", "t_sec": 2.2, "stage": "after"},
            ],
        }],
    }
    first = copy.deepcopy(script)
    first, _ = run_hard_fix(first, is_final_pass=True, return_report=True)
    second = copy.deepcopy(first)
    second, _ = run_hard_fix(second, is_final_pass=True, return_report=True)
    assert first["shots"][0]["keyframes"] == second["shots"][0]["keyframes"]
    assert first["shots"][0]["frame_layouts"]["kfs"] == second["shots"][0]["frame_layouts"]["kfs"]
    assert compute_script_revision(first) == compute_script_revision(second)
