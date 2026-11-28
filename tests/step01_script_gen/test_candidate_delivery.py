"""Self-contained regressions for voice preservation and explicit edit mapping."""
from copy import deepcopy
from types import SimpleNamespace

import pytest


def plan_for(text):
    return {
        "dialogue_lines": [],
        "narration_lines": [{"text": text, "source": text, "fact_refs": []}],
        "shot_beats": ["节拍：观察｜本镜信息目的：确认｜原文依据：略｜载体：旁白1"],
    }


def context(text, approved):
    shot = {"index": 7, "narration_segment": text, "subtitle": text,
            "plan_refs": {"beats": [1], "lines": []}, "main_subject": "甲"}
    script = {"shots": [shot], "characters": [{"name": "甲甲"}],
              "chapter_outline": plan_for(approved)}
    return SimpleNamespace(script=script, shots=script["shots"], hook=None,
                           novel_text='甲甲说：“' + text + '”')


@pytest.mark.parametrize("text", ["Wait...  then go!", "甲甲与朋友们约定在雨停之后一起出发。"])
def test_approved_voice_preserves_punctuation_spacing_and_clause(text):
    from step01_script_gen.constraints.narration_subtitle_consistency import NarrationSubtitleConsistencyConstraint
    from step01_script_gen.validation import post_fix_shot_prompts
    ctx = context(text, text)
    post_fix_shot_prompts(ctx.script)
    NarrationSubtitleConsistencyConstraint().apply(ctx)
    assert ctx.shots[0]["narration_segment"] == text
    assert ctx.shots[0]["subtitle"] == text


def test_approved_sentence_fragment_not_backfilled_as_dialogue():
    from step01_script_gen.constraints.dialogue_density import DialogueDensityConstraint
    ctx = context("等雨停。", "先收好证物。等雨停。然后一起离开。")
    DialogueDensityConstraint().apply(ctx)
    assert ctx.shots[0]["narration_segment"] == "等雨停。"


def sample():
    from step01_script_gen.candidate_edit import object_digest
    plan = plan_for("先收好证物。等雨停。")
    source = {"shots": [
        {"index": 41, "motion_desc": "关门。", "duration_sec": 4,
         "plan_refs": {"beats": [1], "lines": []}},
        {"index": 83, "motion_desc": "取出证物。", "duration_sec": 4,
         "plan_refs": {"beats": [2], "lines": []}},
    ]}
    target = {"index": 1, "motion_desc": "先关门再取出证物。", "duration_sec": 8,
              "narration_segment": "先收好证物。等雨停。", "subtitle": "先收好证物。等雨停。",
              "plan_refs": {"beats": [1], "lines": []}}
    spec = {"schema": "candidate_edit.v1", "plan_sha256": object_digest(plan),
            "source_sha256": object_digest(source), "shots": [{
                "edit_id": "entry", "sources": [
                    {"index": s["index"], "sha256": object_digest(s)} for s in source["shots"]],
                "reason": "连续空间合并，事件次序保留，语义仍需评阅。", "target": target}]}
    return plan, source, spec


def test_explicit_merge_does_not_mutate_inputs_or_retime():
    from step01_script_gen.candidate_edit import build_candidate
    plan, source, spec = sample()
    original = deepcopy((plan, source, spec))
    candidate, problems = build_candidate(plan, source, spec)
    assert not problems
    assert candidate["shots"] == [spec["shots"][0]["target"]]
    assert (plan, source, spec) == original
    assert candidate["chapter_outline"] == plan


@pytest.mark.parametrize("mutation,code", [
    ("source", "source_digest_mismatch"), ("plan", "plan_digest_mismatch"),
    ("locator", "source_identity_mismatch"), ("duplicate", "duplicate_target"),
    ("missing", "source_identity_missing"), ("duration", "invalid_target"),
])
def test_wrong_version_mapping_and_invalid_targets_rejected(mutation, code):
    from step01_script_gen.candidate_edit import build_candidate
    plan, source, spec = sample()
    if mutation == "source": source["shots"][0]["motion_desc"] = "开门。"
    if mutation == "plan": plan["narration_lines"][0]["text"] = "不要离开。"
    if mutation == "locator": spec["shots"][0]["sources"][0]["index"] = 83
    if mutation == "duplicate": spec["shots"].append(deepcopy(spec["shots"][0]))
    if mutation == "missing": spec["shots"][0]["sources"][0]["index"] = 99
    if mutation == "duration": spec["shots"][0]["target"]["duration_sec"] = float("nan")
    candidate, problems = build_candidate(plan, source, spec)
    assert candidate == {}
    assert problems[0]["code"] == code


def test_freeze_requires_all_finalize_errors_and_full_stability():
    from step01_script_gen.candidate_edit import finalize_candidate
    candidate = {"shots": [{"index": 1, "keyframes": [{"desc": "坐下。"}]}]}
    def failed(script): return script, ["timeline invalid"]
    with pytest.raises(ValueError, match="finalize_errors"):
        finalize_candidate(candidate, failed, lambda s: s)
    count = 0
    def drift(script):
        nonlocal count
        count += 1
        script["shots"][0]["keyframes"][0]["desc"] = str(count)
        return script, []
    with pytest.raises(ValueError, match="finalize_drift"):
        finalize_candidate(candidate, drift, lambda s: s)
    assert candidate["shots"][0]["keyframes"][0]["desc"] == "坐下。"


def test_reading_uses_final_timecode_and_full_text():
    from step01_script_gen.candidate_edit import reading_markdown
    script = {"hook": {"duration_sec": 4}, "shots": [
        {"index": 1, "duration_sec": 5, "timecode": {"start": 4, "end": 9},
         "narration_segment": "很长的完整文本。" * 30, "motion_desc": "关门。",
         "keyframes": [{"t_sec": 2, "desc": "手扶门框。"}]}]}
    text = reading_markdown(script, revision="frozen-revision")
    assert "4.00–9.00" in text and "0.00–5.00" in text
    assert script["shots"][0]["narration_segment"] in text
    assert "手扶门框。" in text and "frozen-revision" in text
