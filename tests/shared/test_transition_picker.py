# -*- coding: utf-8 -*-
"""M8.4 按镜头关系智能选转场：只产出合法 kind，显式转场优先."""
from __future__ import annotations

from shared.media_contract import LEGAL_BOUNDARY_TRANSITIONS
from shared.transition_picker import choose_transition, fill_missing_transitions


def _shot(**extra):
    base = {"scene": "庭院", "location": "庭院", "ff_desc": "人物站在院中",
            "lf_desc": "人物仍站在院中", "motion_desc": "抬手"}
    base.update(extra)
    return base


def test_scene_change_uses_fade():
    choice = choose_transition(_shot(scene="庭院"), _shot(scene="街市"))
    assert choice["kind"] == "fade"
    assert choice["duration_sec"] == 0.5
    assert choice["kind"] in LEGAL_BOUNDARY_TRANSITIONS


def test_space_change_in_same_scene_uses_short_fade():
    choice = choose_transition(
        _shot(location="船头"), _shot(location="岸边"))
    assert choice["kind"] == "fade"
    assert choice["duration_sec"] == 0.4


def test_time_jump_uses_fadeblack():
    choice = choose_transition(
        _shot(), _shot(motion_desc="次日清晨，院中无人"))
    assert choice["kind"] == "fadeblack"
    assert choice["duration_sec"] == 0.6


def test_action_continuity_uses_cut():
    choice = choose_transition(
        _shot(motion_desc="对峙"), _shot(motion_desc="挥拳打斗，撞上廊柱"))
    assert choice["kind"] == "cut"
    assert choice["duration_sec"] == 0.0


def test_default_continuity_uses_cut():
    choice = choose_transition(_shot(), _shot())
    assert choice["kind"] == "cut"


def test_explicit_transition_is_never_overwritten():
    shots = [
        _shot(clip_transition="淡入淡出，0.5s"),
        _shot(scene="街市", clip_transition="硬切"),
        _shot(scene="河岸"),
    ]
    suggestions = fill_missing_transitions(shots)
    assert len(suggestions) == 3
    assert suggestions[0] is None
    assert suggestions[1] is None, "显式 clip_transition 必须优先"
    assert suggestions[2] is not None and suggestions[2]["kind"] == "fade"


def test_all_suggestions_are_legal_kinds():
    shots = [
        _shot(), _shot(scene="街市"), _shot(scene="街市", motion_desc="回忆：旧事"),
        _shot(scene="街市", location="岸边"), _shot(scene="河岸", motion_desc="挥拳"),
    ]
    for item in fill_missing_transitions(shots):
        if item is not None:
            assert item["kind"] in LEGAL_BOUNDARY_TRANSITIONS
            assert item["duration_sec"] >= 0
