# -*- coding: utf-8 -*-
"""S03：画面执行计划构建、身份重验、自动反例与失败隔离（全离线）."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from shared.execution_contract import script_digest, validate_execution_binding
from shared.execution_semantics import codes_of
from step02a_frame_gen.execution_plan import (
    build_visual_plan,
    validate_plan_identity,
    verify_frame_observations,
    write_visual_plan,
)

FIXTURES = Path(__file__).resolve().parents[1] / "shared" / "fixtures" / "execution_contract"


def _family(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def test_real_entry_builds_and_revalidates_execution_binding():
    family = _family("offscreen_voice")
    script = family["baseline"]["script"]
    plan = build_visual_plan(script, scope=family["scope"], only_indices={2, 5})
    binding = plan["execution_binding"]
    assert binding["schema"] == "execution_binding.v1"
    assert binding["source"]["source_digest"] == script_digest(script)
    assert validate_execution_binding(
        binding, script, expected_scope=family["scope"],
        expected_stage="step02a.visual_plan") == []
    assert plan["status"] == "planned"
    assert codes_of(plan["checks"]["semantic"]) == []
    assert plan["checks"]["binding"] == []
    assert validate_plan_identity(plan, script, scope=family["scope"]) == []
    assert {f["frame_id"] for f in plan["frames"]} == {
        "shot-2:ff", "shot-2:lf", "shot-5:ff", "shot-5:lf"}
    for frame in plan["frames"]:
        assert frame["content_hash"]
        assert frame["model"]
        assert frame["moment"] in ("start", "end")
        assert frame["responsibility"]
        assert frame["visible_characters"]


def test_visual_plan_marks_offscreen_relation_and_unverified_pixels():
    family = _family("offscreen_voice")
    plan = build_visual_plan(family["baseline"]["script"], scope=family["scope"],
                             only_indices={2})
    shot = plan["shots"][0]
    assert shot["offscreen_speakers"] == ["门外来客"]
    assert "门外来客" not in shot["visible_characters"]
    assert any(item["code"] == "actual_pixels_unverified"
               for item in plan["unverified"])


def test_mutation_offscreen_visible_is_blocked_by_real_entry():
    family = _family("offscreen_voice")
    plan = build_visual_plan(
        family["mutation"]["script"], scope=family["scope"], only_indices={2, 5},
        candidate=family["mutation"]["execution"]["candidate"])
    assert plan["status"] == "blocked"
    codes = codes_of(plan["checks"]["semantic"])
    assert codes == ["listener_reaction_missing", "offscreen_speaker_visible"]
    assert any(item["route"] == "step01" for item in plan["feedback"])
    assert any(item["code"] == "offscreen_speaker_visible"
               for item in plan["execution_binding"]["unresolved"])
    issues = validate_plan_identity(plan, family["mutation"]["script"], scope=family["scope"])
    assert "semantic_change_unverified" in {i["code"] for i in issues}
    assert "visual_plan_blocked" in {i["code"] for i in issues}


def test_legal_ellipsis_is_not_blocked_by_the_frame_plan_entry():
    """复核 P4：计划只缺「行动」标签、镜头过程完整 → 计划仍须放行.

    Step01 已判为合法省略的样本，不能在 Step02a 被同一条结构标签再次拦下——
    否则返工只是换了阶段。这里断言：计划 `planned`、无阻塞级语义 finding、
    `causal_gap` 作为 advisory 被记录。
    """
    family = _family("temporal_ellipsis")
    script = copy.deepcopy(family["baseline"]["script"])
    outline = script["chapter_outline"]
    outline["causal_chain"] = [entry for entry in outline["causal_chain"]
                               if not entry.startswith("行动：")]

    plan = build_visual_plan(script, scope=family["scope"], only_indices={1, 2})
    assert plan["status"] == "planned", plan.get("checks")
    blocking = [f for f in plan["checks"]["semantic"]
                if str(f.get("severity") or "reject") != "advisory"]
    assert blocking == [], blocking
    assert codes_of(plan["checks"]["semantic"]) == ["causal_gap"]
    advisories = [a.get("code") for a in
                  (plan["execution_binding"].get("advisories") or [])]
    assert advisories == ["causal_gap"]
    assert plan["checks"]["unresolved_gate"] == []
    assert validate_plan_identity(plan, script, scope=family["scope"]) == []


def test_required_main_subject_missing_is_detected():
    family = _family("temporal_ellipsis")
    script = copy.deepcopy(family["baseline"]["script"])
    script["shots"][0]["main_subject"] = "工匠阿岚"
    for frame in ("ff", "lf"):
        script["shots"][0]["frame_layouts"][frame] = [
            e for e in script["shots"][0]["frame_layouts"][frame]
            if e.get("name") != "工匠阿岚"
        ]
    assert any(item["code"] == "required_character_missing"
               for item in build_visual_plan(
                   script, scope=family["scope"], only_indices={1})["checks"]["preflight"])


def test_reference_identity_mismatch_is_detected():
    family = _family("voice_timing")
    plan = build_visual_plan(family["baseline"]["script"], scope=family["scope"],
                             only_indices={3})
    frame = plan["frames"][0]
    frame["references"] = [{"role": "design_reference", "path": "x.png",
                            "purpose": "身份锚点", "sha256": "a" * 64,
                            "prepared": True}]
    result = verify_frame_observations(plan, [{
        "frame_id": frame["frame_id"],
        "visible_characters": frame["visible_characters"],
        "visible_props": frame["visible_props"],
        "reference_sha256": {"design_reference": "b" * 64},
    }])
    codes = {i["code"] for i in result["issues"]}
    assert "reference_identity_mismatch" in codes
    assert result["attempts"][frame["frame_id"]]["status"] == "failed"


def test_single_frame_failure_does_not_invalidate_other_verified_attempts():
    family = _family("offscreen_voice")
    plan = build_visual_plan(family["baseline"]["script"], scope=family["scope"],
                             only_indices={2, 5})
    good = plan["frames"][0]
    bad = plan["frames"][1]
    result = verify_frame_observations(plan, [
        {"frame_id": good["frame_id"],
         "visible_characters": good["visible_characters"],
         "visible_props": good["visible_props"]},
        {"frame_id": bad["frame_id"], "visible_characters": [], "visible_props": []},
    ])
    assert result["attempts"][good["frame_id"]]["status"] == "verified"
    assert result["attempts"][bad["frame_id"]]["status"] == "failed"
    assert result["verified_count"] == 1
    assert result["failed_count"] == 1


def test_stale_script_rejects_saved_plan_before_send():
    family = _family("temporal_ellipsis")
    script = family["baseline"]["script"]
    plan = build_visual_plan(script, scope=family["scope"], only_indices={1})
    changed = copy.deepcopy(script)
    changed["shots"][0]["prompt"] += "（改）"
    issues = validate_plan_identity(plan, changed, scope=family["scope"])
    codes = {i["code"] for i in issues}
    assert "source_digest_stale" in codes


def test_plan_round_trips_to_explicit_path(tmp_path):
    family = _family("voice_timing")
    plan = build_visual_plan(family["baseline"]["script"], scope=family["scope"],
                             only_indices={3, 4})
    target = write_visual_plan(plan, tmp_path / "visual_plan.json")
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["schema"] == "step02a.visual_plan.v1"
    assert loaded["execution_binding"]["source"]["source_digest"] == script_digest(
        family["baseline"]["script"])
    assert loaded["status"] == "planned"
