# -*- coding: utf-8 -*-
"""M2.2 确定性跨字段检查：跨镜时段/光线、邻镜状态、镜内运镜冲突."""
from __future__ import annotations

from step01_script_gen.cross_field_checks import cross_field_issues


def _script(**shots):
    return {"shots": [dict(index=i + 1, **s) for i, s in enumerate(shots.values())]}


def _codes(script):
    issues, failures = cross_field_issues(script)
    assert failures == []
    return [i.code for i in issues]


def test_time_reversal_same_scene_is_detected():
    script = _script(
        first={"scene": "河湾", "location": "河湾", "ff_desc": "黄昏河湾，夕阳西沉",
               "lf_desc": "黄昏河湾，夕阳西沉，船头微晃"},
        second={"scene": "河湾", "location": "河湾", "ff_desc": "正午河湾，阳光直射",
                "lf_desc": "正午河湾"},
    )
    assert "cross_shot_time_reversal" in _codes(script)


def test_time_reversal_not_flagged_across_scene_or_with_jump_marker():
    across_scene = _script(
        first={"scene": "河湾", "location": "河湾", "lf_desc": "黄昏，夕阳西沉"},
        second={"scene": "庭院", "location": "庭院", "ff_desc": "正午，阳光直射"},
    )
    assert "cross_shot_time_reversal" not in _codes(across_scene)

    recalled = _script(
        first={"scene": "河湾", "location": "河湾", "lf_desc": "黄昏，夕阳西沉"},
        second={"scene": "河湾", "location": "河湾", "ff_desc": "回忆：正午，阳光直射"},
    )
    assert "cross_shot_time_reversal" not in _codes(recalled)


def test_day_night_lighting_conflict_is_detected():
    script = _script(
        first={"scene": "河湾", "location": "河湾", "lf_desc": "月光洒在水面"},
        second={"scene": "河湾", "location": "河湾", "ff_desc": "阳光照亮船头"},
    )
    assert "cross_shot_lighting_conflict" in _codes(script)


def test_adjacent_state_break_requires_no_bridge():
    broken = _script(
        first={"scene": "河湾", "location": "河湾", "lf_desc": "他倒地不起，伏在船板"},
        second={"scene": "河湾", "location": "河湾", "ff_desc": "他站立在船头"},
    )
    assert "adjacent_state_break" in _codes(broken)

    bridged = _script(
        first={"scene": "河湾", "location": "河湾", "lf_desc": "他倒地不起，伏在船板"},
        second={"scene": "河湾", "location": "河湾",
                "ff_desc": "他已被扶起，站立在船头"},
    )
    assert "adjacent_state_break" not in _codes(bridged)


def test_intra_shot_camera_opposites_conflict():
    """同一段内**同时**要求推近与拉远 → 冲突（旧的「推近后随即拉远」是先后，
    属合法分段运镜，见下一条）。"""
    script = _script(
        one={"camera_movement": "镜头缓慢推近并同时拉远", "motion_desc": "人物抬头"},
    )
    assert "intra_shot_camera_conflict" in _codes(script)


def test_intra_shot_camera_ordered_opposites_are_legal():
    """「推近后随即拉远」是显式先后顺序，不是同一时刻的互斥要求."""
    script = _script(
        one={"camera_movement": "镜头缓慢推近后随即拉远", "motion_desc": "人物抬头"},
    )
    assert "intra_shot_camera_conflict" not in _codes(script)


def test_intra_shot_camera_segmented_is_legal():
    script = _script(
        one={"camera_movement": "先推近，再拉远", "motion_desc": "前2秒推近，后2秒拉远"},
    )
    assert "intra_shot_camera_conflict" not in _codes(script)


def test_fixed_camera_with_motion_is_conflict():
    script = _script(
        one={"camera_movement": "固定机位", "motion_desc": "镜头跟拍人物穿过回廊"},
    )
    assert "intra_shot_camera_conflict" in _codes(script)


def test_camera_conflict_issue_is_visual_risk():
    script = _script(
        one={"camera_movement": "固定机位", "motion_desc": "镜头跟拍人物"},
    )
    issues, _ = cross_field_issues(script)
    hit = [i for i in issues if i.code == "intra_shot_camera_conflict"]
    assert hit and hit[0].severity == "visual_risk"
    assert hit[0].field_path == "camera_movement"
