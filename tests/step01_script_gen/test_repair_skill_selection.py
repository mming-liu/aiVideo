# -*- coding: utf-8 -*-
"""S03：技能段依赖必须按**结构化码**判定（2026-11-07 P1 回归）."""
from step01_script_gen import main as m


def _sec(picked, audit=None):
    return m._repair_skill_sections(picked, audit or {})


def test_structural_kf_needs_no_skills():
    sections, unclassified = _sec({"shot_1": ["[kf_segment_missing] 关键帧缺段"]})
    assert sections == set() and unclassified is False


def test_camera_code_selects_craft_even_without_keywords():
    sections, unclassified = _sec({"shot_1": ["[camera_move_invalid] 描述不符"]})
    assert "craft" in sections and unclassified is False


def test_unknown_code_keeps_full_context_and_reports():
    sections, unclassified = _sec({"shot_1": ["[totally_new_problem] 新题型"]})
    assert unclassified is True


def test_mixed_batch_with_unknown_also_keeps_full_context():
    sections, unclassified = _sec({"shot_1": ["[camera_move_invalid] 运镜"],
                                   "shot_2": ["[totally_new_problem] 新题型"]})
    assert "craft" in sections and unclassified is True


def test_audit_family_is_classified_by_message_text():
    sections, unclassified = _sec({"shot_1": []},
                                  {"_a": [{"code": "model_audit_TEXT_ERROR",
                                           "message": "旁白与画面不符"}]})
    assert sections == set() and unclassified is False
