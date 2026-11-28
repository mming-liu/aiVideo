"""Planning repairs must receive the same shape, identity and limits we enforce."""
import copy
import json
import pytest

from _text_gen import agent_plan_provider as ap
from _text_gen.models import TextGenerationRequest
from shared.presentation_contract import view_problems
from step01_script_gen import outline as o

pytestmark = pytest.mark.v2_request


def test_opencode_does_not_depend_on_unstable_remote_schema_support():
    service = ap.AgentPlanTextService.__new__(ap.AgentPlanTextService)
    service._base_url = "https://opencode.ai/zen/go/v1"
    service._model = "deepseek-v4.1-flash"
    schema = copy.deepcopy(o.REPLAN_DELTA_SCHEMA)
    request = TextGenerationRequest(system_prompt="s", user_prompt="JSON", json_schema=schema)
    payload = service._build_payload(request)
    assert payload["response_format"] == {"type": "json_object"}
    assert request.json_schema == schema
    assert ap.sent_structured_output(payload) == "json_object"


def test_unsupported_endpoint_keeps_its_existing_format():
    service = ap.AgentPlanTextService.__new__(ap.AgentPlanTextService)
    service._base_url = "https://ark.cn-beijing.volces.com/api/v3"
    service._model = "doubao"
    request = TextGenerationRequest(system_prompt="s", user_prompt="JSON", json_schema=o.REPLAN_DELTA_SCHEMA)
    assert service._build_payload(request)["response_format"] == {"type": "json_object"}


def test_feedback_uses_producer_identity_without_display_position():
    view = {"frozen_facts": [{"uid": "f09"}, {"uid": "f02"}]}
    record = {"code": "view_binding_dangling", "kind": "fact", "index": None,
              "uid": "f02", "reason": "broken carrier", "path": "fact_bindings"}
    assert o._view_identity(record, view) == "f02"
    assert "实体 uid=f02" in o._view_feedback_block([record], view)


def test_event_reversal_feedback_identifies_both_neighbors():
    view = {"event_binding": "declared", "frozen_events": ["earlier", "later"],
            "beats": [{"uid": "b10", "event_uids": ["later"]},
                      {"uid": "b07", "event_uids": ["earlier"]}],
            "frozen_facts": [], "fact_bindings": []}
    problem = next(p for p in view_problems(view) if p["code"] == "view_event_order_reversed")
    assert "b10" in problem["reason"] and "b07" in problem["reason"]
    assert "order.beats" in problem["fix"]
    legal = copy.deepcopy(view)
    legal["beats"].reverse()
    assert not any(p["code"] == "view_event_order_reversed" for p in view_problems(legal))


def test_orphan_feedback_allows_selection_instead_of_only_growing_beats():
    from step01_script_gen.plan_feasibility import carrier_relation_records
    problems = carrier_relation_records(["节拍：走路｜载体：画面"], 1, 0)
    problem = next(p for p in problems if p["code"] == "orphan_uncarried")
    assert "删" in problem["reason"] and "撤销" in problem["reason"]
    assert "承载" in problem["reason"]


def test_relation_problem_preserves_fact_identity_through_plan_acceptance():
    from pathlib import Path
    plan = json.loads((Path(__file__).parent / "fixtures" / "plan_revision_ch1_20261007_plan.json").read_text(encoding="utf-8"))
    problems = o.plan_acceptance_problems(plan, "")
    broken = next(p for p in problems if p["code"] == "view_binding_dangling")
    assert broken["uid"] == "f05" and broken["kind"] == "fact"
    from shared.presentation_contract import view_from_plan
    view, read_problems = view_from_plan(plan)
    assert not read_problems
    assert "实体 uid=f05" in o._view_feedback_block([broken], view)
    repaired = copy.deepcopy(plan)
    binding = next(b for b in repaired["presentation"]["fact_bindings"] if b["fact_uid"] == "f05")
    binding["carriers"] = [{"kind": "screen_card", "card_id": "c05"}]
    assert not any(p["code"] == "view_binding_dangling" for p in o.plan_acceptance_problems(repaired, ""))


def test_real_revision_request_transmits_its_limits_and_fix(monkeypatch):
    from pathlib import Path
    from _text_gen.models import TextGenerationResult
    from step01_script_gen.plan_revision import RevisionBudget
    plan = json.loads((Path(__file__).parent / "fixtures" / "plan_revision_ch1_20261007_plan.json").read_text(encoding="utf-8"))
    captured = []
    class Service:
        def generate(self, request):
            captured.append(request)
            return TextGenerationResult(success=False, error="capture only")
    monkeypatch.setattr(o, "create_service", lambda: Service())
    budget = RevisionBudget(max_calls=3)
    issue = {"code": "budget_infeasible", "kind": "plan", "reason": "too long",
             "fix": "reselect_dialogue", "limits": {"max_seconds": 90}}
    o._replan_by_view(plan, [issue], "", budget, protocol=o.ROUTE_VIEW_DELTA)
    assert len(captured) == 1
    prompt = captured[0].user_prompt
    assert '"budget":' in prompt and '"limits":' in prompt
    assert '"calls_left": 3' in prompt
    assert "修正出口：reselect_dialogue" in prompt
    assert "请现在实际修复" in prompt
    assert '"response_contract":' in prompt
    assert '"resources":' in prompt
    assert '"card_costs":' in prompt
    assert '"voice_costs":' in prompt
    assert '"pattern": "^new:.+$"' in prompt
    assert '"type": "object"' in prompt
    assert '"required": [' in prompt
    uid_schema = captured[0].json_schema["schema"]["properties"]["upsert"]["properties"]["beats"]["items"]["properties"]["uid"]
    assert uid_schema["anyOf"][0]["enum"] == ["b01", "b02", "b03", "b04", "b05", "b06"]
    assert uid_schema["anyOf"][1]["pattern"] == "^new:.+$"
    assert captured[0].json_schema["schema"]["properties"]["rule_version"]["const"] == o.PLAN_DELTA_RULE_VERSION


