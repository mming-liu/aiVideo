"""Timecode must not manufacture invalid frames from valid authored timing."""
import copy

import pytest


@pytest.mark.parametrize("index", [11, 23.5])
def test_open_tail_preserves_declared_container(index):
    from step01_script_gen.timecode import _compute_timecodes, project_post_split_metrics
    shot = {"index": index, "duration_sec": 5.0,
            "motion_desc": "前2秒：旅人举灯；2秒后：旅人登桥",
            "keyframes": [{"t_sec": 1.0}, {"t_sec": 3.5}]}
    script = {"fps": 24, "shots": [copy.deepcopy(shot)]}
    projected = project_post_split_metrics([shot], fps=24, shot_cap=15)
    _compute_timecodes(script)
    assert script["shots"][0]["duration_sec"] == 5.0
    assert projected["duration_sec"] == script["total_duration"]
    assert script["shots"][0]["keyframes"] == shot["keyframes"]
    first = copy.deepcopy(script)
    _compute_timecodes(script)
    assert script == first


def test_keyframe_lower_bound_survives_frame_alignment():
    from step01_script_gen.timecode import _compute_timecodes, project_post_split_metrics
    shot = {"index": 7, "motion_desc": "前2秒：举灯；2秒后：转身",
            "keyframes": [{"t_sec": 4.8}]}
    script = {"fps": 24, "shots": [shot]}
    projected = project_post_split_metrics([shot], fps=24, shot_cap=15)
    _compute_timecodes(script)
    duration = shot["duration_sec"]
    assert duration >= 4.8
    assert abs(duration * 24 - round(duration * 24)) < 1e-6
    assert abs(projected["duration_sec"] - duration) <= 0.01


def test_narration_projection_matches_execution():
    from step01_script_gen.timecode import _compute_timecodes, project_post_split_metrics
    shot = {"index": 2, "narration_segment": "山间的旅人在暮色中沿着石阶走向远处的村庄。"}
    script = {"fps": 24, "shots": [shot]}
    projected = project_post_split_metrics([shot], fps=24, shot_cap=15)
    _compute_timecodes(script)
    assert projected["duration_sec"] == script["total_duration"]
    assert shot["duration_sec"] > 4


def test_timeline_serializer_does_not_reestimate():
    from step01_script_gen.timecode import write_timeline_from_durations
    shot = {"index": 2, "duration_sec": 4, "motion_desc": "0-6秒：转身",
            "keyframes": [{"t_sec": 5}]}
    script = {"shots": [shot]}
    write_timeline_from_durations(script)
    assert shot["duration_sec"] == 4
