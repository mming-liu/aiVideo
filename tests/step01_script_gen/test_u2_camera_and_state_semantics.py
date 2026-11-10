# -*- coding: utf-8 -*-
"""U2 验收：M2.2 运镜主体/否定/时间关系 + 跨镜状态的实体级匹配.

样本全部匿名、自足（无现场数据、无外部调用）。从 cross_field_issues() 实际
入口断言「合法不拦 / 明确冲突拦住 / 归属不明只提示」。
"""
from __future__ import annotations

import copy

import pytest

from step01_script_gen.cross_field_checks import cross_field_issues
from step01_script_gen.continuity_semantics import extract_camera_statements


def _shot(**over):
    shot = {"index": 1, "ff_desc": "", "lf_desc": "", "motion_desc": "",
            "camera_movement": "", "keyframes": [],
            "frame_layouts": {"ff": [], "lf": [], "kfs": []}}
    shot.update(over)
    return shot


def _script(*shots, characters=None):
    return {"shots": [dict(s, index=i + 1) if "index" not in s else s
                      for i, s in enumerate(shots)],
            "characters": characters or []}


def _camera(script):
    return [i for i in cross_field_issues(script)[0]
            if i.code == "intra_shot_camera_conflict"]


# ---------------------------------------------------------------------------
# 相机陈述提取：主体 / 否定 / 分段
# ---------------------------------------------------------------------------
def test_person_action_is_not_a_camera_statement():
    shot = _shot(motion_desc="他起身向前推门，随后抬头看向画外")
    subjects = {s.subject for s in extract_camera_statements(shot)}
    assert "camera" not in subjects


def test_vehicle_approach_is_not_a_camera_push():
    shot = _shot(motion_desc="小船缓缓靠近岸边，船头压过低垂的荷叶")
    assert not any(s.subject == "camera" for s in extract_camera_statements(shot))


def test_negation_binds_its_own_clause_only():
    shot = _shot(camera_movement="镜头缓慢推近",
                 motion_desc="镜头保持推近，禁止拉远")
    assert not _camera(_script(shot))


def test_explicit_segments_split_the_chain():
    shot = _shot(camera_movement="先固定机位，随后缓慢推近",
                 motion_desc="前2秒人物不动，后2秒镜头推近")
    assert not _camera(_script(shot))


# ---------------------------------------------------------------------------
# 合法不阻断
# ---------------------------------------------------------------------------
def test_person_advancing_without_camera_noun_is_not_a_camera_push():
    """「甲侧身让出小径推进」是人物的推进，不是镜头推进（无相机名词 → 不硬判）."""
    shot = _shot(camera_movement="固定机位",
                 motion_desc="1.5-3秒：甲侧身让出小径推进")
    assert not _camera(_script(shot))


def test_fixed_camera_with_person_walking_is_legal():
    shot = _shot(camera_movement="固定机位",
                 motion_desc="人物起身走向船头，衣袍被风吹起")
    assert not _camera(_script(shot))


def test_person_still_camera_pushes_in_is_legal():
    shot = _shot(camera_movement="缓慢推近",
                 motion_desc="人物不动，镜头缓慢推近至半身")
    assert not _camera(_script(shot))


def test_negated_opposite_is_not_a_conflict():
    shot = _shot(camera_movement="缓慢推近",
                 motion_desc="镜头缓缓推近，不得拉远或回退")
    assert not _camera(_script(shot))


def test_stop_being_fixed_then_push_in_is_legal():
    shot = _shot(camera_movement="不再固定机位，开始缓慢推近",
                 motion_desc="前2秒固定，后2秒推近")
    assert not _camera(_script(shot))


def test_fixed_then_push_in_is_legal():
    shot = _shot(camera_movement="固定机位，随后推近至人物特写",
                 motion_desc="先静置，再推近")
    assert not _camera(_script(shot))


def test_ordered_opposites_within_one_clause_are_legal():
    shot = _shot(camera_movement="镜头推近后随即拉远")
    assert not _camera(_script(shot))


# ---------------------------------------------------------------------------
# 明确冲突
# ---------------------------------------------------------------------------
def test_simultaneous_opposites_conflict():
    shot = _shot(camera_movement="镜头缓慢推近并同时拉远")
    hits = _camera(_script(shot))
    assert hits and hits[0].severity == "visual_risk"


def test_fixed_camera_versus_push_in_conflicts():
    shot = _shot(camera_movement="固定机位", motion_desc="镜头缓缓推近")
    hits = _camera(_script(shot))
    assert hits and hits[0].severity == "visual_risk"
    assert hits[0].field_path in ("camera_movement", "motion_desc")


def test_fixed_camera_versus_tracking_conflicts():
    shot = _shot(camera_movement="固定机位", motion_desc="镜头跟拍人物穿过回廊")
    assert _camera(_script(shot))


def test_person_segments_do_not_exempt_camera_conflict():
    """motion_desc 里人物动作的「先…再…」不能豁免 camera_movement 的真冲突."""
    shot = _shot(camera_movement="镜头推近，同时拉远",
                 motion_desc="人物先抬手，再转身离去")
    assert _camera(_script(shot))


def test_segmented_person_action_does_not_hide_cross_field_conflict():
    shot = _shot(camera_movement="固定机位",
                 motion_desc="前2秒人物先抬手，后2秒镜头推近")
    hits = _camera(_script(shot))
    assert hits and hits[0].severity == "visual_risk"


