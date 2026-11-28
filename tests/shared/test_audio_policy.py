# -*- coding: utf-8 -*-
"""Audio source selection must follow model capability, not config alone."""
from __future__ import annotations

from shared.audio_policy import resolve_audio_policy


def test_silent_model_falls_back_to_tts(monkeypatch):
    import shared.audio_policy as ap
    monkeypatch.setenv("VIDEO_MODEL_VOICE", "1")
    monkeypatch.setenv("MERGE_AUDIO_MODE", "native")
    monkeypatch.setattr(ap, "active_video_supports_audio", lambda: False)
    monkeypatch.setattr(ap, "active_video_model", lambda: "silent-model")
    policy = ap.resolve_audio_policy()
    assert policy.tts_required is True
    assert policy.effective_merge_mode == "tts_mix"


def test_native_capable_model_uses_native(monkeypatch):
    import shared.audio_policy as ap
    monkeypatch.setenv("VIDEO_MODEL_VOICE", "1")
    monkeypatch.setenv("MERGE_AUDIO_MODE", "native")
    monkeypatch.setattr(ap, "active_video_supports_audio", lambda: True)
    monkeypatch.setattr(ap, "active_video_model", lambda: "native-model")
    policy = ap.resolve_audio_policy()
    assert policy.tts_required is False
    assert policy.effective_merge_mode == "native"


def test_explicit_tts_mix_wins(monkeypatch):
    import shared.audio_policy as ap
    monkeypatch.setenv("MERGE_AUDIO_MODE", "tts_mix")
    monkeypatch.setattr(ap, "active_video_supports_audio", lambda: True)
    monkeypatch.setattr(ap, "active_video_model", lambda: "native-model")
    policy = ap.resolve_audio_policy()
    assert policy.tts_required is True
    assert policy.effective_merge_mode == "tts_mix"


def test_advertised_native_but_silent_clips_fall_back_to_tts(monkeypatch):
    import shared.audio_policy as ap
    monkeypatch.setenv("VIDEO_MODEL_VOICE", "1")
    monkeypatch.setenv("MERGE_AUDIO_MODE", "native")
    monkeypatch.setattr(ap, "active_video_supports_audio", lambda: True)
    monkeypatch.setattr(ap, "active_video_model", lambda: "native-model")
    policy = ap.resolve_audio_policy(actual_audio_present=False)
    assert policy.tts_required is True
    assert policy.effective_merge_mode == "tts_mix"
