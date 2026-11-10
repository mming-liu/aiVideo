# -*- coding: utf-8 -*-
"""R2-4：声音—节拍承载闭环预检（匿名自足样本）."""
from __future__ import annotations

from step01_script_gen.voice_carrier_closure import closure

NOVEL = "甲进屋。甲说会走。乙留下。"


def _plan(*, facts, beats, dialogue=None, narration=None, uids=None):
    return {
        "required_facts": facts,
        "fact_uids": uids or ["f%02d" % (i + 1) for i in range(len(facts))],
        "shot_beats": beats,
        "dialogue_lines": dialogue or [],
        "narration_lines": narration or [],
        "main_events": ["事件"],
        "omittable": [],
    }


def _fact(claim, ev, carrier):
    return "%s｜原文证据：“%s”｜承载：%s" % (claim, ev, carrier)


def _beat(body, carrier):
    return "节拍：%s｜本镜信息目的：推进｜原文依据：“%s”｜载体：%s" % (body, body, carrier)


def _codes(plan):
    return [p["code"] for p in closure(plan, NOVEL)["problems"]]


def test_zero_voices_is_closed():
    plan = _plan(facts=[_fact("甲进屋", "甲进屋。", "B1/画面")],
                 beats=[_beat("甲推门进屋", "画面")])
    assert _codes(plan) == []


def test_one_voice_is_closed():
    plan = _plan(facts=[_fact("甲说话", "甲说会走。", "B1/旁白1")],
                 beats=[_beat("甲开口", "旁白1")],
                 narration=[{"text": "甲说会走。", "source": "甲说会走。",
                             "fact_refs": [1]}])
    assert _codes(plan) == []


def test_multiple_voices_are_closed_when_each_has_one_beat():
    plan = _plan(
        facts=[_fact("甲说话", "甲说会走。", "B1/旁白1"),
               _fact("乙留下", "乙留下。", "B2/旁白2")],
        beats=[_beat("甲开口", "旁白1"), _beat("乙留在屋里", "旁白2")],
        narration=[{"text": "甲说会走。", "source": "甲说会走。", "fact_refs": [1]},
                   {"text": "乙留下。", "source": "乙留下。", "fact_refs": [2]}])
    assert _codes(plan) == []


def test_orphan_voice_is_reported_with_fix():
    plan = _plan(
        facts=[_fact("甲说话", "甲说会走。", "B1/旁白1")],
        beats=[_beat("甲开口", "旁白1")],
        narration=[{"text": "甲说会走。", "source": "甲说会走。", "fact_refs": [1]},
                   {"text": "乙留下。", "source": "乙留下。", "fact_refs": []}])
    probs = closure(plan, NOVEL)["problems"]
    assert [p["code"] for p in probs] == ["voice_unplaced"]
    assert probs[0]["path"] == "narration_lines[1]"
    assert "｜载体：旁白2" in probs[0]["fix"]


def test_duplicate_occupancy_is_reported():
    plan = _plan(
        facts=[_fact("甲说话", "甲说会走。", "B1/旁白1")],
        beats=[_beat("甲开口", "旁白1"), _beat("甲又说", "旁白1")],
        narration=[{"text": "甲说会走。", "source": "甲说会走。", "fact_refs": [1]}])
    assert "voice_multiply_placed" in _codes(plan)


def test_dangling_voice_number_is_reported():
    plan = _plan(
        facts=[_fact("甲说话", "甲说会走。", "B1/旁白1")],
        beats=[_beat("甲开口", "旁白1"), _beat("空", "旁白9")],
        narration=[{"text": "甲说会走。", "source": "甲说会走。", "fact_refs": [1]}])
    assert "voice_carrier_dangling" in _codes(plan)


def test_fact_and_beat_must_point_to_each_other():
    plan = _plan(
        facts=[_fact("甲说话", "甲说会走。", "B1/旁白2")],
        beats=[_beat("甲开口", "旁白1")],
        narration=[{"text": "甲说会走。", "source": "甲说会走。", "fact_refs": []},
                   {"text": "乙留下。", "source": "乙留下。", "fact_refs": []}])
    assert "voice_fact_ref_mismatch" in _codes(plan)


def test_missing_beat_and_empty_display_are_reported():
    plan = _plan(facts=[_fact("甲进屋", "甲进屋。", "B9/画面")],
                 beats=[_beat("甲推门进屋", "画面")])
    assert "fact_carrier_beat_missing" in _codes(plan)
    plan2 = _plan(facts=[_fact("甲进屋", "甲进屋。", "B1/画面")],
                  beats=[_beat("", "画面")])
    assert "fact_display_empty" in _codes(plan2)


def test_shared_narration_is_recorded_not_auto_rejected():
    plan = _plan(
        facts=[_fact("甲说话", "甲说会走。", "B1/旁白1"),
               _fact("甲要走", "甲说会走。", "B1/旁白1")],
        beats=[_beat("甲开口", "旁白1")],
        narration=[{"text": "甲说会走。", "source": "甲说会走。", "fact_refs": [1, 2]}])
    out = closure(plan, NOVEL)
    assert [p["code"] for p in out["problems"]] == []
    assert out["shared"] and out["shared"][0]["fact_refs"] == [1, 2]


def test_non_standard_fact_uids_are_preserved_in_reading():
    plan = _plan(facts=[_fact("甲进屋", "甲进屋。", "B1/画面")],
                 beats=[_beat("甲推门进屋", "画面")], uids=["x9"])
    out = closure(plan, NOVEL)
    assert out["facts"][0]["uid"] == "x9"
    assert out["problems"] == []
