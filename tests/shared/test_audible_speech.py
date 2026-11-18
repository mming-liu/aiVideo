# -*- coding: utf-8 -*-
"""R7：声音承载回读告警（2026-11-18）."""
from __future__ import annotations

from shared.audible_speech import missing_audible_speech_units
from shared.script_preflight import run as preflight_run


def _shot(idx: int, *, audio: str = "", motion: str = "") -> dict:
    return {"index": idx, "audio_cue": audio, "motion_desc": motion,
            "prompt": "", "ff_desc": "", "lf_desc": ""}


def _script(*shots) -> dict:
    return {"shots": list(shots), "chapter_outline": {"narration_lines": [],
                                                       "dialogue_lines": []}}


def test_threat_without_unit_is_warned():
    script = _script(_shot(11, audio="人声=张十三压低嗓音厉色威胁；音效=衣料摩擦",
                           motion="他低声厉色开口，目光扫向岸边"))
    warnings = missing_audible_speech_units(
        script, voice_unit_records=[], audio_mode="tts_mix")
    assert [w["code"] for w in warnings] == ["missing_audible_speech_unit"] * 2
    assert {w["field"] for w in warnings} == {"audio_cue", "motion_desc"}
    assert warnings[0]["shot"] == 11


def test_dialogue_unit_on_shot_clears_warning():
    script = _script(_shot(11, audio="人声=张十三压低嗓音厉色威胁",
                           motion="他低声厉色开口"))
    warnings = missing_audible_speech_units(
        script, voice_unit_records=[{"shot_indexes": ["11"]}],
        audio_mode="tts_mix")
    assert warnings == []


def test_environment_only_does_not_warn():
    script = _script(_shot(1, audio="人声=无；音效=箫声、蝉鸣、风声",
                           motion="镜头缓慢推近，荷叶轻晃"))
    assert missing_audible_speech_units(
        script, voice_unit_records=[], audio_mode="tts_mix") == []


def test_native_mode_does_not_warn():
    script = _script(_shot(11, audio="人声=张十三压低嗓音厉色威胁",
                           motion="他低声厉色开口"))
    assert missing_audible_speech_units(
        script, voice_unit_records=[], audio_mode="native") == []


def test_preflight_consumes_same_warning():
    script = _script(_shot(11, audio="人声=张十三压低嗓音厉色威胁",
                           motion="他低声厉色开口"))
    issues = preflight_run(script, include_audible_speech=True,
                           speech_units=[], audio_mode="tts_mix")
    assert any(i["code"] == "missing_audible_speech_unit" for i in issues)
