# -*- coding: utf-8 -*-
"""R2-1：必保留事实来源分级合同与 legacy 视图往返（离线，自足）."""
from __future__ import annotations

from shared import fact_sources as fs
from shared import presentation_contract as pc


def _view():
    return {
        "schema": pc.PRESENTATION_SCHEMA,
        "mode": pc.MODE_LEGACY,
        "event_binding": "unknown",
        "frozen_facts": [
            {"uid": "f01", "claim": "甲进屋", "evidence": "甲进屋。"},
            {"uid": "f02", "claim": "乙留下", "evidence": "乙留下。"},
        ],
        "frozen_events": ["事件1"],
        "beats": [
            {"uid": "b01", "text": "甲推门进屋。", "event_uids": ["事件1"],
             "purpose": "", "evidence": "甲进屋。", "source": {},
             "voice_ref": {"kind": "visual", "uid": "b01"}},
            {"uid": "b02", "text": "乙留在屋里。", "event_uids": ["事件1"],
             "purpose": "", "evidence": "乙留下。", "source": {},
             "voice_ref": {"kind": "visual", "uid": "b02"}},
        ],
        "dialogue_units": [],
        "narration_units": [],
        "screen_cards": [],
        "fact_bindings": [
            {"fact_uid": "f01", "carriers": [{"kind": "visual", "uid": "b01"}]},
            {"fact_uid": "f02", "carriers": [{"kind": "visual", "uid": "b02"}]},
        ],
        "hook_bindings": {"spoken_uids": [], "carriers": []},
        "evidence_updates": [],
        "plan_basis": {"fact_uids": ["f01", "f02"], "climax": "x",
                       "causal_chain": ["起→承"]},
    }


def _plan(sources=None):
    plan = pc.plan_from_view(_view())
    if sources is not None:
        plan["fact_sources"] = sources
    return plan


def test_legacy_plan_without_sources_is_unknown_not_hard_or_advisory():
    plan = _plan()
    recs = fs.source_records(plan)
    assert [r["level"] for r in recs] == [fs.LEVEL_UNKNOWN, fs.LEVEL_UNKNOWN]
    assert [r["source"] for r in recs] == [fs.SOURCE_UNKNOWN, fs.SOURCE_UNKNOWN]
    codes = [p["code"] for p in fs.source_problems(plan)]
    assert codes.count("fact_source_unknown") == 2
    assert "fact_source_basis_missing" not in codes


def test_explicit_hard_with_basis_is_accepted_and_advisory_is_kept():
    plan = _plan([
        {"uid": "f01", "source": fs.SOURCE_USER, "level": fs.LEVEL_HARD,
         "basis": "用户明确指定"},
        {"uid": "f02", "source": fs.SOURCE_MODEL_BACKGROUND,
         "level": fs.LEVEL_ADVISORY, "basis": "模型自选背景"},
    ])
    recs = fs.source_records(plan)
    assert recs[0]["level"] == fs.LEVEL_HARD and recs[0]["basis"] == "用户明确指定"
    assert recs[1]["level"] == fs.LEVEL_ADVISORY
    assert [p["code"] for p in fs.source_problems(plan)] == []


def test_hard_without_basis_is_flagged_not_silently_hard():
    plan = _plan([{"uid": "f01", "source": fs.SOURCE_CORE,
                   "level": fs.LEVEL_HARD, "basis": ""}])
    codes = [p["code"] for p in fs.source_problems(plan)]
    assert "fact_source_basis_missing" in codes


def test_length_mismatch_is_flagged():
    plan = _plan([{"uid": "f01", "source": fs.SOURCE_USER,
                   "level": fs.LEVEL_HARD, "basis": "用户指定"}])
    codes = [p["code"] for p in fs.source_problems(plan)]
    assert "fact_sources_length_mismatch" in codes


def test_normalize_sources_returns_aligned_table():
    plan = _plan()
    out = fs.normalize_sources(plan)
    assert [r["uid"] for r in out["fact_sources"]] == ["f01", "f02"]
    assert [r["level"] for r in out["fact_sources"]] == [fs.LEVEL_UNKNOWN] * 2


