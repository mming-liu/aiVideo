# -*- coding: utf-8 -*-
from __future__ import annotations
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def test_fixed_camera_with_pan_text_warns():
    from shared.shot_motion_checks import motion_warnings
    shot = {"camera_movement": "固定机位",
            "motion_desc": "前2秒船滑出；后1秒镜头掠过荷丛",
            "ff_desc": "船在荷丛中", "lf_desc": "船滑出荷丛，停在开阔水面"}
    codes = [w["code"] for w in motion_warnings(shot)]
    assert "camera_motion_conflict" in codes


def test_moving_camera_with_pan_text_is_clean():
    from shared.shot_motion_checks import motion_warnings
    shot = {"camera_movement": "镜头横移",
            "motion_desc": "镜头横移掠过荷丛",
            "ff_desc": "船在荷丛中", "lf_desc": "船滑出荷丛，停在开阔水面"}
    assert motion_warnings(shot) == []


def test_near_identical_frames_with_displacement_warns():
    from shared.shot_motion_checks import motion_warnings
    shot = {"camera_movement": "固定机位",
            "motion_desc": "船缓缓滑出荷丛",
            "ff_desc": "黄昏荷湾，船半隐，杨旭持箫未吹",
            "lf_desc": "黄昏荷湾，船半隐，杨旭持箫吹奏"}
    codes = [w["code"] for w in motion_warnings(shot)]
    assert "frame_displacement_missing" in codes


def test_fixed_camera_without_pan_is_clean():
    from shared.shot_motion_checks import motion_warnings
    shot = {"camera_movement": "固定机位", "motion_desc": "杨旭吹箫，听香侧身",
            "ff_desc": "船在荷丛中，杨旭持箫未吹", "lf_desc": "船在荷丛中，杨旭吹箫，听香侧身"}
    assert motion_warnings(shot) == []
