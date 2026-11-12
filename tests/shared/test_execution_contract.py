# -*- coding: utf-8 -*-
"""S02：跨阶段执行绑定/变化权限的离线单测（无网络、无模型、无现场数据）."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from shared.execution_contract import (
    CONTRACT_VERSION,
    GUARDED_SEGMENTS,
    SCHEMA,
    ExecutionContractError,
    attach_semantic_checks,
    build_execution_binding,
    script_digest,
    unresolved_gate_issues,
    validate_execution_binding,
    validate_execution_changes,
)
from shared.execution_semantics import check_execution_semantics

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "execution_contract"
STAGE = "step02a.visual_plan"


def _scope(**overrides) -> dict:
    scope = {"chapter": 1, "book_index": 1, "book_name": "匿名测试书"}
    scope.update(overrides)
    return scope


def _mini_script(**overrides) -> dict:
    script = {
        "book_name": "匿名测试书",
        "book_index": 1,
        "chapter_index": 1,
        "chapter_title": "测试章",
        "script_revision": "d1" * 32,
        "source_hash": "abababababababab",
        "characters": [{"identifier": "甲"}, {"identifier": "乙"}],
        "shots": [
            {"index": 1, "duration_sec": 4.0,
             "plan_refs": {"beats": [1], "lines": []},
             "prompt": "甲在门口", "ff_desc": "甲推门", "lf_desc": "甲站定",
             "motion_desc": "甲推门后站定", "narration_segment": "",
             "frame_layouts": {"ff": [], "lf": [], "kfs": []}, "keyframes": []},
            {"index": 2, "duration_sec": 5.0,
             "plan_refs": {"beats": [2], "lines": [1]},
             "prompt": "乙在窗边", "ff_desc": "乙看向窗外", "lf_desc": "乙回头",
             "motion_desc": "乙看向窗外后回头", "narration_segment": "乙：你来啦。",
             "frame_layouts": {"ff": [], "lf": [], "kfs": []}, "keyframes": []},
        ],
        "chapter_outline": {
            "plan_contract_version": 5,
            "fact_uids": ["f01", "f02"],
            "required_facts": ["甲推门进入。", "乙在窗边回头。"],
            "voice_units": [
                {"unit_id": "d01", "kind": "tts_dialogue", "speaker": "甲",
                 "text": "你来啦。", "shot_index": 2, "window_sec": 3.0,
                 "actual_sec": 1.5, "verified": True},
            ],
            "plan_verification": {"plan_digest": "e" * 64},
        },
    }
    script.update(overrides)
    return script


def _binding(script: dict | None = None, *, scope: dict | None = None,
             stage: str = STAGE) -> dict:
    script = _mini_script() if script is None else script
    return build_execution_binding(
        script, scope=_scope() if scope is None else scope,
        stage=stage, source_digest=script_digest(script))


def _codes(issues: list[dict]) -> set[str]:
    return {str(item.get("code")) for item in issues}


def _paths(issues: list[dict]) -> set[str]:
    return {str(item.get("path")) for item in issues}


def _load_family(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def test_build_binding_captures_identity_requirements_and_defaults():
    script = _mini_script()
    before = copy.deepcopy(script)
    binding = _binding(script)

    assert binding["schema"] == SCHEMA
    assert binding["contract_version"] == CONTRACT_VERSION
    assert binding["stage"] == STAGE
    assert binding["source"]["scope"] == _scope()
    assert binding["source"]["script_revision"] == script["script_revision"]
    assert binding["source"]["source_digest"] == script_digest(script)
    assert binding["source"]["digest_algorithm"] == "sha256.canonical_json.v1"
    assert binding["source"]["input"]["plan_digest"] == "e" * 64

    assert binding["requirements"]["facts"] == [
        {"uid": "f01", "claim": "甲推门进入。"},
        {"uid": "f02", "claim": "乙在窗边回头。"},
    ]
    assert [s["index"] for s in binding["requirements"]["shots"]] == [1, 2]
    assert binding["requirements"]["characters"] == ["甲", "乙"]
    assert binding["requirements"]["voice_units"][0]["unit_id"] == "d01"
    assert binding["requirements"]["voice_units"][0]["speaker"] == "甲"
    assert binding["requirements"]["voice_units"][0]["text"] == "你来啦。"

    assert binding["allowed_changes"] == []
    assert binding["changes"] == []
    assert binding["checks"] == []
    assert binding["unresolved"] == []
    assert binding["feedback"] == []
    assert script == before, "build 不得改写调用方稿件"


def test_build_is_deterministic():
    script = _mini_script()
    assert _binding(script) == _binding(script)


def test_script_digest_ignores_private_runtime_keys_but_tracks_content():
    script = _mini_script()
    with_runtime = copy.deepcopy(script)
    with_runtime["_runtime"] = {"pid": 1, "log": "x"}
    with_runtime["shots"][0]["_cache"] = "ignored"
    assert script_digest(with_runtime) == script_digest(script)

    changed = copy.deepcopy(script)
    changed["shots"][0]["prompt"] = "甲在门内"
    assert script_digest(changed) != script_digest(script)


@pytest.mark.parametrize(("mutate", "code"), [
    (lambda s: s.pop("script_revision"), "script_revision_blank"),
    (lambda s: s.__setitem__("script_revision", "   "), "script_revision_blank"),
    (lambda s: s.__setitem__("chapter_index", 0), "script_chapter_invalid"),
    (lambda s: s.__setitem__("book_name", ""), "script_book_name_blank"),
    (lambda s: s.__setitem__("chapter_outline", []), "chapter_outline_invalid"),
    (lambda s: s.__setitem__("shots", {}), "shots_invalid"),
])
def test_build_rejects_missing_or_blank_script_fields(mutate, code):
    script = _mini_script()
    mutate(script)
    with pytest.raises(ExecutionContractError) as exc:
        build_execution_binding(script, scope=_scope(), stage=STAGE,
                                source_digest=script_digest(script))
    assert exc.value.code == code


@pytest.mark.parametrize(("scope", "code"), [
    ({"chapter": 1, "book_index": 1}, "scope_book_name_blank"),
    ({"chapter": 0, "book_index": 1, "book_name": "x"}, "scope_chapter_invalid"),
    ({"chapter": 1, "book_index": True, "book_name": "x"}, "scope_book_index_invalid"),
    (None, "scope_invalid"),
])
def test_build_rejects_missing_or_blank_scope(scope, code):
    script = _mini_script()
    with pytest.raises(ExecutionContractError) as exc:
        build_execution_binding(script, scope=scope, stage=STAGE,
                                source_digest=script_digest(script))
    assert exc.value.code == code


def test_build_rejects_blank_stage_and_bad_digest():
    script = _mini_script()
    with pytest.raises(ExecutionContractError) as exc:
        build_execution_binding(script, scope=_scope(), stage="   ",
                                source_digest=script_digest(script))
    assert exc.value.code == "stage_blank"

    with pytest.raises(ExecutionContractError) as exc:
        build_execution_binding(script, scope=_scope(), stage=STAGE, source_digest="")
    assert exc.value.code == "source_digest_blank"

    with pytest.raises(ExecutionContractError) as exc:
        build_execution_binding(script, scope=_scope(), stage=STAGE, source_digest="not-a-digest")
    assert exc.value.code == "source_digest_invalid"

    with pytest.raises(ExecutionContractError) as exc:
        build_execution_binding(script, scope=_scope(), stage=STAGE, source_digest="f" * 64)
    assert exc.value.code == "source_digest_mismatch"


# ---------------------------------------------------------------------------
# validate binding
# ---------------------------------------------------------------------------

def test_validate_binding_accepts_matching_binding():
    script = _mini_script()
    assert validate_execution_binding(_binding(script), script,
                                     expected_scope=_scope(), expected_stage=STAGE) == []


def test_validate_binding_ignores_scope_diagnostic_fields():
    script = _mini_script()
    expected = _scope()
    expected.update({"source": {"chapter": "cli"}, "overrides": {}, "summary": "x"})
    assert validate_execution_binding(_binding(script), script,
                                     expected_scope=expected, expected_stage=STAGE) == []


def test_validate_binding_reports_missing_and_blank_fields():
    issues = validate_execution_binding({}, _mini_script(),
                                        expected_scope=_scope(), expected_stage=STAGE)
    assert "field_missing" in _codes(issues)
    assert "contract_version_missing" not in _codes(issues)  # 整个 binding 缺失时先报字段缺失
    assert "binding.stage" in _paths(issues)

    binding = _binding()
    binding["stage"] = "  "
    binding["source"].pop("source_digest")
    issues = validate_execution_binding(binding, _mini_script(),
                                       expected_scope=_scope(), expected_stage=STAGE)
    assert "field_blank" in _codes(issues)
    assert {"binding.stage", "binding.source.source_digest"} <= _paths(issues)

    binding = _binding()
    binding["source"]["scope"]["book_name"] = ""
    binding["requirements"]["facts"][0]["uid"] = " "
    issues = validate_execution_binding(binding, _mini_script(),
                                       expected_scope=_scope(), expected_stage=STAGE)
    assert "field_blank" in _codes(issues)
    assert "requirement_identity_missing" in _codes(issues)


def test_validate_binding_unknown_schema_and_version():
    binding = _binding()
    binding["contract_version"] = 999
    binding["schema"] = "execution_binding.v999"
    issues = validate_execution_binding(binding, _mini_script(),
                                       expected_scope=_scope(), expected_stage=STAGE)
    assert "contract_version_unknown" in _codes(issues)
    assert "schema_unknown" in _codes(issues)

    binding = _binding()
    binding["contract_version"] = ""
    issues = validate_execution_binding(binding, _mini_script(),
                                       expected_scope=_scope(), expected_stage=STAGE)
    assert "field_blank" in _codes(issues)
    assert "binding.contract_version" in _paths(issues)

    for bad_version in (True, 1.0):
        binding = _binding()
        binding["contract_version"] = bad_version
        issues = validate_execution_binding(binding, _mini_script(),
                                           expected_scope=_scope(), expected_stage=STAGE)
        assert "contract_version_unknown" in _codes(issues)


def test_validate_binding_cross_chapter_and_book():
    script = _mini_script()
    binding = _binding(script)

    issues = validate_execution_binding(binding, script,
                                       expected_scope=_scope(chapter=2), expected_stage=STAGE)
    assert "scope_mismatch" in _codes(issues)
    assert "binding.source.scope" in _paths(issues)
    assert issues[0]["expected"]["chapter"] == 2

    issues = validate_execution_binding(binding, script,
                                       expected_scope=_scope(book_name="另一本"),
                                       expected_stage=STAGE)
    assert "scope_mismatch" in _codes(issues)


def test_validate_binding_rejects_scope_not_matching_script_identity():
    script = _mini_script()
    binding = _binding(script, scope=_scope(chapter=2))
    issues = validate_execution_binding(binding, script,
                                       expected_scope=_scope(chapter=2),
                                       expected_stage=STAGE)
    assert "scope_script_mismatch" in _codes(issues)
    assert "binding.source.scope" in _paths(issues)


def test_validate_binding_stage_mismatch():
    issues = validate_execution_binding(_binding(), _mini_script(),
                                       expected_scope=_scope(),
                                       expected_stage="step02b.video")
    assert "stage_mismatch" in _codes(issues)
    assert "binding.stage" in _paths(issues)


def test_validate_binding_wrong_revision_and_stale_input():
    script = _mini_script()
    binding = _binding(script)

    wrong_revision = copy.deepcopy(script)
    wrong_revision["script_revision"] = "e2" * 32
    issues = validate_execution_binding(binding, wrong_revision,
                                       expected_scope=_scope(), expected_stage=STAGE)
    assert "script_revision_mismatch" in _codes(issues)
    assert "source_digest_stale" in _codes(issues)

    same_revision_changed = copy.deepcopy(script)
    same_revision_changed["shots"][0]["prompt"] = "甲在门内"
    issues = validate_execution_binding(binding, same_revision_changed,
                                       expected_scope=_scope(), expected_stage=STAGE)
    assert "script_revision_mismatch" not in _codes(issues)
    assert "source_digest_stale" in _codes(issues)


def test_validate_binding_missing_script_revision():
    script = _mini_script()
    binding = _binding(script)
    script = copy.deepcopy(script)
    script.pop("script_revision")
    issues = validate_execution_binding(binding, script,
                                       expected_scope=_scope(), expected_stage=STAGE)
    assert "script_revision_blank" in _codes(issues)

    script = _mini_script()
    script.pop("book_name")
    script.pop("chapter_index")
    issues = validate_execution_binding(_binding(), script,
                                       expected_scope=_scope(), expected_stage=STAGE)
    assert "script_book_name_blank" in _codes(issues)
    assert "script_chapter_invalid" in _codes(issues)


def test_validate_binding_requires_expected_scope_and_stage():
    issues = validate_execution_binding(_binding(), _mini_script(),
                                       expected_scope=None, expected_stage="")
    assert "expected_scope_missing" in _codes(issues)
    assert "expected_stage_missing" in _codes(issues)


# ---------------------------------------------------------------------------
# validate changes
# ---------------------------------------------------------------------------

def _execution_before() -> dict:
    return {
        "shots": [
            {"index": 1, "prompt": "甲在门口", "camera_movement": "固定", "speaker": ""},
        ],
        "voice_units": [
            {"unit_id": "d01", "speaker": "甲", "text": "你来啦。", "actual_sec": 1.5},
        ],
        "visual_plan": {"visible_characters": ["甲"]},
    }


def _with_reason(binding: dict, path: str, reason: str = "执行细化") -> dict:
    binding = copy.deepcopy(binding)
    binding["changes"] = [{"path": path, "reason": reason}]
    return binding


def _with_semantic_pass(binding: dict, path: str) -> dict:
    binding = copy.deepcopy(binding)
    binding.setdefault("checks", []).append(
        {"kind": "semantic", "checker": "execution_semantics.v1",
         "status": "pass", "path": path})
    return binding


def test_validate_changes_allows_declared_execution_field():
    before = _execution_before()
    after = copy.deepcopy(before)
    after["shots"][0]["prompt"] = "甲在门口停步"
    binding = _with_semantic_pass(_with_reason(_binding(), "shots[0].prompt"),
                                  "shots[0].prompt")
    issues = validate_execution_changes(before, after, binding=binding,
                                        allowed_changes=["prompt"])
    assert issues == []


def test_validate_changes_rejects_undeclared_field():
    before = _execution_before()
    after = copy.deepcopy(before)
    after["shots"][0]["camera_movement"] = "推近"
    binding = _with_reason(_binding(), "shots[0].camera_movement")
    issues = validate_execution_changes(before, after, binding=binding,
                                        allowed_changes=["prompt"])
    assert "change_not_allowed" in _codes(issues)
    assert "shots[0].camera_movement" in _paths(issues)


def test_validate_changes_rejects_protected_fields_even_if_allowed():
    before = _execution_before()
    after = copy.deepcopy(before)
    after["shots"][0]["speaker"] = "乙"
    binding = _with_reason(_binding(), "shots[0].speaker")
    issues = validate_execution_changes(before, after, binding=binding,
                                        allowed_changes=["shots[0].speaker"])
    assert "protected_field_change" in _codes(issues)

    after = copy.deepcopy(before)
    after["voice_units"][0]["text"] = "你走吧。"
    binding = _with_reason(_binding(), "voice_units[0].text")
    issues = validate_execution_changes(before, after, binding=binding,
                                        allowed_changes=["voice_units[0].text"])
    assert "protected_field_change" in _codes(issues)


@pytest.mark.parametrize("changes", [
    [],
    [{"path": "shots[0].prompt", "reason": ""}],
    [{"path": "shots[0].prompt", "reason": "   "}],
])
def test_validate_changes_requires_reason(changes):
    before = _execution_before()
    after = copy.deepcopy(before)
    # 用非语义承载字段验证「已授权但仍缺理由」，避免和 guarded 字段检查混在一起。
    after["shots"][0]["camera_movement"] = "推近"
    binding = _binding()
    binding["changes"] = changes
    issues = validate_execution_changes(before, after, binding=binding,
                                        allowed_changes=["camera_movement"])
    assert "change_reason_missing" in _codes(issues)
    assert "shots[0].camera_movement" in _paths(issues)


def test_validate_changes_missing_or_unknown_binding():
    before = _execution_before()
    after = copy.deepcopy(before)
    after["shots"][0]["prompt"] = "甲在门口停步"

    issues = validate_execution_changes(before, after, binding={}, allowed_changes=["prompt"])
    assert "contract_version_missing" in _codes(issues)

    issues = validate_execution_changes(before, after,
                                        binding={"contract_version": 99},
                                        allowed_changes=["prompt"])
    assert "contract_version_unknown" in _codes(issues)


def test_validate_changes_invalid_allowed_changes():
    before = _execution_before()
    after = copy.deepcopy(before)
    after["shots"][0]["camera_movement"] = "推近"
    binding = _binding()

    issues = validate_execution_changes(before, after, binding=binding,
                                        allowed_changes="camera_movement")
    assert "allowed_changes_invalid" in _codes(issues)
    assert "change_not_allowed" in _codes(issues)

    issues = validate_execution_changes(before, after, binding=binding,
                                        allowed_changes=["camera_movement", "   "])
    assert "allowed_changes_invalid" in _codes(issues)
    assert "allowed_changes[1]" in _paths(issues)


def test_validate_changes_no_diff():
    before = _execution_before()
    assert validate_execution_changes(before, copy.deepcopy(before),
                                      binding=_binding(), allowed_changes=[]) == []


def test_validate_changes_list_growth_and_nested_paths():
    before = _execution_before()
    after = copy.deepcopy(before)
    after["shots"].append({"index": 2, "prompt": "乙在窗边", "camera_movement": "固定", "speaker": ""})
    binding = _with_reason(_binding(), "shots")
    issues = validate_execution_changes(before, after, binding=binding,
                                        allowed_changes=["shots"])
    assert issues == []


def test_validate_changes_parent_permission_covers_child():
    before = _execution_before()
    after = copy.deepcopy(before)
    after["visual_plan"]["visible_characters"] = ["甲", "乙"]
    binding = _with_semantic_pass(_with_reason(_binding(), "visual_plan"), "visual_plan")
    issues = validate_execution_changes(before, after, binding=binding,
                                        allowed_changes=["visual_plan"])
    assert issues == []


# ---------------------------------------------------------------------------
# S01 fixtures：自足、离线、摘要实算
# ---------------------------------------------------------------------------

def test_fixture_manifest_digests_match_file_bytes():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema"] == "execution_contract.fixture_manifest.v1"
    assert [item["family"] for item in manifest["families"]] == [
        "temporal_ellipsis", "offscreen_voice", "voice_timing"]
    for item in manifest["families"]:
        path = FIXTURES / item["file"]
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == item["sha256"]
        assert len(raw) == item["size"]
        family = json.loads(raw.decode("utf-8"))
        assert family["family"] == item["family"]
        assert family["scope"] == item["scope"]
        assert family["baseline"]["expected"]["verdict"] == item["baseline_verdict"]
        assert family["mutation"]["expected"]["verdict"] == item["mutation_verdict"]
        assert family["mutation"]["expected"]["codes"] == item["mutation_codes"]
        baseline_script = family["baseline"]["script"]
        assert item["character_count"] == len(baseline_script["characters"])
        assert item["shot_indices"] == [s["index"] for s in baseline_script["shots"]]
        assert item["voice_unit_count"] == len(
            baseline_script["chapter_outline"].get("voice_units") or [])


def test_fixture_families_build_and_validate_bindings():
    for name in ("temporal_ellipsis", "offscreen_voice", "voice_timing"):
        family = _load_family(name)
        for side in ("baseline", "mutation"):
            script = family[side]["script"]
            stage = family[side]["execution"]["stage"]
            binding = build_execution_binding(
                script, scope=family["scope"], stage=stage,
                source_digest=script_digest(script))
            assert validate_execution_binding(
                binding, script, expected_scope=family["scope"], expected_stage=stage) == []

            outline = script["chapter_outline"]
            assert [f["uid"] for f in binding["requirements"]["facts"]] == outline["fact_uids"]
            assert [s["index"] for s in binding["requirements"]["shots"]] == [
                s["index"] for s in script["shots"]]
            assert [u["unit_id"] for u in binding["requirements"]["voice_units"]] == [
                u["unit_id"] for u in outline.get("voice_units") or []]
            assert binding["source"]["source_digest"] == script_digest(script)


def test_fixture_baseline_binding_is_stale_against_mutation():
    for name in ("temporal_ellipsis", "offscreen_voice", "voice_timing"):
        family = _load_family(name)
        baseline = family["baseline"]["script"]
        mutation = family["mutation"]["script"]
        binding = build_execution_binding(
            baseline, scope=family["scope"], stage=family["baseline"]["execution"]["stage"],
            source_digest=script_digest(baseline))
        issues = validate_execution_binding(
            binding, mutation, expected_scope=family["scope"],
            expected_stage=family["baseline"]["execution"]["stage"])
        assert {"script_revision_mismatch", "source_digest_stale"} <= _codes(issues)


def test_fixture_voice_timing_legal_change_and_text_protection():
    family = _load_family("voice_timing")
    baseline = family["baseline"]
    before = baseline["execution"]["candidate"]
    after = copy.deepcopy(before)
    after["voice_units"][1]["actual_sec"] = 2.1
    binding = build_execution_binding(
        baseline["script"], scope=family["scope"],
        stage=baseline["execution"]["stage"],
        source_digest=script_digest(baseline["script"]))
    binding["changes"] = [{"path": "voice_units[1].actual_sec",
                           "reason": "实测时长更新"}]
    issues = validate_execution_changes(
        before, after, binding=binding,
        allowed_changes=["voice_units[0].actual_sec", "voice_units[1].actual_sec",
                         "voice_units[2].actual_sec", "audio_windows"])
    assert issues == []

    after["voice_units"][1]["text"] = "再弹一次。"
    binding["changes"] = [{"path": "voice_units[1].text", "reason": "改词"}]
    issues = validate_execution_changes(
        before, after, binding=binding,
        allowed_changes=["voice_units[0].actual_sec", "voice_units[1].actual_sec",
                         "voice_units[2].actual_sec", "audio_windows",
                         "voice_units[1].text"])
    assert "protected_field_change" in _codes(issues)


# ---------------------------------------------------------------------------
# S02 R3：prompt / visible_characters 不能绕过语义保护
# ---------------------------------------------------------------------------

def test_guarded_prompt_change_without_semantic_check_is_rejected():
    before = _execution_before()
    after = copy.deepcopy(before)
    after["shots"][0]["prompt"] = "甲在门口停步"
    binding = _with_reason(_binding(), "shots[0].prompt")
    issues = validate_execution_changes(before, after, binding=binding,
                                        allowed_changes=["prompt"])
    assert "semantic_change_unverified" in _codes(issues)
    record = next(i for i in issues if i["code"] == "semantic_change_unverified")
    assert record["route"] == "step01"
    assert record["feedback"][0]["path"] == "shots[0].prompt"
    assert "prompt" in GUARDED_SEGMENTS


def test_offscreen_visible_bypass_is_rejected_even_if_allowed():
    family = _load_family("offscreen_voice")
    baseline = family["baseline"]
    mutation = family["mutation"]
    after = mutation["execution"]["candidate"]
    before = baseline["execution"]["candidate"]
    binding = build_execution_binding(
        mutation["script"], scope=family["scope"],
        stage=mutation["execution"]["stage"],
        source_digest=script_digest(mutation["script"]))
    allowed = ["prompt", "frame_layouts", "visual_plan.visible_characters",
               "visual_plan", "visual_plan.offscreen_speakers"]
    binding["changes"] = [{"path": "visual_plan", "reason": "执行细化"},
                          {"path": "shots", "reason": "执行细化"}]
    issues = validate_execution_changes(before, after, binding=binding,
                                        allowed_changes=allowed)
    paths = _paths(issues)
    assert "visual_plan.visible_characters[1]" in paths
    assert _codes(issues) <= {"semantic_change_unverified", "change_reason_missing"}

    findings = check_execution_semantics(mutation["script"], after)
    binding = attach_semantic_checks(binding, checked_paths=allowed, findings=findings)
    assert {u["code"] for u in binding["unresolved"]} >= {
        "offscreen_speaker_visible", "listener_reaction_missing"}
    assert any(f["route"] == "step01" for f in binding["feedback"])
    assert "semantic_change_unverified" in _codes(unresolved_gate_issues(binding))
    issues = validate_execution_changes(before, after, binding=binding,
                                        allowed_changes=allowed)
    assert "semantic_change_unverified" in _codes(issues)


def test_semantic_pass_record_allows_guarded_execution_refinement():
    before = _execution_before()
    after = copy.deepcopy(before)
    after["shots"][0]["prompt"] = "甲在门口停步"
    script = _mini_script()
    binding = build_execution_binding(
        script, scope=_scope(), stage=STAGE, source_digest=script_digest(script))
    binding = attach_semantic_checks(binding, checked_paths=["shots[0].prompt"],
                                     findings=[])
    binding["changes"] = [{"path": "shots[0].prompt", "reason": "构图细化"}]
    assert validate_execution_changes(before, after, binding=binding,
                                      allowed_changes=["prompt"]) == []
    assert unresolved_gate_issues(binding) == []


def test_protection_is_configurable_per_binding():
    script = _mini_script()
    binding = build_execution_binding(
        script, scope=_scope(), stage=STAGE, source_digest=script_digest(script),
        protection={"guarded_segments": ["camera_movement"]})
    assert "camera_movement" in binding["protection"]["guarded_segments"]
    before = _execution_before()
    after = copy.deepcopy(before)
    after["shots"][0]["camera_movement"] = "推近"
    binding["changes"] = [{"path": "shots[0].camera_movement", "reason": "运镜"}]
    issues = validate_execution_changes(before, after, binding=binding,
                                        allowed_changes=["camera_movement"])
    assert "semantic_change_unverified" in _codes(issues)
