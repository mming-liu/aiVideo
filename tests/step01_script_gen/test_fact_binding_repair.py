# -*- coding: utf-8 -*-
"""S16 事实重绑：只追加既有视听承载，不改内容/声音."""
from __future__ import annotations

from step01_script_gen import lightweight_plan as lw
from step01_script_gen.fact_binding_repair import rebind_fact_carriers
from step01_script_gen.outline import plan_acceptance_problems

NOVEL = ("黄昏时分，甲站在渡口。\n"
         "甲说道：“这河水深得很，夜里行船最是危险，你若执意要走。”\n"
         "乙说道：“我要连夜过河送药，不能再等了。”\n"
         "乙摇头。\n")
L1 = "这河水深得很，夜里行船最是危险，你若执意要走。"


def _beat(text, src, kind="none", vt="", sp=""):
    return {"text": text, "purpose": "推进", "source": src, "voice_kind": kind,
            "voice_text": vt, "speaker": sp}


def _answer():
    return {
        "characters": ["甲", "乙"], "emotional_arc": "迟疑", "climax": "劝阻",
        "end_hook": "乙摇头", "omittable": [],
        "events": [
            {"summary": "甲到渡口", "beats": [
                _beat("甲站在渡口", "黄昏时分，甲站在渡口。"),
                _beat("甲开口劝阻", "甲说道", "dialogue", L1, "甲")]},
            {"summary": "乙说明来意", "beats": [
                _beat("乙回答", "乙说道", "dialogue", "我要连夜过河送药，不能再等了。", "乙")]},
            {"summary": "乙摇头", "beats": [_beat("乙摇头", "乙摇头。")]},
        ],
        "facts": [{"claim": "甲劝乙不要连夜走", "evidence": L1,
                   "event": 1, "beat": 2, "carrier": "voice"}],
    }


def test_rebind_adds_existing_visual_and_keeps_voice_content():
    plan, probs = lw.build_plan(_answer(), NOVEL)
    assert plan is not None and probs == []
    new, problems, meta = rebind_fact_carriers(
        plan, [{"fact_uid": "f01", "add": [{"beat": 1, "kind": "visual"}]}], novel_text=NOVEL)
    assert problems == [] and new is not None
    fact = [f for f in new["required_facts"] if f.startswith("甲劝乙")][0]
    assert "B2/台词1" in fact and "B1/画面" in fact
    assert plan_acceptance_problems(new, NOVEL) == []
    assert len(new["dialogue_lines"]) == len(plan["dialogue_lines"])
    assert new["shot_beats"] == plan["shot_beats"]
    assert meta["voice_entries"] == 2


def test_rebind_rejects_wrong_kind_and_no_change():
    plan, _ = lw.build_plan(_answer(), NOVEL)
    _new, problems, _ = rebind_fact_carriers(
        plan, [{"fact_uid": "f01", "add": [{"beat": 1, "kind": "dialogue"}]}], novel_text=NOVEL)
    assert [p["code"] for p in problems] == ["binding_kind_mismatch"]
    _new2, problems2, _ = rebind_fact_carriers(
        plan, [{"fact_uid": "f01", "add": [{"beat": 2, "kind": "dialogue"}]}], novel_text=NOVEL)
    assert [p["code"] for p in problems2] == ["binding_no_change"]


def test_rebind_unknown_fact_and_empty_add_are_rejected():
    plan, _ = lw.build_plan(_answer(), NOVEL)
    _new, problems, _ = rebind_fact_carriers(
        plan, [{"fact_uid": "f09", "add": [{"beat": 1, "kind": "visual"}]}], novel_text=NOVEL)
    assert [p["code"] for p in problems] == ["binding_fact_unknown"]
    _new2, problems2, _ = rebind_fact_carriers(
        plan, [{"fact_uid": "f01", "add": []}], novel_text=NOVEL)
    assert [p["code"] for p in problems2] == ["binding_add_empty"]