# ---------------------------------------------------------------------------
# 证据不足 → 只提示
# ---------------------------------------------------------------------------
def test_ambiguous_subject_yields_advisory_not_hard_block():
    """同一段里出现互斥方向、但没写相机主体 → 只提示，不硬判."""
    shot = _shot(motion_desc="上升并同时下降")
    hits = _camera(_script(shot))
    assert hits and all(h.severity == "advisory" for h in hits)


def test_explicit_sequence_with_ambiguous_subject_is_not_flagged():
    """「上升，随后下降」是显式先后顺序，不构成冲突（即便主体未写明）."""
    shot = _shot(motion_desc="上升，随后下降")
    assert not _camera(_script(shot))


def test_hard_conflict_is_repairable_and_has_evidence():
    shot = _shot(camera_movement="固定机位", motion_desc="镜头缓缓推近")
    hit = _camera(_script(shot))[0]
    assert hit.repairable is True and hit.evidence


# ---------------------------------------------------------------------------
# 跨镜状态：同一实体才比较
# ---------------------------------------------------------------------------
def _layouts(ff, lf):
    return {"ff": ff, "lf": lf, "kfs": []}


def test_same_entity_state_break_is_reported():
    first = _shot(index=1, scene="河湾", location="河湾",
                  lf_desc="甲倒地不起，伏在船板",
                  frame_layouts=_layouts(
                      [{"name": "甲", "pose": "站立"}],
                      [{"name": "甲", "pose": "倒地不起"}]))
    second = _shot(index=2, scene="河湾", location="河湾",
                   ff_desc="甲站立在船头，衣袍整洁",
                   frame_layouts=_layouts(
                       [{"name": "甲", "pose": "站立"}],
                       [{"name": "甲", "pose": "站立"}]))
    issues = [i for i in cross_field_issues(_script(first, second))[0]
              if i.code == "adjacent_state_break"]
    assert len(issues) == 1
    assert issues[0].entity_id == "甲"
    assert "倒地" in issues[0].message


def test_different_entities_state_change_is_not_compared():
    """甲的终态（倒地）与乙的初态（站立）不是同一条状态链."""
    first = _shot(index=1, scene="河湾", location="河湾",
                  lf_desc="甲倒地不起，乙站在船头",
                  frame_layouts=_layouts(
                      [{"name": "甲", "pose": "站立"}, {"name": "乙", "pose": "站立"}],
                      [{"name": "甲", "pose": "倒地不起"}, {"name": "乙", "pose": "站立"}]))
    second = _shot(index=2, scene="河湾", location="河湾",
                   ff_desc="甲仍伏在船板，乙站立在船头",
                   frame_layouts=_layouts(
                       [{"name": "甲", "pose": "伏在船板"}, {"name": "乙", "pose": "站立"}],
                       [{"name": "甲", "pose": "伏在船板"}, {"name": "乙", "pose": "站立"}]))
    issues = [i for i in cross_field_issues(_script(first, second))[0]
              if i.code == "adjacent_state_break"]
    assert issues == []


def test_text_path_requires_same_explicit_subject():
    characters = [{"identifier": "甲"}, {"identifier": "乙"}]
    first = _shot(index=1, scene="河湾", location="河湾", lf_desc="甲倒地不起")
    second = _shot(index=2, scene="河湾", location="河湾", ff_desc="乙站立在船头")
    issues = [i for i in cross_field_issues(_script(first, second, characters=characters))[0]
              if i.code == "adjacent_state_break"]
    assert issues == []


def test_text_path_same_subject_without_bridge_is_reported():
    characters = [{"identifier": "甲"}]
    first = _shot(index=1, scene="河湾", location="河湾", lf_desc="甲倒地不起")
    second = _shot(index=2, scene="河湾", location="河湾", ff_desc="甲站立在船头")
    issues = [i for i in cross_field_issues(_script(first, second, characters=characters))[0]
              if i.code == "adjacent_state_break"]
    assert len(issues) == 1 and issues[0].entity_id == "甲"


def test_text_path_transition_basis_is_respected():
    characters = [{"identifier": "甲"}]
    first = _shot(index=1, scene="河湾", location="河湾", lf_desc="甲倒地不起")
    second = _shot(index=2, scene="河湾", location="河湾", ff_desc="甲已被扶起，站立在船头")
    issues = [i for i in cross_field_issues(_script(first, second, characters=characters))[0]
              if i.code == "adjacent_state_break"]
    assert issues == []


def test_unknown_subject_keeps_uncertainty_as_advisory():
    first = _shot(index=1, scene="河湾", location="河湾", lf_desc="两人倒地不起")
    second = _shot(index=2, scene="河湾", location="河湾", ff_desc="有人站立在船头")
    issues = [i for i in cross_field_issues(_script(first, second))[0]
              if i.code == "adjacent_state_break"]
    assert issues and all(i.severity == "advisory" for i in issues)


def test_time_jump_marker_suppresses_state_break():
    first = _shot(index=1, scene="河湾", location="河湾", lf_desc="甲倒地不起")
    second = _shot(index=2, scene="河湾", location="河湾",
                   ff_desc="次日，甲站立在船头")
    issues = [i for i in cross_field_issues(_script(first, second))[0]
              if i.code == "adjacent_state_break"]
    assert issues == []


# ---------------------------------------------------------------------------
# 纯读
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("script", [
    _script(_shot(camera_movement="固定机位", motion_desc="镜头推近")),
    _script(_shot(motion_desc="人物不动，镜头推近")),
])
def test_checks_are_read_only(script):
    before = copy.deepcopy(script)
    cross_field_issues(script)
    assert script == before
