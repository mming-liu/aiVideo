# -*- coding: utf-8 -*-
"""Step E：章节声音状态（两态）的构建与写入语义.

覆盖：
- 真实合成产物（存在/非空/时长可读）→ ``confirmed``，含文件摘要与绑定 revision；
- 音轨缺失/过小/时长不可读 → ``prepared`` + 具名 blocking（不冒充确认态）；
- revision 为空 → ``prepared``（无法绑定稿件身份不升级）；
- 调用方声明「未真实合成」（plan-only）→ 即使文件都在也保持 ``prepared``。
"""
from __future__ import annotations

import json
import wave
from pathlib import Path

from shared import voice_state as vs


def _wav(path: Path, seconds: float = 0.6, rate: int = 16000) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = wave.open(str(path), "wb")
    handle.setnchannels(1)
    handle.setsampwidth(2)
    handle.setframerate(rate)
    handle.writeframes(b"\x00\x00" * int(rate * seconds))
    handle.close()
    return path


def _script(revision: str = "rev-voice-1") -> dict:
    return {"script_revision": revision, "shots": [{"index": 1}],
            "chapter_outline": {"voice_units": []}}


def test_confirmed_records_digest_and_revision(tmp_path):
    audio = _wav(tmp_path / "audio" / "shot_1.mp3.wav")
    state = vs.build_voice_state(
        _script(), tracks=[{"unit_id": "shot-1", "kind": "tts_dialogue",
                            "shot_index": 1, "path": str(audio)}],
        scope={"chapter": 1}, source="step03.real_synthesis")

    assert state["schema"] == vs.SCHEMA
    assert state["state"] == vs.STATE_CONFIRMED
    assert state["blocking"] == []
    assert state["script_revision"] == "rev-voice-1"
    unit = state["units"][0]
    assert unit["state"] == vs.STATE_CONFIRMED
    assert unit["duration_sec"] == 0.6
    assert unit["sha256"] and state["digests"]["shot-1"] == unit["sha256"]
    assert state["counts"] == {"required": 1, "confirmed": 1, "prepared": 0}


def test_missing_audio_stays_prepared_with_named_blocking(tmp_path):
    state = vs.build_voice_state(
        _script(), tracks=[{"unit_id": "shot-1", "kind": "tts_dialogue",
                            "path": str(tmp_path / "audio" / "nope.mp3")}])
    assert state["state"] == vs.STATE_PREPARED
    assert [b["code"] for b in state["blocking"]] == ["audio_file_missing"]
    assert state["units"][0]["sha256"] is None


def test_too_small_audio_is_not_confirmed(tmp_path):
    tiny = tmp_path / "tiny.mp3"
    tiny.write_bytes(b"\x00" * 16)
    state = vs.build_voice_state(
        _script(), tracks=[{"unit_id": "shot-1", "kind": "tts_dialogue",
                            "path": str(tiny)}])
    assert state["state"] == vs.STATE_PREPARED
    assert state["blocking"][0]["code"] == "audio_file_too_small"


def test_missing_revision_never_confirms(tmp_path):
    audio = _wav(tmp_path / "a.wav")
    state = vs.build_voice_state(
        {"shots": [{"index": 1}]},
        tracks=[{"unit_id": "shot-1", "kind": "tts_dialogue", "path": str(audio)}])
    assert state["state"] == vs.STATE_PREPARED
    assert "script_revision_missing" in [b["code"] for b in state["blocking"]]


def test_plan_only_reason_keeps_prepared_even_if_files_exist(tmp_path):
    audio = _wav(tmp_path / "a.wav")
    state = vs.build_voice_state(
        _script(), tracks=[{"unit_id": "shot-1", "kind": "tts_dialogue",
                            "path": str(audio)}],
        prepared_reason="plan_only_no_synthesis")
    assert state["state"] == vs.STATE_PREPARED
    assert [b["code"] for b in state["blocking"]] == ["plan_only_no_synthesis"]
    # 单元级事实仍如实记录（文件确实在那里，只是本次没合成）
    assert state["units"][0]["state"] == vs.STATE_CONFIRMED


def test_no_tracks_never_confirms():
    state = vs.build_voice_state(_script(), tracks=[])
    assert state["state"] == vs.STATE_PREPARED
    assert [b["code"] for b in state["blocking"]] == ["audio_units_missing"]


def test_read_voice_state_round_trip(tmp_path):
    target = tmp_path / vs.FILENAME
    target.write_text(json.dumps({"state": "confirmed", "script_revision": "rev"}),
                      encoding="utf-8")
    data = vs.read_voice_state(target)
    assert data["state"] == "confirmed"
    assert vs.read_voice_state(tmp_path / "absent.json") is None
