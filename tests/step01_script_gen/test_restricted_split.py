# -*- coding: utf-8 -*-
"""R2-2：受限事实拆分合同（匿名自足样本，无模型调用）."""
from __future__ import annotations

import copy

from step01_script_gen import restricted_split as rs

NOVEL = "甲把家业交给了十六岁的儿子。后来生意兴隆，如今已跻身十大豪门。乙留下。"

PARENT = ("甲十六岁接手家业，数年间跻身十大豪门｜"
          "原文证据：“甲把家业交给了十六岁的儿子。……如今已跻身十大豪门。”"
          "｜承载：B1/旁白1")
F01 = "乙留下｜原文证据：“乙留下。”｜承载：B3/画面"
CHILD1 = "甲十六岁接手家业｜原文证据：“甲把家业交给了十六岁的儿子。”｜承载：B1/旁白1"
CHILD2 = "数年间跻身十大豪门｜原文证据：“如今已跻身十大豪门。”｜承载：B2/旁白2"


def _base():
    return {
        "required_facts": [F01, PARENT],
        "fact_uids": ["f01", "f02"],
        "shot_beats": [
            "节拍：甲接手家业｜本镜信息目的：交代继业｜原文依据：甲把家业交给儿子｜载体：旁白1",
            "节拍：生意兴隆｜本镜信息目的：交代结果｜原文依据：如今已跻身十大豪门｜载体：画面",
            "节拍：乙留下｜本镜信息目的：收尾｜原文依据：乙留下｜载体：画面",
        ],
        "dialogue_lines": [],
        "narration_lines": [
            {"text": "甲把家业交给了十六岁的儿子。", "source": "甲把家业交给了十六岁的儿子。",
             "fact_refs": [2]},
        ],
    }


def _candidate(children=None, *, f01=None, lineage=True, parent_present=False,
               credentials=None):
    kids = copy.deepcopy(children if children is not None else [CHILD1, CHILD2])
    facts = [f01 or F01] + kids + ([PARENT] if parent_present else [])
    uids = ["f01"] + [("f02a" if i == 0 else "f02b") for i in range(len(kids))]
    if parent_present:
        uids.append("f02")
    plan = _base()
    plan["required_facts"] = facts
    plan["fact_uids"] = uids
    plan["shot_beats"] = [
        "节拍：甲接手家业｜本镜信息目的：交代继业｜原文依据：甲把家业交给儿子｜载体：旁白1",
        "节拍：跻身十大豪门｜本镜信息目的：交代结果｜原文依据：如今已跻身十大豪门｜载体：旁白2",
        "节拍：乙留下｜本镜信息目的：收尾｜原文依据：乙留下｜载体：画面",
    ]
    plan["narration_lines"] = [
        {"text": "甲把家业交给了十六岁的儿子。", "source": "甲把家业交给了十六岁的儿子。",
         "fact_refs": [2]},
        {"text": "如今已跻身十大豪门。", "source": "如今已跻身十大豪门。",
         "fact_refs": [3]},
    ]
    if lineage:
        plan[rs.LINEAGE_FIELD] = [
            rs.lineage_record("f02", ["f02a", "f02b"], PARENT)]
    if credentials:
        plan.update(credentials)
    return plan


def _codes(probs):
    return [p["code"] for p in probs]


def test_legal_split_covers_parent_and_keeps_non_target():
    probs = rs.plan_split_problems(_base(), _candidate(), "f02",
                                   ["f02a", "f02b"], novel_text=NOVEL)
    assert probs == [], _codes(probs)


def test_missing_result_is_rejected():
    probs = rs.split_contract_problems(PARENT, [CHILD1], novel_text=NOVEL,
                                       parent_uid="f02")
    assert "split_child_count" in _codes(probs)


def test_only_result_without_handover_is_rejected():
    probs = rs.split_contract_problems(
        PARENT, [CHILD2], novel_text=NOVEL, parent_uid="f02",
        child_uids=["f02b"])
    assert "split_child_count" in _codes(probs)


