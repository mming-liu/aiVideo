# -*- coding: utf-8 -*-
"""人物声音唯一性（匿名角色，离线）."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from step03_tts_audio.shot_tts import (canonical_character_role,
                                       check_voice_uniqueness)
from tts.voice_registry import (CHARACTER_VOICES, VoicePoolExhaustedError,
                                VoiceRegistry)


def _unit(role: str, voice: str, kind: str = "dialogue") -> object:
    return SimpleNamespace(kind=kind, voice_role=role,
                           voice_profile_id=voice, model_id="", speaker="")


def test_cross_chapter_same_role_reuses_voice(tmp_path):
    path = tmp_path / "voice_registry.json"
    first = VoiceRegistry(path).get_voice("甲", "male", "young")
    again = VoiceRegistry(path).get_voice("甲", "male", "young")
    assert again["id"] == first["id"]


def test_legacy_pool_exhaustion_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_STRICT_UNIQUE", "1")
    reg = VoiceRegistry(tmp_path / "voice_registry.json")
    for i in range(len(CHARACTER_VOICES)):
        reg.get_voice("角色%s" % i, "male", "young")
    with pytest.raises(VoicePoolExhaustedError) as exc:
        reg.get_voice("角色X", "male", "young")
    assert exc.value.remaining == 0
    assert len(exc.value.occupied) == len(CHARACTER_VOICES)


def test_new_pool_narrator_reserves_speaker_and_exhausts(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_STRICT_UNIQUE", "1")
    reg = VoiceRegistry(tmp_path / "voice_registry.json")
    legal = ("spk-a", "spk-b", "spk-c")
    first = reg.profile_for("甲", provider="p", legal_speakers=legal,
                            work_id="w", mapping_version="v1")
    assert reg.profiles["w"]["Narrator"]["speaker"] in legal
    second = reg.profile_for("乙", provider="p", legal_speakers=legal,
                             work_id="w", mapping_version="v1")
    assert first["speaker"] != second["speaker"]
    with pytest.raises(VoicePoolExhaustedError):
        reg.profile_for("丙", provider="p", legal_speakers=legal,
                        work_id="w", mapping_version="v1")


def test_explicit_collision_is_rejected(tmp_path):
    reg = VoiceRegistry(tmp_path / "voice_registry.json")
    legal = ("spk-a", "spk-b", "spk-c")
    a = reg.profile_for("甲", provider="p", legal_speakers=legal, work_id="w")
    with pytest.raises(VoicePoolExhaustedError):
        reg.profile_for("乙", provider="p", legal_speakers=legal, work_id="w",
                        speaker=a["speaker"])
    narrator = reg.profiles["w"]["Narrator"]["speaker"]
    with pytest.raises(VoicePoolExhaustedError):
        reg.profile_for("丙", provider="p", legal_speakers=legal, work_id="w",
                        speaker=narrator)


def test_alias_normalization_does_not_split():
    chars = [{"identifier": "甲", "aliases": ["甲哥"]},
             {"identifier": "乙", "alias_map": {"乙叔": "乙"}}]
    assert canonical_character_role("甲哥", chars) == "甲"
    assert canonical_character_role("乙叔", chars) == "乙"
    units = [_unit("甲", "v1"), _unit("甲哥", "v1")]
    assert check_voice_uniqueness(units, None, chars)["ok"] is True


def test_uniqueness_conflicts_are_reported():
    assert not check_voice_uniqueness(
        [_unit("甲", "v1"), _unit("甲", "v2")], None, [])["ok"]
    assert not check_voice_uniqueness(
        [_unit("甲", "v1"), _unit("乙", "v1")], None, [])["ok"]
    assert not check_voice_uniqueness(
        [_unit("Narrator", "v1", kind="narration"), _unit("甲", "v1")],
        None, [])["ok"]
    assert check_voice_uniqueness([_unit("甲", "v1")], None, [])["ok"]
