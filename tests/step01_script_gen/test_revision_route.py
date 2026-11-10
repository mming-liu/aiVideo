# -*- coding: utf-8 -*-
"""R3-1/R3-4：修订路由、字段白名单、core 冻结（匿名样本）."""
from step01_script_gen import revision_route as rr


def _p(code, **kw):
    d = {"code": code}
    d.update(kw)
    return d


R26 = [
    _p("carrier_fact_narration_mismatch"),
    _p("orphan_uncarried"),
    _p("fact_basis_invalid"),
    _p("verify_fact_unsupported"),
]


def test_carrier_problems_win_over_source_problems():
    d = rr.route_decision(R26)
    assert d["route"] == rr.ROUTE_CARRIER
    assert "carrier_fact_narration_mismatch" in d["carrier_codes"]
    assert "fact_basis_invalid" in d["deferred_source_codes"]
    assert "fact_bindings" in d["allowed_fields"]
    assert "required_facts" not in d["allowed_fields"]


def test_source_only_problems_stay_source():
    d = rr.route_decision([_p("fact_basis_invalid")])
    assert d["route"] == rr.ROUTE_SOURCE
    assert d["allowed_fields"] == ["required_facts"]
    assert "fact_bindings" not in d["allowed_fields"]


def test_no_special_problem_falls_back():
    assert rr.route_decision([_p("time_budget")])["route"] == rr.ROUTE_DEFAULT


def test_delta_fields_are_extracted_from_nested_blocks():
    delta = {"upsert": {"beats": [], "narration_units": []},
             "fact_bindings": [], "required_facts": []}
    assert rr.delta_touched_fields(delta) == [
        "beats", "fact_bindings", "narration_units", "required_facts"]


def test_source_route_rejects_carrier_rebind():
    delta = {"fact_bindings": [{"fact_uid": "f01", "carriers": []}]}
    bad = rr.delta_field_violations(delta, rr.SOURCE_FIELDS)
    assert [x["code"] for x in bad] == ["route_field_violation"]
    assert bad[0]["path"] == "fact_bindings"


def test_carrier_route_rejects_claim_rewrite():
    delta = {"upsert": {"required_facts": ["改写主张"]}}
    bad = rr.delta_field_violations(delta, rr.CARRIER_FIELDS)
    assert [x["code"] for x in bad] == ["route_field_violation"]


def test_carrier_route_allows_binding_and_beat_changes():
    delta = {"upsert": {"beats": [], "narration_units": []},
             "fact_bindings": []}
    assert rr.delta_field_violations(delta, rr.CARRIER_FIELDS) == []


def test_r26_core_updates_are_frozen_by_default():
    core = {"main_events": ["改写后的主线"], "end_hook": "改写后的钩子"}
    bad = rr.core_update_problems(core)
    assert {x["code"] for x in bad} == {"core_field_frozen"}
    assert {x["path"] for x in bad} == {
        "core_updates.main_events", "core_updates.end_hook"}


def test_core_update_allowed_with_dependency_reason_and_traced():
    core = {"end_hook": "新的钩子"}
    allowed = {"end_hook": "f02 的接手事实必须与结尾钩子同步"}
    assert rr.core_update_problems(core, allowed) == []
    assert rr.core_update_trace(core, allowed) == {
        "end_hook": "f02 的接手事实必须与结尾钩子同步"}


def test_unknown_core_field_is_rejected():
    bad = rr.core_update_problems({"mystery": "x"})
    assert [x["code"] for x in bad] == ["core_field_unknown"]


def test_revise_plan_once_routes_r26_to_carrier(monkeypatch):
    from step01_script_gen import outline as O
    from step01_script_gen.plan_revision import RevisionBudget
    captured = {}
    monkeypatch.setattr(O, "_content_slots_applicable",
                        lambda *a, **k: (False, "test", {}))
    monkeypatch.setattr(O, "_replan_route",
                        lambda *a, **k: O.ROUTE_VIEW_DELTA)
    monkeypatch.setattr(
        O, "_replan_by_view",
        lambda plan, problems, novel_text, budget, rejections=None, **kw:
        (captured.update(kw) or (plan, "", "", kw.get("evidence") or {})))
    plan = {
        "required_facts": [
            "甲进屋｜原文证据：“甲进屋。”｜承载：B1/画面"],
        "fact_uids": ["f01"],
        "shot_beats": [
            "节拍：甲推门进屋｜本镜信息目的：建立人物｜原文依据：“甲进屋。”｜载体：画面"],
        "dialogue_lines": [],
        "narration_lines": [],
        "plan_verification": {"status": "ok"},
    }
    out = O.revise_plan_once(plan, R26, RevisionBudget(max_calls=1),
                             novel_text="甲进屋。")
    assert out["kind"] == "applied", out
    assert captured["protocol"] == O.ROUTE_VIEW_DELTA
    assert captured["allowed_fields"] == list(rr.CARRIER_FIELDS)
