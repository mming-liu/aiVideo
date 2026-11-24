# -*- coding: utf-8 -*-
"""S13 受限声音分配（v2）离线验收：拆分、多承载、证据与预算门.

匿名样本；不读 `.tmp` / `output`，不发真实请求。
"""
from __future__ import annotations

import pytest

from step01_script_gen import lightweight_plan as lw
from step01_script_gen import voice_allocation as va
from step01_script_gen.outline import plan_acceptance_problems

pytestmark = pytest.mark.v2_request

NOVEL = ("黄昏时分，甲站在渡口。\n"
         "甲说道：“这河水深得很，夜里行船最是危险，你若执意要走，出了事可怨不得我。”\n"
         "乙说道：“我要连夜过河送药，不能再等了。”\n"
         "甲说道：“那你要小心些，风浪大时靠岸停一停。”\n"
         "乙摇头。\n")
L1 = "这河水深得很，夜里行船最是危险，你若执意要走，出了事可怨不得我。"


def _b(text, src, kind="none", vt="", sp=""):
    return {"text": text, "purpose": "推进", "source": src, "voice_kind": kind,
            "voice_text": vt, "speaker": sp}


def _answer(evidence=L1, all_voice_facts=False):
    return {
        "characters": ["甲", "乙"], "emotional_arc": "迟疑", "climax": "劝阻",
        "end_hook": "乙摇头", "omittable": [],
        "events": [
            {"summary": "甲到渡口", "beats": [
                _b("甲站在渡口", "黄昏时分，甲站在渡口。"),
                _b("甲开口劝阻", "甲说道", "dialogue", L1, "甲")]},
            {"summary": "乙说明来意", "beats": [
                _b("乙回答", "乙说道", "dialogue", "我要连夜过河送药，不能再等了。", "乙")]},
            {"summary": "甲叮嘱", "beats": [
                _b("甲叮嘱", "甲说道", "dialogue", "那你要小心些，风浪大时靠岸停一停。", "甲"),
                _b("乙摇头", "乙摇头。")]},
        ],
        "facts": [
            {"claim": "甲劝乙不要连夜走", "evidence": evidence, "event": 1, "beat": 2,
             "carrier": "voice"},
            {"claim": "乙摇头", "evidence": "乙摇头。", "event": 3, "beat": 2,
             "carrier": "visual"},
        ] + ([{"claim": "甲叮嘱乙小心", "evidence": "那你要小心些，风浪大时靠岸停一停。",
               "event": 3, "beat": 1, "carrier": "voice"}] if all_voice_facts else []),
    }


def _ctx(limits=None, evidence=L1, all_voice_facts=False):
    return va.build_voice_choices(_answer(evidence, all_voice_facts), NOVEL, limits=limits or {
        "single_shot_sec": 11, "max_sec": 90, "max_han": 280, "max_lines": 10})


def test_split_voice_and_multi_carrier_fact_reaches_a_valid_plan():
    ctx = _ctx()
    units = [u["unit"] for u in ctx["voices"]["D1"]["units"] if u["in_evidence"]][:2]
    assert len(units) == 2
    cand, problems = va.apply_voice_allocation(
        _answer(), {"voices": [{"id": "D1", "units": units}],
                    "facts": [{"id": "f01", "units": ["D1:1", "D1:2"]}]},
        ctx, novel_text=NOVEL)
    assert problems == [] and cand is not None
    plan, build_problems = lw.build_plan(cand, NOVEL)
    assert plan is not None and build_problems == []
    assert "、" in [f for f in plan["required_facts"] if f.startswith("甲劝乙")][0]
    assert plan_acceptance_problems(plan, NOVEL) == []


def test_a_unit_that_is_not_source_text_is_rejected():
    ctx = _ctx(evidence="这河水深得很，")
    bad = ctx["voices"]["D1"]["units"][0]["unit"]
    ctx["voices"]["D1"]["units"][0]["text"] = "目录之外的一句"
    _cand, problems = va.apply_voice_allocation(
        _answer("这河水深得很，"), {"voices": [{"id": "D1", "units": [bad]}], "facts": []},
        ctx, novel_text=NOVEL)
    assert problems and all(p["code"] == "allocation_unit_outside_evidence" for p in problems)


def test_units_from_another_voice_are_rejected():
    ctx = _ctx()
    other = next(u["unit"] for u in ctx["voices"]["D2"]["units"])
    _cand, problems = va.apply_voice_allocation(
        _answer(), {"voices": [{"id": "D1", "units": [other]}], "facts": []},
        ctx, novel_text=NOVEL)
    assert [p["code"] for p in problems] == ["allocation_unit_unknown"]


def test_line_budget_drops_only_a_fact_free_voice():
    ctx = _ctx({"single_shot_sec": 11, "max_sec": 90, "max_han": 280, "max_lines": 3})
    units = [u["unit"] for u in ctx["voices"]["D1"]["units"] if u["in_evidence"]][:2]
    cand, problems = va.apply_voice_allocation(
        _answer(), {"voices": [{"id": "D1", "units": units}],
                    "facts": [{"id": "f01", "units": ["D1:1", "D1:2"]}]},
        ctx, novel_text=NOVEL)
    assert problems == [] and cand is not None
    dropped = cand.get("allocation_dropped") or []
    assert len(dropped) == 1 and dropped[0]["text"]
    plan, bprobs = lw.build_plan(cand, NOVEL)
    assert plan is not None and bprobs == []
    assert plan_acceptance_problems(plan, NOVEL) == []


def test_budget_conflict_when_no_droppable_fact_free_voice_exists():
    ctx = _ctx({"single_shot_sec": 11, "max_sec": 90, "max_han": 280, "max_lines": 3},
               all_voice_facts=True)
    units = [u["unit"] for u in ctx["voices"]["D1"]["units"] if u["in_evidence"]][:2]
    cand, problems = va.apply_voice_allocation(
        _answer(all_voice_facts=True),
        {"voices": [{"id": "D1", "units": units}],
         "facts": [{"id": "f01", "units": ["D1:1", "D1:2"]}]},
        ctx, novel_text=NOVEL)
    assert cand is None
    assert [p["code"] for p in problems] == ["allocation_budget_conflict"]