def test_adjudication_marks_child_hard_and_background_advisory():
    plan = _plan()
    mapping = {
        "f01": {"source": fs.SOURCE_USER, "level": fs.LEVEL_HARD,
                "basis": "用户 2026-11-10 裁决：f01 hard",
                "adjudicated_by": fs.ADJUDICATED_BY_USER},
        "f02": {"source": fs.SOURCE_MODEL_BACKGROUND, "level": fs.LEVEL_ADVISORY,
                "basis": "模型自选背景；omittable 允许省略"},
    }
    out, probs = fs.apply_adjudication(plan, mapping)
    assert probs == []
    recs = fs.source_records(out)
    assert recs[0]["level"] == fs.LEVEL_HARD
    assert recs[1]["level"] == fs.LEVEL_ADVISORY
    assert fs.source_problems(out) == []


def test_background_cannot_be_promoted_to_hard_without_user():
    mapping = {"f02": {"source": fs.SOURCE_MODEL_BACKGROUND,
                       "level": fs.LEVEL_HARD, "basis": "模型自己觉得重要"}}
    codes = [p["code"] for p in fs.adjudication_problems(mapping)]
    assert "fact_source_level_conflict" in codes


def test_user_specified_cannot_be_downgraded_without_user():
    mapping = {"f01": {"source": fs.SOURCE_USER, "level": fs.LEVEL_ADVISORY,
                       "basis": "修订层想省内容"}}
    codes = [p["code"] for p in fs.adjudication_problems(mapping)]
    assert "fact_source_level_conflict" in codes


def test_unknown_legacy_is_not_silently_downgraded_by_adjudication():
    plan = _plan()
    out, probs = fs.apply_adjudication(plan, {})
    assert probs == []
    assert [r["level"] for r in fs.source_records(out)] == [fs.LEVEL_UNKNOWN] * 2
    assert "fact_source_unknown" in [p["code"] for p in fs.source_problems(out)]


def test_model_background_hard_is_optional_selection():
    plan = {"required_facts": ["甲进屋｜原文证据：“甲进屋。”｜承载：B1/画面"],
            "fact_uids": ["f01"]}
    mapping = {"f02": {"source": fs.SOURCE_MODEL_BACKGROUND,
                       "level": fs.LEVEL_HARD,
                       "basis": "用户显式升 hard，但只约束选入后完整",
                       "adjudicated_by": fs.ADJUDICATED_BY_USER}}
    out, probs = fs.apply_adjudication(plan, mapping)
    assert probs == []
    required = fs.adjudicated_records(mapping)
    assert fs.must_select(required[0]) is False
    assert fs.selection_problems(out, required_records=required) == []


def test_user_hard_missing_is_reported_not_silently_optional():
    plan = {"required_facts": ["甲进屋｜原文证据：“甲进屋。”｜承载：B1/画面"],
            "fact_uids": ["f01"]}
    mapping = {"f02": {"source": fs.SOURCE_USER, "level": fs.LEVEL_HARD,
                       "basis": "用户明确指定", "adjudicated_by": fs.ADJUDICATED_BY_USER}}
    out, probs = fs.apply_adjudication(plan, mapping)
    assert probs == []
    required = fs.adjudicated_records(mapping)
    assert [p["code"] for p in fs.selection_problems(
        out, required_records=required)] == ["hard_fact_not_selected"]


def test_view_roundtrip_preserves_source_metadata_by_fact_order():
    plan = _plan([
        {"uid": "f01", "source": fs.SOURCE_USER, "level": fs.LEVEL_HARD,
         "basis": "用户明确指定", "adjudicated_by": fs.ADJUDICATED_BY_USER},
        {"uid": "f02", "source": fs.SOURCE_MODEL_BACKGROUND,
         "level": fs.LEVEL_ADVISORY, "basis": "模型自选背景"},
    ])
    view, probs = pc.view_from_plan(plan)
    assert probs == []
    assert view["frozen_facts"][0]["source"]["source"] == fs.SOURCE_USER
    assert view["frozen_facts"][1]["source"]["level"] == fs.LEVEL_ADVISORY
    back = pc.plan_from_view(view)
    assert back["fact_sources"][0]["uid"] == "f01"
    assert back["fact_sources"][0]["basis"] == "用户明确指定"
    assert back["fact_sources"][0]["adjudicated_by"] == fs.ADJUDICATED_BY_USER
    assert back["fact_sources"][1]["level"] == fs.LEVEL_ADVISORY
    # `plan_basis` 不再带副本，避免拆分/重排后两份漂移。
    assert "fact_sources" not in pc.plan_basis(plan)


def test_old_plan_without_sources_does_not_gain_the_field_on_roundtrip():
    plan = _plan()
    assert "fact_sources" not in pc.plan_from_view(pc.view_from_plan(plan)[0])
