# -*- coding: utf-8 -*-
"""S03：因果链单位身份的生产校验与修订重映射（插入/删除/重编号/拆分）.

2026-11-14 复核要求的最小链路：
`计划生成 → 身份校验 → 镜头承载 → 修订重映射 → 关系检查`。
本文件覆盖后四步；生成端提示的改动见 `step01_script_gen/outline.py` 的
`causal_chain` 规则（要求每条带 `（Bk）` 身份）。
"""
from __future__ import annotations

import copy

from shared.execution_semantics import (
    CAUSAL_UNRESOLVED_MARK,
    check_script_semantics,
    codes_of,
    remap_causal_refs,
    validate_causal_identity,
)


def _plan(chain, beats, **extra):
    plan = {
        "causal_chain": list(chain),
        "shot_beats": list(beats),
        "dialogue_lines": [],
        "main_events": [],
        "fact_uids": [],
        "required_facts": [],
    }
    plan.update(extra)
    return plan


# ---------------------------------------------------------------------------
# 身份校验
# ---------------------------------------------------------------------------
def test_identity_resolved_by_explicit_ref():
    plan = _plan(["起因：甲出门（B1）", "结果：乙到达（B2）"],
                 ["节拍：甲出门", "节拍：乙到达"])
    out = validate_causal_identity(plan)
    assert out["status"] == "resolved"
    assert out["units"] == {"beats": [1, 2]}
    assert out["unresolved"] == []


def test_identity_resolved_by_exact_body():
    """旧稿可用的内容身份：条目正文与节拍正文完全相等（不做相似度）."""
    plan = _plan(["起因：甲出门", "结果：乙到达"],
                 ["节拍：甲出门", "节拍：乙到达"])
    out = validate_causal_identity(plan)
    assert out["status"] == "resolved"
    assert out["units"] == {"beats": [1, 2]}


def test_identity_unresolved_for_old_plan_without_identity():
    plan = _plan(["起因：甲动身", "结果：乙抵达"],
                 ["节拍：甲出门", "节拍：乙到达"])
    out = validate_causal_identity(plan)
    assert out["status"] == "unresolved"
    assert out["unresolved"] == ["甲动身", "乙抵达"]
    assert out["problems"] == []


def test_identity_out_of_range_is_invalid():
    plan = _plan(["起因：甲出门（B1）", "结果：乙到达（B9）"],
                 ["节拍：甲出门", "节拍：乙到达"])
    out = validate_causal_identity(plan)
    assert out["status"] == "invalid"
    assert out["problems"][0]["code"] == "causal_ref_out_of_range"
    assert out["problems"][0]["path"].endswith("causal_chain[1]")


def test_identity_conflict_between_body_and_ref_is_invalid():
    """正文身份指向 B2、编号却写 B1 ⇒ 身份互相矛盾（invalid）."""
    plan = _plan(["结果：乙到达（B1）"],
                 ["节拍：甲出门", "节拍：乙到达"])
    out = validate_causal_identity(plan)
    assert out["status"] == "invalid"
    assert out["conflicts"] and out["conflicts"][0]["text"] == "乙到达（B1）"
    assert out["unresolved"] == ["乙到达（B1）"]


def test_identity_ambiguous_body_match_is_unresolved():
    """正文同时匹配两条节拍 ⇒ 歧义，不得把两个候选都纳入身份集合."""
    plan = _plan(["结果：乙到达"],
                 ["节拍：乙到达", "节拍：乙到达"])
    out = validate_causal_identity(plan)
    assert out["status"] == "unresolved"
    assert out["units"] == {}
    assert out["ambiguous"] and out["ambiguous"][0]["candidates"] == {
        "beats": [1, 2]}


def test_explicit_unresolved_marker_wins_over_body_match():
    """已写「未落节拍」的条目不得在下一阶段按正文重新解析成 resolved."""
    plan = _plan(["结果：乙到达（%s）" % CAUSAL_UNRESOLVED_MARK],
                 ["节拍：乙到达", "节拍：乙到达"])
    out = validate_causal_identity(plan)
    assert out["status"] == "unresolved"
    assert out["units"] == {}
    assert out["explicit_unresolved"] == [
        "乙到达（%s）" % CAUSAL_UNRESOLVED_MARK]


def _apply_remap(plan: dict, old: list, new: list) -> list[str]:
    """按真实调用顺序：先落新节拍表，再重映射因果身份."""
    plan["shot_beats"] = list(new)
    return remap_causal_refs(plan, old, new)


