# -*- coding: utf-8 -*-
"""S02 R2：三个样本族的语义检查器必须**自动**检出 expected.codes.

本文件不读取 ``expected.codes`` 来生成判定：测试先从夹具拿到原始基线，调用
``shared.execution_semantics`` 得到 findings，再与 ``expected.codes`` 对照。
每个 code 另有最小、独立的变异用例，证明它不是笼统地「一起报错」。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from shared.execution_semantics import (
    KNOWN_CODES,
    apply_execution_candidate,
    check_execution_semantics,
    check_script_semantics,
    codes_of,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "execution_contract"
FAMILIES = ("temporal_ellipsis", "offscreen_voice", "voice_timing")


def _family(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _candidate_codes(family: dict, side: str) -> list[str]:
    return codes_of(check_execution_semantics(
        family[side]["script"], family[side]["execution"]["candidate"]))


@pytest.mark.parametrize("name", FAMILIES)
def test_fixture_manifest_expectation_is_autodetected(name):
    family = _family(name)
    baseline_expected = sorted(family["baseline"]["expected"]["codes"])
    mutation_expected = sorted(family["mutation"]["expected"]["codes"])
    assert _candidate_codes(family, "baseline") == baseline_expected
    assert _candidate_codes(family, "mutation") == mutation_expected
    assert set(mutation_expected) <= KNOWN_CODES
    for side in ("baseline", "mutation"):
        for finding in check_execution_semantics(
                family[side]["script"], family[side]["execution"]["candidate"]):
            # 2026-11-12（复核 P4）：`causal_gap` 是**计划结构记录**（标签缺失），
            # 不阻塞——合法省略在 Step01 通过、到 Step02a 又被拒收就是这条造成的。
            # 其余 code 仍必须是 reject；这条断言防止 advisory 被扩大到别的判据上。
            if finding["code"] == "causal_gap":
                assert finding["severity"] == "advisory", finding
            else:
                assert finding["severity"] == "reject", finding
            assert finding["path"]
            assert finding["message"]


def test_causal_gap_isolated_from_time_ellipsis():
    family = _family("temporal_ellipsis")
    baseline = family["baseline"]
    mutated = copy.deepcopy(baseline["script"])
    outline = mutated["chapter_outline"]
    outline["causal_chain"] = [entry for entry in outline["causal_chain"]
                               if not entry.startswith("行动：")]
    # 保留时间标记与过程载体：只缺「行动」角色。
    codes = codes_of(check_execution_semantics(mutated, baseline["execution"]["candidate"]))
    assert codes == ["causal_gap"]


def test_time_ellipsis_lost_requires_missing_temporal_or_process_carrier():
    family = _family("temporal_ellipsis")
    baseline = family["baseline"]
    mutated = copy.deepcopy(baseline["script"])
    outline = mutated["chapter_outline"]
    outline["causal_chain"] = [entry for entry in outline["causal_chain"]
                               if not entry.startswith("行动：")]
    for shot in mutated["shots"]:
        for key in ("prompt", "narration_segment", "audio_cue", "motion_desc",
                    "ff_desc", "lf_desc"):
            if isinstance(shot.get(key), str):
                shot[key] = shot[key].replace("三日后", "").replace("三日", "")
    outline["narration_lines"] = []
    outline["main_events"] = [e for e in outline["main_events"] if "三日" not in e]
    codes = codes_of(check_script_semantics(mutated))
    assert codes == ["causal_gap", "time_ellipsis_lost"]


def test_offscreen_speaker_visible_isolated():
    family = _family("offscreen_voice")
    baseline = family["baseline"]
    mutated = copy.deepcopy(baseline["script"])
    unit = mutated["chapter_outline"]["voice_units"][0]
    unit["on_screen"] = True
    shot = next(s for s in mutated["shots"] if s["index"] == unit["shot_index"])
    for frame in ("ff", "lf"):
        shot["frame_layouts"][frame].append(
            {"name": "门外来客", "on_screen": True, "position": "门口", "pose": "站立"})
    codes = codes_of(check_script_semantics(mutated))
    assert codes == ["offscreen_speaker_visible"]


def test_listener_reaction_missing_isolated():
    family = _family("offscreen_voice")
    baseline = family["baseline"]
    mutated = copy.deepcopy(baseline["script"])
    listener = "值守者阿澄"
    for shot in mutated["shots"]:
        if shot.get("index") != 2:
            continue
        shot["motion_desc"] = "阿澄继续擦灯"
        shot["prompt"] = "[中景] 旧驿站堂内，阿澄背对镜头擦铜灯，门在画左；门外传来人声"
        shot["lf_desc"] = "阿澄背对镜头继续擦灯，门仍然关着"
        for frame in ("ff", "lf"):
            for entry in shot["frame_layouts"][frame]:
                if entry.get("name") == listener:
                    entry["pose"] = "站立擦灯"
                    entry["gaze_target"] = "铜灯"
                    entry["position"] = "堂内"
    codes = codes_of(check_script_semantics(mutated))
    assert codes == ["listener_reaction_missing"]


def test_audio_window_overflow_isolated():
    family = _family("voice_timing")
    baseline = family["baseline"]
    mutated = copy.deepcopy(baseline["script"])
    mutated["chapter_outline"]["voice_units"][1]["actual_sec"] = 3.4
    codes = codes_of(check_script_semantics(mutated))
    assert codes == ["audio_window_overflow"]


def test_native_dialogue_unverified_isolated():
    family = _family("voice_timing")
    baseline = family["baseline"]
    mutated = copy.deepcopy(baseline["script"])
    mutated["chapter_outline"]["voice_units"][0]["verified"] = False
    codes = codes_of(check_script_semantics(mutated))
    assert codes == ["native_dialogue_unverified"]


def test_legitimate_candidate_refinement_keeps_baseline_green():
    family = _family("offscreen_voice")
    baseline = family["baseline"]
    candidate = copy.deepcopy(baseline["execution"]["candidate"])
    candidate["shots"][0]["prompt"] = "阿澄背对镜头擦亮铜灯，门外传来人声，阿澄转头看向门"
    merged = apply_execution_candidate(baseline["script"], candidate)
    assert codes_of(check_script_semantics(merged)) == []


def test_candidate_paths_reports_only_new_findings():
    family = _family("offscreen_voice")
    baseline_candidate = family["baseline"]["execution"]["candidate"]
    mutation_candidate = family["mutation"]["execution"]["candidate"]
    from shared.execution_semantics import check_candidate_paths
    findings = check_candidate_paths(
        family["mutation"]["script"], baseline_candidate, mutation_candidate)
    assert {f["code"] for f in findings} == {
        "offscreen_speaker_visible", "listener_reaction_missing"}
