# -*- coding: utf-8 -*-
from __future__ import annotations
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def test_fixed_camera_with_affirmative_pan_is_definite():
    from shared.shot_motion_checks import motion_outcome, motion_report
    shot = {"camera_movement": "固定机位",
            "motion_desc": "镜头掠过荷丛",
            "ff_desc": "船在荷丛中", "lf_desc": "船滑出荷丛，停在开阔水面"}
    assert motion_outcome(shot) == "definite"
    assert any(r["class"] == "definite" for r in motion_report(shot))


def test_plain_verb_follow_is_not_a_camera_instruction():
    """固定机位 + “小孩跟随母亲走进院子”：跟随是人物动作，不是运镜."""
    from shared.shot_motion_checks import motion_outcome
    shot = {"camera_movement": "固定机位",
            "motion_desc": "小孩跟随母亲走进院子",
            "ff_desc": "院门口", "lf_desc": "院子里"}
    assert motion_outcome(shot) == "no_conflict"


def test_segmented_camera_is_suspected_not_definite():
    """前两秒固定机位、随后横移：分段运镜，不能按整镜固定机位判 definite."""
    from shared.shot_motion_checks import motion_outcome, motion_report
    shot = {"camera_movement": "固定机位",
            "motion_desc": "前两秒固定机位，随后横移",
            "ff_desc": "院门口", "lf_desc": "院子里"}
    assert motion_outcome(shot) == "suspected"
    assert any(r["time_relation"] == "unknown" for r in motion_report(shot))


def test_negated_pan_is_suspected_not_definite():
    from shared.shot_motion_checks import motion_outcome
    shot = {"camera_movement": "固定机位",
            "motion_desc": "不要镜头掠过荷丛，保持机位",
            "ff_desc": "船在荷丛中", "lf_desc": "船滑出荷丛"}
    assert motion_outcome(shot) == "suspected"


def test_quoted_pan_is_suspected():
    from shared.shot_motion_checks import motion_outcome
    shot = {"camera_movement": "固定机位",
            "motion_desc": "旁白说“镜头掠过荷丛”，画面保持不动",
            "ff_desc": "船在荷丛中", "lf_desc": "船滑出荷丛"}
    assert motion_outcome(shot) == "suspected"


def test_conditional_pan_is_suspected():
    from shared.shot_motion_checks import motion_outcome
    shot = {"camera_movement": "固定机位",
            "motion_desc": "若镜头掠过荷丛则能看到老农，否则看不到",
            "ff_desc": "船在荷丛中", "lf_desc": "船滑出荷丛"}
    assert motion_outcome(shot) == "suspected"


def test_moving_camera_is_no_conflict():
    from shared.shot_motion_checks import motion_outcome
    shot = {"camera_movement": "镜头横移",
            "motion_desc": "镜头横移掠过荷丛",
            "ff_desc": "船在荷丛中", "lf_desc": "船滑出荷丛，停在开阔水面"}
    assert motion_outcome(shot) == "no_conflict"


def test_different_time_state_is_no_conflict():
    from shared.shot_motion_checks import motion_outcome
    shot = {"camera_movement": "固定机位",
            "motion_desc": "杨旭吹箫",
            "prompt": "杨旭双手持箫吹奏",
            "ff_desc": "杨旭持箫未吹", "lf_desc": "杨旭吹箫"}
    assert motion_outcome(shot) == "no_conflict"


def test_similarity_is_signal_not_warning():
    from shared.shot_motion_checks import motion_report, motion_warnings
    shot = {"camera_movement": "固定机位",
            "motion_desc": "船缓缓滑出荷丛",
            "ff_desc": "黄昏荷湾，船半隐，杨旭持箫未吹",
            "lf_desc": "黄昏荷湾，船半隐，杨旭持箫吹奏"}
    classes = [r["class"] for r in motion_report(shot)]
    assert "signal" in classes
    assert motion_warnings(shot) == []


def test_density_is_signal():
    from shared.shot_motion_checks import motion_report
    shot = {"camera_movement": "固定机位", "duration_sec": 5.0,
            "motion_desc": "甲；乙；丙",
            "ff_desc": "甲", "lf_desc": "丙"}
    codes = [r["code"] for r in motion_report(shot)]
    assert "motion_density_high" in codes


def test_plain_shot_is_no_conflict():
    from shared.shot_motion_checks import motion_report, motion_outcome
    shot = {"camera_movement": "固定机位", "duration_sec": 5.0,
            "motion_desc": "杨旭吹箫，听香撑伞",
            "ff_desc": "船在荷丛中，杨旭持箫未吹",
            "lf_desc": "船在荷丛中，杨旭吹箫，听香侧身"}
    assert motion_report(shot) == []
    assert motion_outcome(shot) == "no_conflict"