# ---------------------------------------------------------------------------
# 修订重映射：插入 / 删除 / 重排 / 拆分
# ---------------------------------------------------------------------------
def test_remap_insert_shifts_refs():
    plan = _plan(["结果：乙到达（B2）"],
                 ["节拍：甲出门", "节拍：乙到达"])
    old = ["节拍：甲出门", "节拍：乙到达"]
    new = ["节拍：鸟飞过", "节拍：甲出门", "节拍：乙到达"]
    notes = _apply_remap(plan, old, new)
    assert plan["causal_chain"] == ["结果：乙到达（B3）"]
    assert notes == []
    assert validate_causal_identity(plan)["units"] == {"beats": [3]}


def test_remap_delete_marks_unresolved():
    plan = _plan(["起因：甲出门（B1）", "结果：乙到达（B2）"],
                 ["节拍：甲出门", "节拍：乙到达"])
    notes = _apply_remap(plan, ["节拍：甲出门", "节拍：乙到达"],
                         ["节拍：甲出门"])
    assert plan["causal_chain"] == [
        "起因：甲出门（B1）",
        "结果：乙到达（%s）" % CAUSAL_UNRESOLVED_MARK,
    ]
    assert notes and "B2" in notes[0]
    out = validate_causal_identity(plan)
    # 被删除的结果节拍没有身份 ⇒ 该条目明确「未完成语义判定」。
    assert out["status"] == "unresolved"
    assert out["unresolved"] == ["乙到达（%s）" % CAUSAL_UNRESOLVED_MARK]


def test_remap_reorder_maps_by_body_not_position():
    plan = _plan(["起因：甲出门（B1）", "结果：乙到达（B2）"],
                 ["节拍：甲出门", "节拍：乙到达"])
    old = ["节拍：甲出门", "节拍：乙到达"]
    new = ["节拍：乙到达", "节拍：甲出门"]
    notes = _apply_remap(plan, old, new)
    assert plan["causal_chain"] == ["起因：甲出门（B2）", "结果：乙到达（B1）"]
    assert notes == []
    assert validate_causal_identity(plan)["units"] == {"beats": [1, 2]}


def test_remap_split_marks_unresolved():
    plan = _plan(["结果：乙到达（B2）"],
                 ["节拍：甲出门", "节拍：乙到达"])
    notes = _apply_remap(
        plan, ["节拍：甲出门", "节拍：乙到达"],
        ["节拍：甲出门", "节拍：乙到达·前段", "节拍：乙到达·后段"])
    assert CAUSAL_UNRESOLVED_MARK in plan["causal_chain"][0]
    assert notes and "B2" in notes[0]


def test_remap_duplicate_bodies_marks_unresolved():
    """正文重复时无法唯一映射 ⇒ 未落节拍（不猜第一条）."""
    plan = _plan(["结果：乙到达（B1）"], ["节拍：乙到达"])
    notes = _apply_remap(plan, ["节拍：乙到达"],
                         ["节拍：乙到达", "节拍：乙到达"])
    assert CAUSAL_UNRESOLVED_MARK in plan["causal_chain"][0]
    assert notes
    # 重映射的失效标记必须**跨阶段保持**：下一阶段不得按正文重新解析成 resolved。
    out = validate_causal_identity(plan)
    assert out["status"] == "unresolved"
    assert out["units"] == {}
    assert out["explicit_unresolved"] and \
        CAUSAL_UNRESOLVED_MARK in out["explicit_unresolved"][0]


def test_remap_duplicate_bodies_stays_unresolved_through_relation_check():
    """串联 重映射 → 身份校验 → 关系检查：歧义不得在关系检查里被"解决"."""
    plan = _plan(["起因：甲出门（B1）", "结果：乙到达（B2）"],
                 ["节拍：甲出门", "节拍：乙到达"])
    _apply_remap(plan, ["节拍：甲出门", "节拍：乙到达"],
                 ["节拍：乙到达", "节拍：乙到达"])
    assert validate_causal_identity(plan)["status"] == "unresolved"
    script = _script(plan, [1, 2])
    findings = check_script_semantics(script)
    assert codes_of(findings) == ["causal_gap"]
    gap = findings[0]
    assert gap["relation_status"] == "unresolved"
    assert gap["identity_status"] == "unresolved"
    assert gap["needs_semantic_review"] is True
    assert gap["relation_note"].startswith("该项未完成语义判定")


def test_remap_keeps_entries_without_refs_untouched():
    plan = _plan(["起因：甲动身", "结果：乙到达（B2）"],
                 ["节拍：甲出门", "节拍：乙到达"])
    _apply_remap(plan, ["节拍：甲出门", "节拍：乙到达"],
                 ["节拍：鸟飞过", "节拍：甲出门", "节拍：乙到达"])
    assert plan["causal_chain"][0] == "起因：甲动身"
    assert plan["causal_chain"][1] == "结果：乙到达（B3）"


