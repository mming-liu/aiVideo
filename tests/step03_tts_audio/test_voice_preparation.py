# -*- coding: utf-8 -*-
"""S05：声音准备/实测、窗口冲突、来源与身份反馈（离线；ffmpeg 造本地音频）."""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from shared.voice_preparation import (
    NO_AUDIO_CONFIRMATION,
    build_voice_preparation,
    feedback_for_conflicts,
    measure_units,
)

FIXTURES = Path(__file__).resolve().parents[1] / "shared" / "fixtures" / "execution_contract"
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def _family(name: str = "voice_timing") -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _windows() -> dict:
    return {"3": 4.0, "4": 4.0}


def _codes(report: dict) -> list[str]:
    return sorted({c["code"] for c in report["conflicts"]})


def test_baseline_ready_and_mutation_codes_detected():
    family = _family()
    baseline = build_voice_preparation(family["baseline"]["script"],
                                       scope=family["scope"], windows=_windows())
    assert baseline["status"] == "ready"
    assert baseline["codes"] == []
    assert {u["unit_id"] for u in baseline["units"]} == {"d01", "d02", "n01"}
    assert baseline["unverified"] == ["d01", "d02", "n01"]  # plan 阶段没有实测

    mutation = build_voice_preparation(family["mutation"]["script"],
                                       scope=family["scope"], windows=_windows())
    assert mutation["status"] == "blocked"
    assert _codes(mutation) == ["audio_window_overflow", "native_dialogue_unverified"]
    routed = feedback_for_conflicts(mutation)
    assert {r["code"] for r in routed} == {"audio_window_overflow",
                                           "native_dialogue_unverified"}
    assert all(r["route"] == "voice_execution" for r in routed)


def test_plan_stage_overflow_is_planning_not_blocking():
    """G6：准备态（未实测）的计划超窗只进 planning_issues，不阻断 status."""
    family = _family()
    script = copy.deepcopy(family["baseline"]["script"])
    unit = script["chapter_outline"]["voice_units"][1]
    unit.pop("verified", None)
    unit["actual_sec"] = 3.4          # 计划声明值，未实测
    report = build_voice_preparation(script, scope=family["scope"], windows=_windows())
    assert report["status"] == "ready"
    assert report["codes"] == []
    assert "audio_window_overflow_planned" in report["planning_codes"]
    by_id = {u["unit_id"]: u for u in report["units"]}
    assert by_id["d02"]["state"] == "prepared"
    assert by_id["d02"]["actual_sec"] is None      # 声明值不冒充实测
    assert by_id["d02"]["declared_sec"] == 3.4
    assert by_id["n01"]["state"] == "confirmed"    # 源侧 verified=true 保留确认态


def test_dual_dialogue_source_and_text_identity_route_step01():
    family = _family()
    script = copy.deepcopy(family["baseline"]["script"])
    units = script["chapter_outline"]["voice_units"]
    units.append({"unit_id": "d03", "kind": "tts_dialogue", "speaker": "乐师阿遥",
                  "text": "这一曲，送你。", "shot_index": 3, "window_sec": 4.0,
                  "actual_sec": 2.4, "verified": True})
    units[1]["text"] = "换了一句。"
    units[1]["speaker"] = "不存在的人"
    report = build_voice_preparation(script, scope=family["scope"], windows=_windows())
    assert "dual_dialogue_source" in report["codes"]
    assert "voice_text_mismatch" in report["codes"]
    assert "voice_speaker_unknown" in report["codes"]
    routed = {r["code"]: r["route"] for r in feedback_for_conflicts(report)}
    assert routed["voice_text_mismatch"] == "step01"
    assert routed["voice_speaker_unknown"] == "step01"
    assert routed["dual_dialogue_source"] == "step01"


def test_missing_audio_track_only_when_artifacts_required():
    family = _family()
    plan = build_voice_preparation(family["baseline"]["script"],
                                   scope=family["scope"], windows=_windows())
    assert "audio_track_missing" not in plan["codes"]
    measured = build_voice_preparation(family["baseline"]["script"],
                                       scope=family["scope"], windows=_windows(),
                                       require_audio_artifacts=True)
    assert "audio_track_missing" in measured["codes"]


def test_no_audio_requirement_requires_explicit_confirmation():
    script = {"book_name": "匿名", "chapter_index": 1, "script_revision": "r" * 64,
              "shots": [{"index": 1, "duration_sec": 4.0}],
              "characters": [], "chapter_outline": {"dialogue_lines": [],
                                                    "narration_lines": [],
                                                    "voice_units": []}}
    report = build_voice_preparation(script, scope={"chapter": 1}, windows={"1": 4.0})
    assert report["status"] == "ready"
    assert report["no_audio_confirmation"]["code"] == NO_AUDIO_CONFIRMATION


def _make_wav(path: Path, seconds: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
         "sine=frequency=440:sample_rate=16000:duration=%.3f" % seconds,
         "-c:a", "pcm_s16le", str(path)], check=True)
    return path


@pytest.mark.skipif(FFMPEG is None or FFPROBE is None,
                    reason="需要本机 ffmpeg/ffprobe 造并探测音频")
def test_measured_audio_round_trip_and_overflow(tmp_path):
    family = _family()
    paths = {
        "d01": str(_make_wav(tmp_path / "d01.wav", 2.4)),
        "d02": str(_make_wav(tmp_path / "d02.wav", 1.8)),
        "n01": str(_make_wav(tmp_path / "n01.wav", 1.2)),
    }
    report = build_voice_preparation(
        family["baseline"]["script"], scope=family["scope"], windows=_windows(),
        audio_paths=paths, require_audio_artifacts=True)
    assert report["status"] == "ready"
    assert report["unverified"] == []
    by_id = {u["unit_id"]: u for u in report["units"]}
    assert by_id["d02"]["measurement"] == "ffprobe"
    assert abs(by_id["d02"]["actual_sec"] - 1.8) < 0.05

    bad = dict(paths)
    bad["d02"] = str(_make_wav(tmp_path / "d02_long.wav", 3.4))
    report = build_voice_preparation(
        family["baseline"]["script"], scope=family["scope"], windows=_windows(),
        audio_paths=bad, require_audio_artifacts=True)
    assert "audio_window_overflow" in report["codes"]
    overflow = [c for c in report["conflicts"] if c["code"] == "audio_window_overflow"]
    assert any(c.get("unit_id") == "d02" for c in overflow)
    assert any(c.get("shot_index") == 4 for c in overflow)

    # 实测回填只读本地文件：未知单元忽略、缺失文件明确记未验证。
    updated = measure_units(report, {"n01": str(tmp_path / "missing.wav"),
                                     "unknown": "x"})
    n01 = next(u for u in updated["units"] if u["unit_id"] == "n01")
    assert n01["measurement"] == "audio_file_missing"
    assert "n01" in updated["unverified"]
