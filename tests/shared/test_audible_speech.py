# -*- coding: utf-8 -*-
"""R7：声音承载回读告警（2026-11-19 修：消费真实 SpeechUnit）."""
from __future__ import annotations

from types import SimpleNamespace

from shared.audible_speech import missing_audible_speech_units
from shared.script_preflight import run as preflight_run


def _shot(idx: int, *, audio: str = "", motion: str = "", lines=None) -> dict:
    return {"index": idx, "audio_cue": audio, "motion_desc": motion,
            "prompt": "", "ff_desc": "", "lf_desc": "",
            "plan_refs": {"beats": [], "lines": list(lines or [])}}


def _script(*shots) -> dict:
    return {"shots": list(shots), "chapter_outline": {"narration_lines": [],
                                                       "dialogue_lines": []}}


def _unit(kind: str, source_index: int, role: str = "", text: str = "") -> object:
    return SimpleNamespace(kind=kind, source_index=source_index, voice_role=role,
                           text=text, speaker="")


def test_threat_without_unit_is_warned():
    script = _script(_shot(11, audio="人声=张十三压低嗓音厉色威胁；音效=衣料摩擦",
                           motion="他低声厉色开口，目光扫向岸边"))
    warnings = missing_audible_speech_units(
        script, units=[], voice_unit_records=[], audio_mode="tts_mix")
    assert [w["code"] for w in warnings] == ["missing_audible_speech_unit"] * 2
    assert {w["field"] for w in warnings} == {"audio_cue", "motion_desc"}
    assert warnings[0]["shot"] == 11


def test_dialogue_unit_on_shot_clears_warning():
    script = _script(_shot(11, audio="人声=张十三压低嗓音厉色威胁",
                           motion="他低声厉色开口"))
    warnings = missing_audible_speech_units(
        script, units=[_unit("dialogue", 11, text="你若不想吃官司，便听我吩咐")],
        voice_unit_records=[], audio_mode="tts_mix")
    assert warnings == []


def test_plan_record_but_no_actual_unit_is_warned():
    """旧实现的漏报：计划记录覆盖该镜，但最终没有 SpeechUnit."""
    script = _script(_shot(11, audio="人声=张十三压低嗓音厉色威胁"))
    warnings = missing_audible_speech_units(
        script, units=[],
        voice_unit_records=[{"kind": "dialogue", "index": 1,
                             "shot_indexes": ["11"]}],
        audio_mode="tts_mix")
    assert warnings and warnings[0]["code"] == "missing_audible_speech_unit"


def test_only_unrelated_narration_does_not_cover_dialogue():
    script = _script(_shot(11, audio="人声=张十三压低嗓音厉色威胁"))
    warnings = missing_audible_speech_units(
        script, units=[_unit("narration", 4, role="", text="无关旁白")],
        voice_unit_records=[{"kind": "narration", "index": 4,
                             "shot_indexes": ["11"]}],
        audio_mode="tts_mix")
    assert warnings and warnings[0]["code"] == "missing_audible_speech_unit"


def test_wrong_role_on_same_shot_is_warned():
    script = _script(_shot(11, audio="人声=张十三压低嗓音厉色威胁"))
    warnings = missing_audible_speech_units(
        script, units=[_unit("dialogue", 11, role="听香", text="无关台词")],
        voice_unit_records=[], audio_mode="tts_mix")
    assert warnings and "角色" in warnings[0]["detail"]


def test_structured_dialogue_contract_can_be_hard():
    script = _script(_shot(11, audio="人声=张十三压低嗓音厉色威胁", lines=[1]))
    warnings = missing_audible_speech_units(
        script, units=[], voice_unit_records=[], audio_mode="tts_mix")
    assert warnings and warnings[0]["severity"] == "hard"


def test_cross_shot_narration_covers_both_shots():
    script = _script(_shot(4, audio="人声=张十三内心旁白（压低嗓音，不张口）"))
    warnings = missing_audible_speech_units(
        script, units=[_unit("narration", 2, role="张十三", text="N2")],
        voice_unit_records=[{"kind": "narration", "index": 2,
                             "shot_indexes": ["3", "4"]}],
        audio_mode="tts_mix")
    assert warnings == []


def test_environment_only_does_not_warn():
    script = _script(_shot(1, audio="人声=无；音效=箫声、蝉鸣、风声",
                           motion="镜头缓慢推近，荷叶轻晃"))
    assert missing_audible_speech_units(
        script, units=[], voice_unit_records=[], audio_mode="tts_mix") == []


def test_native_mode_does_not_warn():
    script = _script(_shot(11, audio="人声=张十三压低嗓音厉色威胁",
                           motion="他低声厉色开口"))
    assert missing_audible_speech_units(
        script, units=[], voice_unit_records=[], audio_mode="native") == []


def test_preflight_consumes_same_warning():
    script = _script(_shot(11, audio="人声=张十三压低嗓音厉色威胁",
                           motion="他低声厉色开口"))
    issues = preflight_run(script, include_audible_speech=True,
                           speech_units=[], audio_mode="tts_mix")
    assert any(i["code"] == "missing_audible_speech_unit" for i in issues)
