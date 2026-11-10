# -*- coding: utf-8 -*-
"""计划证据连续性：不能把两段相隔很远的原文用省略号拼成一条「逐字承载」事实.

样本自足（合成原文，不依赖现场章节）。判据与 2026-11-10 真实失败同源：
f02 的「十六岁接手家业……跻身十大豪门」跨过 163 字，修订轮只能覆盖一半。
"""
from __future__ import annotations

import pytest

from step01_script_gen.plan_feasibility import (
    fact_evidence_noncontiguous, plan_contract_records,
)

NOVEL = "甲把家业交给了十六岁的儿子。旁人都以为要败落。可是谁知从第二年起，生意无往不利，如今已跻身十大豪门之列。"

F_COMPOUND = ("甲十六岁接手家业，数年间跻身十大豪门｜"
              "原文证据：“甲把家业交给了十六岁的儿子……如今已跻身十大豪门之列”"
              "｜承载：B1/旁白1")
F_ATOMIC = ("甲十六岁接手家业｜原文证据：“甲把家业交给了十六岁的儿子。”"
            "｜承载：B1/旁白1")


def _plan(facts, beats, narrations, lines=None):
    return {
        "required_facts": facts,
        "shot_beats": beats,
        "dialogue_lines": lines or [],
        "narration_lines": narrations,
        "fact_uids": ["f%02d" % (i + 1) for i in range(len(facts))],
        "main_events": ["事件"],
        "key_scenes": ["场景"],
        "causal_chain": ["起因→结果"],
        "character_motives": [],
        "omittable": [],
        "time_budget": "约 70 秒",
    }


def test_compound_evidence_is_detected():
    msg = fact_evidence_noncontiguous(F_COMPOUND, NOVEL)
    assert "跨过" in msg and "不是一段连续原文" in msg


def test_atomic_evidence_is_contiguous():
    assert fact_evidence_noncontiguous(F_ATOMIC, NOVEL) == ""


def test_contiguous_quote_with_literal_ellipsis_is_not_flagged():
    novel = "他喊道：「走吧……」然后转身离开。"
    fact = "他喊人离开｜原文证据：“他喊道：「走吧……」然后转身离开。”｜承载：B1/旁白1"
    assert fact_evidence_noncontiguous(fact, novel) == ""


def test_unknown_parts_are_not_judged_here():
    novel = "完全不同的原文。"
    assert fact_evidence_noncontiguous(F_COMPOUND, novel) == ""


def test_voice_carried_compound_fact_is_a_contract_problem():
    plan = _plan([F_COMPOUND],
                 ["节拍：甲接手家业并跻身豪门｜本镜信息目的：交代背景｜原文依据：甲把家业交给儿子｜载体：旁白1"],
                 [{"text": "甲把家业交给了十六岁的儿子。", "source": "甲把家业交给了十六岁的儿子。",
                   "fact_refs": [1]}])
    records = plan_contract_records(plan, novel_text=NOVEL, require_verification=False)
    codes = [r.get("code") for r in records]
    assert "fact_evidence_noncontiguous" in codes
    hit = next(r for r in records if r["code"] == "fact_evidence_noncontiguous")
    assert "拆成两条事实" in hit["reason"]
    assert "画面" in hit["reason"]


def test_visual_carried_compound_fact_is_not_flagged_by_this_rule():
    plan = _plan([F_COMPOUND.replace("B1/旁白1", "B1/画面")],
                 ["节拍：甲接手家业并跻身豪门｜本镜信息目的：交代背景｜原文依据：甲把家业交给儿子｜载体：画面"],
                 [])
    codes = [r.get("code") for r in plan_contract_records(plan, novel_text=NOVEL,
                                                          require_verification=False)]
    assert "fact_evidence_noncontiguous" not in codes


def test_atomic_two_fact_plan_passes():
    f2 = ("甲数年间跻身十大豪门｜原文证据：“如今已跻身十大豪门之列”｜承载：B2/旁白2")
    plan = _plan([F_ATOMIC, f2],
                 ["节拍：甲接手家业｜本镜信息目的：交代继业｜原文依据：甲把家业交给了儿子｜载体：旁白1",
                  "节拍：甲跻身豪门｜本镜信息目的：交代结果｜原文依据：如今已跻身十大豪门之列｜载体：旁白2"],
                 [{"text": "甲把家业交给了十六岁的儿子。", "source": "甲把家业交给了十六岁的儿子。",
                   "fact_refs": [1]},
                  {"text": "如今已跻身十大豪门之列", "source": "如今已跻身十大豪门之列",
                   "fact_refs": [2]}])
    records = plan_contract_records(plan, novel_text=NOVEL, require_verification=False)
    assert [r.get("code") for r in records] == []
