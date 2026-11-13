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


# ---------------------------------------------------------------------------
# 过程载体必须**绑定到被质疑的因果**（2026-11-12 复核第二例）
# ---------------------------------------------------------------------------
def _causal_base(*, bound: bool = False, process: bool = False) -> dict:
    """起因 → 结果，没有「行动」标签.

    `bound` 控制镜头是否带显式计划绑定（`plan_refs`）；`process` 控制是否有动作分段。
    """
    shot = {"index": 1, "duration_sec": 5.0, "ff_desc": "院中，甲与乙相对而立",
            "lf_desc": "甲拱手", "motion_desc": "甲拱手",
            "prompt": "院中", "narration_segment": "", "context_bridge": ""}
    if process:
        shot["motion_desc"] = "前2秒：甲拱手；后2秒：甲转身"
    if bound:
        shot["plan_refs"] = {"beats": [1], "lines": []}
    return {
        "chapter_outline": {
            "causal_chain": ["起因：甲与乙约定赴城", "结果：乙已抵达城门"],
            "narration_lines": [],
            "main_events": [],
        },
        "shots": [shot],
        "total_duration": 5.0,
    }


def _add_shot(script: dict, shot: dict) -> dict:
    out = copy.deepcopy(script)
    out["shots"].append(shot)
    out["total_duration"] = sum(float(s.get("duration_sec") or 0)
                                for s in out["shots"])
    return out


def test_unbound_carrier_is_pending_not_exempt_and_not_rejected():
    """没有显式绑定时：不豁免、也不认定因果缺失，状态交给语义评阅."""
    base = _causal_base()
    findings = check_script_semantics(base)
    assert codes_of(findings) == ["causal_gap"], findings
    gap = findings[0]
    assert gap["binding_status"] == "unbound"
    assert gap["needs_semantic_review"] is True


def test_unrelated_process_shot_does_not_change_an_unbound_verdict():
    """加一镜共享地点、但没有 `plan_refs` 的鸟，裁决不变（不得据此豁免）."""
    base = _causal_base()
    with_bird = _add_shot(base, {
        "index": 2, "duration_sec": 3.0, "ff_desc": "城门上方一只鸟飞过",
        "lf_desc": "鸟振翅飞走", "motion_desc": "前1秒：鸟掠过；后1秒：鸟飞远",
        "prompt": "城门", "narration_segment": "", "context_bridge": "",
        "keyframes": [{"t_sec": 1.0, "desc": "一只鸟掠过城门"}],
    })
    assert codes_of(check_script_semantics(base)) == codes_of(
        check_script_semantics(with_bird)) == ["causal_gap"]
    gap = check_script_semantics(with_bird)[0]
    assert gap["carrier_evidence"] == []
    assert "shot:2" in gap["unbound_shots"]


def test_bound_carrier_with_process_exempts_the_reject():
    """已绑定镜头里有过程载体 → 只留结构 advisory."""
    findings = check_script_semantics(_causal_base(bound=True, process=True))
    assert codes_of(findings) == ["causal_gap"], findings
    gap = findings[0]
    assert gap["binding_status"] == "bound"
    assert gap["carrier_evidence"] == ["shot:1"]


def test_bound_without_process_still_rejects():
    """已绑定镜头里既无过程、也无时间标记 → 仍然是删过程."""
    findings = check_script_semantics(_causal_base(bound=True, process=False))
    assert codes_of(findings) == ["causal_gap", "time_ellipsis_lost"], findings


def test_temporal_marker_outside_the_bound_shots_does_not_exempt():
    """未绑定镜头里的「次日」不影响当前问题（时间标记按镜头范围核对）."""
    base = _causal_base(bound=True, process=False)
    with_marker = _add_shot(base, {
        "index": 2, "duration_sec": 3.0, "ff_desc": "另一处场景",
        "lf_desc": "场景延续", "motion_desc": "场景空镜",
        "prompt": "别处", "narration_segment": "",
        "context_bridge": "次日，别处",
    })
    codes = codes_of(check_script_semantics(with_marker))
    assert codes == ["causal_gap", "time_ellipsis_lost"], codes


def test_candidate_paths_reports_only_new_findings():
    family = _family("offscreen_voice")
    baseline_candidate = family["baseline"]["execution"]["candidate"]
    mutation_candidate = family["mutation"]["execution"]["candidate"]
    from shared.execution_semantics import check_candidate_paths
    findings = check_candidate_paths(
        family["mutation"]["script"], baseline_candidate, mutation_candidate)
    assert {f["code"] for f in findings} == {
        "offscreen_speaker_visible", "listener_reaction_missing"}