def test_request_schema_uses_real_card_identity_and_freezes_hashes():
    from pathlib import Path
    from shared.presentation_contract import view_from_plan, delta_request
    plan = json.loads((Path(__file__).parent / "fixtures" / "plan_revision_ch1_20261007_plan.json").read_text(encoding="utf-8"))
    view, problems = view_from_plan(plan)
    assert not problems
    request = delta_request(view)
    schema = o.replan_delta_schema_for_request(request)["schema"]
    props = schema["properties"]
    assert props["base_plan_hash"]["const"] == request["base_plan_hash"]
    card_key = props["upsert"]["properties"]["screen_cards"]["items"]["properties"]["card_id"]
    assert card_key["anyOf"][0]["enum"] == ["c01", "c02", "c03", "c04", "c05", "c06"]
    assert props["delete"]["properties"]["narration_units"]["maxItems"] == 0
    assert props["fact_bindings"]["items"]["properties"]["fact_uid"]["enum"] == ["f01", "f02", "f03", "f04", "f05", "f06"]


def test_real_verification_feedback_keeps_the_fact_and_voice_identity():
    from shared import content_verification as cv
    from tests.shared.test_content_verification import _record, _view
    view = _view()
    record = _record(view, verdict=cv.VERDICT_PARTIAL)
    record["facts"][0]["missing"] = "账目接管关系"
    record["voice_units"][0]["echo_text"] = "错误正文"
    problems = cv.verification_problems(record, view, plan=cv.view_candidate(view, view))
    fact = next(p for p in problems if p["code"] == "verify_fact_unsupported")
    voice = next(p for p in problems if p["code"] == "verify_voice_text_mismatch")
    assert fact["uid"] == "f01"
    assert voice["uid"] == "n01"
    prompt = o._view_feedback_block([fact, voice], view)
    assert "实体 uid=f01" in prompt and "实体 uid=n01" in prompt
    assert view["narration_units"][0]["text"] in voice["reason"]


def test_real_card_and_beat_failures_return_stable_identity():
    from pathlib import Path
    from step01_script_gen.plan_feasibility import presentation_records
    plan = json.loads((Path(__file__).parent / "fixtures" / "plan_revision_ch1_20261007_plan.json").read_text(encoding="utf-8"))
    plan["presentation"]["fact_bindings"][4]["carriers"] = [{"kind": "screen_card", "card_id": "c05"}]
    issues = presentation_records(plan, novel_text="这是另一篇小说的原文。")
    card = next(p for p in issues if p["code"] == "presentation_card_not_in_novel")
    beat = next(p for p in issues if p["code"] == "presentation_beat_evidence_not_in_novel")
    assert card["uid"] == "c01"
    assert beat["uid"] == "b01"


def test_resource_context_reports_the_same_profile_and_estimator_as_acceptance():
    from pathlib import Path
    from dataclasses import replace
    from shared.screen_cards import profile
    from shared.presentation_contract import view_from_plan
    from step01_script_gen import plan_feasibility as pf
    plan = json.loads((Path(__file__).parent / "fixtures" / "plan_revision_ch1_20261007_plan.json").read_text(encoding="utf-8"))
    view, problems = view_from_plan(plan)
    assert not problems
    resources = pf.planning_resource_context(view)
    assert resources["card_reading"]["budget_sec"] == profile.active_profile().reading_budget_sec
    assert resources["card_costs"][0]["seconds"] == pf.card_reading_seconds(view["screen_cards"][0]["text"])
    assert resources["voice_costs"][0]["seconds"] == pf.dialogue_seconds([view["dialogue_units"][0]["text"]])
    assert resources["duration_band_sec"]["max"] == pf.budget_band()[1]


def test_first_version_and_revision_share_reading_rules(monkeypatch):
    from dataclasses import replace
    from shared.screen_cards import profile
    changed = replace(profile.DisplayProfile(), reading_budget_sec=22.0, reading_chars_per_sec=5.5, settle_sec=0.9)
    monkeypatch.setattr(profile, "active_profile", lambda: changed)
    args = {"novel_text": "正文", "beat_edit_rule": "同一镜承载"}
    legacy = o.outline_prompt_for_mode("legacy_audio", **args)
    hybrid = o.outline_prompt_for_mode("screen_hybrid", **args)
    assert legacy == o.OUTLINE_PROMPT.format(**args)
    assert '"budget_sec": 22.0' in hybrid
    assert '"reading_chars_per_sec": 5.5' in hybrid
    assert "分页不会降低总阅读时间" in hybrid
    assert '"items": {"type": "array"' in hybrid
    assert "须拆成多张卡" not in hybrid


def test_verifier_distinguishes_semantic_visual_support_from_literal_evidence():
    from step01_script_gen.content_verify import build_verification_prompt
    from tests.shared.test_content_verification import _view
    prompt = build_verification_prompt(_view())
    assert "画面节拍按演出的动作、状态和先后关系判断" in prompt
    assert "引用记录的逐字要求，不是要求画面逐字复述主张" in prompt
    assert "missing 只能指出冻结主张本身未被表达的部分" in prompt
    assert "不能要求文字卡再次复述已经由画面表达的部分" in prompt