# ---------------------------------------------------------------------------
# 修订入口：apply_replan_ops 插入节拍后，因果身份随新节拍表重映射
# ---------------------------------------------------------------------------
def test_revision_entry_insert_remaps_causal_refs():
    from step01_script_gen.outline import apply_replan_ops

    plan = {
        "causal_chain": ["起因：甲出门（B1）", "结果：乙到达（B2）"],
        "shot_beats": [
            "节拍：甲出门｜原文依据：甲出门｜载体：画面",
            "节拍：乙到达｜原文依据：乙到达｜载体：画面",
        ],
        "dialogue_lines": [], "narration_lines": [], "required_facts": [],
        "main_events": ["甲出门", "乙到达"], "climax": "乙到达", "fact_uids": [],
    }
    ops = {
        "beat_ops": [{
            "op": "insert_after", "index": 1,
            "new_text": "鸟从城头飞过｜原文依据：鸟从城头飞过",
            "new_carrier": "画面",
        }],
        "fact_carriers": [],
    }
    new, why = apply_replan_ops(plan, ops, "甲出门。鸟从城头飞过。乙到达。")
    assert why == ""
    assert new["shot_beats"][1].startswith("节拍：鸟从城头飞过")
    assert new["causal_chain"] == ["起因：甲出门（B1）", "结果：乙到达（B3）"]
    assert validate_causal_identity(new)["units"]["beats"] == [1, 3]


# ---------------------------------------------------------------------------
# 关系检查：重映射后仍绑定到正确的承载镜头
# ---------------------------------------------------------------------------
def _script(plan, shot_refs):
    return {
        "chapter_outline": plan,
        "shots": [{
            "index": 1, "duration_sec": 5.0,
            "ff_desc": "城门前，守卫横矛封锁", "lf_desc": "旅人已经进入城门",
            "motion_desc": "守卫横矛", "prompt": "城门",
            "narration_segment": "", "context_bridge": "",
            "plan_refs": {"beats": list(shot_refs), "lines": []},
        }],
        "total_duration": 5.0,
    }


def test_relation_binds_to_remapped_beats_after_insert():
    """插入一条真实节拍后，关系身份重映射；鸟节拍不进入承载范围."""
    plan = _plan(["起因：守卫封锁城门（B1）", "结果：旅人已经进入城门（B2）"],
                 ["节拍：守卫封锁城门", "节拍：旅人已经进入城门"])
    old = ["节拍：守卫封锁城门", "节拍：旅人已经进入城门"]
    new = ["节拍：鸟飞过", "节拍：守卫封锁城门", "节拍：旅人已经进入城门"]
    _apply_remap(plan, old, new)
    assert plan["causal_chain"] == [
        "起因：守卫封锁城门（B2）", "结果：旅人已经进入城门（B3）"]
    script = _script(plan, [2, 3])
    findings = check_script_semantics(script)
    assert codes_of(findings) == ["causal_gap", "time_ellipsis_lost"]
    gap = findings[0]
    assert gap["relation_status"] == "bound"
    assert gap["relation_units"] == {"beats": [2, 3]}
    assert gap["carrier_evidence"] == []


def test_relation_after_result_beat_deleted_is_evidence_insufficient():
    """结果节拍被删除 ⇒ 身份标未落节拍，关系未定位，不按全章拒收."""
    plan = _plan(["起因：守卫封锁城门（B1）", "结果：旅人进入城门（B2）"],
                 ["节拍：守卫封锁城门", "节拍：旅人进入城门"])
    _apply_remap(
        plan, ["节拍：守卫封锁城门", "节拍：旅人进入城门"],
        ["节拍：守卫封锁城门"])
    script = _script(plan, [1])
    findings = check_script_semantics(script)
    assert codes_of(findings) == ["causal_gap"]
    gap = findings[0]
    assert gap["relation_status"] == "unresolved"
    assert gap["needs_semantic_review"] is True
    assert gap["relation_note"].startswith("该项未完成语义判定")


def test_old_plan_unresolved_reports_semantic_pending():
    """旧稿没有身份：只出 advisory，并明确标注「未完成语义判定」."""
    plan = _plan(["起因：守卫封锁了城门", "结果：旅人进了城门"],
                 ["节拍：守卫封锁城门", "节拍：旅人进入城门"])
    script = _script(plan, [1, 2])
    findings = check_script_semantics(script)
    assert codes_of(findings) == ["causal_gap"]
    gap = findings[0]
    assert gap["relation_status"] == "unresolved"
    assert gap["relation_note"].startswith("该项未完成语义判定")