def test_wrong_subject_is_rejected():
    bad = "乙数年间跻身十大豪门｜原文证据：“如今已跻身十大豪门。”｜承载：B2/旁白2"
    probs = rs.split_contract_problems(PARENT, [CHILD1, bad], novel_text=NOVEL,
                                       parent_uid="f02")
    assert "split_child_claim_not_covered" in _codes(probs)


def test_time_or_negation_change_is_rejected():
    bad = "甲二十岁接手家业｜原文证据：“甲把家业交给了十六岁的儿子。”｜承载：B1/旁白1"
    probs = rs.split_contract_problems(PARENT, [bad, CHILD2], novel_text=NOVEL,
                                       parent_uid="f02")
    assert "split_child_claim_not_covered" in _codes(probs)


def test_over_split_and_duplicate_are_rejected():
    kids = [CHILD1, CHILD2, CHILD2, CHILD1, CHILD2]
    probs = rs.split_contract_problems(PARENT, kids, novel_text=NOVEL,
                                       parent_uid="f02")
    assert "split_child_count" in _codes(probs)
    probs2 = rs.split_contract_problems(PARENT, [CHILD1, CHILD1], novel_text=NOVEL,
                                        parent_uid="f02")
    assert "split_child_claim_duplicate" in _codes(probs2)


def test_no_evidence_and_cross_segment_evidence_are_rejected():
    no_ev = "数年间跻身十大豪门｜原文证据：“”｜承载：B2/旁白2"
    probs = rs.split_contract_problems(PARENT, [CHILD1, no_ev], novel_text=NOVEL,
                                       parent_uid="f02")
    assert "split_child_evidence_empty" in _codes(probs)
    cross = ("数年间跻身十大豪门｜原文证据：“甲把家业交给了十六岁的儿子。……"
             "如今已跻身十大豪门。”｜承载：B2/旁白2")
    probs2 = rs.split_contract_problems(PARENT, [CHILD1, cross], novel_text=NOVEL,
                                        parent_uid="f02")
    assert "split_child_evidence_noncontiguous" in _codes(probs2)


def test_non_target_fact_and_beat_change_are_rejected():
    cand = _candidate(f01="乙离开了｜原文证据：“乙留下。”｜承载：B3/画面")
    probs = rs.plan_split_problems(_base(), cand, "f02", ["f02a", "f02b"],
                                   novel_text=NOVEL)
    assert "split_non_target_fact_changed" in _codes(probs)
    cand2 = _candidate()
    cand2["shot_beats"][2] = "节拍：乙走开｜本镜信息目的：收尾｜原文依据：乙留下｜载体：画面"
    probs2 = rs.plan_split_problems(_base(), cand2, "f02", ["f02a", "f02b"],
                                    novel_text=NOVEL)
    assert "split_non_target_beat_changed" in _codes(probs2)


def test_parent_must_be_replaced_and_lineage_recorded():
    probs = rs.plan_split_problems(_base(), _candidate(parent_present=True),
                                   "f02", ["f02a", "f02b"], novel_text=NOVEL)
    assert "split_parent_not_removed" in _codes(probs)
    probs2 = rs.plan_split_problems(_base(), _candidate(lineage=False),
                                    "f02", ["f02a", "f02b"], novel_text=NOVEL)
    assert "split_lineage_missing" in _codes(probs2)


def test_child_uid_must_keep_parent_prefix():
    kids = [CHILD1.replace("旁白1", "旁白1"), CHILD2]
    probs = rs.split_contract_problems(PARENT, [
        {"uid": "x1", "claim": "甲十六岁接手家业",
         "evidence": "甲把家业交给了十六岁的儿子。", "carrier": "旁白1"},
        {"uid": "x2", "claim": "数年间跻身十大豪门",
         "evidence": "如今已跻身十大豪门。", "carrier": "旁白2"},
    ], novel_text=NOVEL, parent_uid="f02")
    assert "split_child_uid_prefix" in _codes(probs)
