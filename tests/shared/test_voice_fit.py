# -*- coding: utf-8 -*-
"""M8.3 TTS 音色绑定与镜头窗口自适应变速（[1.0,1.20] 边界）."""
from __future__ import annotations

from shared.voice_fit import (RATE_MAX, RATE_MIN, adaptive_rate, binding_issues,
                              fit_unit)
from tts.models import ProviderCapabilities, SpeechUnit


def _caps(supports_rate=True):
    return ProviderCapabilities(provider="stub", supports_rate=supports_rate)


def _unit(**kwargs):
    base = {"unit_id": "shot-1", "kind": "dialogue", "text": "字" * 18,
            "planned_window": (0.0, 4.0), "speaker": "voice-a",
            "voice_profile_id": "profile-a"}
    base.update(kwargs)
    return SpeechUnit(**base)


def test_rate_is_one_when_window_fits():
    result = adaptive_rate(4.0, 4.0)
    assert result["status"] == "fits"
    assert result["rate"] == RATE_MIN
    assert result["overflow"] is False


def test_rate_scales_inside_one_to_one_point_two():
    assert adaptive_rate(4.4, 4.0)["rate"] == 1.1
    assert adaptive_rate(4.8, 4.0)["rate"] == RATE_MAX
    assert adaptive_rate(4.8, 4.0)["overflow"] is False


def test_rate_never_exceeds_one_point_twenty():
    result = adaptive_rate(5.0, 4.0)
    assert result["rate"] == RATE_MAX
    assert result["overflow"] is True
    assert result["status"] == "overflow"


def test_invalid_duration_is_not_adjusted():
    result = adaptive_rate(None, 4.0)
    assert result["status"] == "invalid"
    assert result["rate"] == RATE_MIN


def test_fit_unit_adjusts_rate_within_bounds():
    unit = _unit()
    fitted, plan = fit_unit(unit, caps=_caps())
    assert plan["status"] == "adjusted"
    assert 1.0 < fitted.rate <= RATE_MAX
    assert fitted.text == unit.text
    assert fitted.planned_window == unit.planned_window


def test_fit_unit_overflow_does_not_change_rate():
    unit = _unit(text="字" * 60)
    fitted, plan = fit_unit(unit, caps=_caps())
    assert plan["status"] == "overflow"
    assert fitted.rate == 1.0


def test_fit_unit_respects_explicit_rate_and_unsupported_provider():
    explicit = _unit(rate=0.92)
    fitted, plan = fit_unit(explicit, caps=_caps())
    assert plan["status"] == "explicit_rate"
    assert fitted.rate == 0.92

    unsupported = _unit()
    fitted2, plan2 = fit_unit(unsupported, caps=_caps(supports_rate=False))
    assert plan2["status"] == "unsupported"
    assert fitted2.rate == 1.0


def test_fit_unit_without_window_is_noop():
    unit = _unit(planned_window=None)
    fitted, plan = fit_unit(unit, caps=_caps())
    assert plan["status"] == "no_window"
    assert fitted is unit


def test_binding_is_one_profile_one_speaker():
    consistent = [_unit(unit_id="shot-1"), _unit(unit_id="shot-2")]
    assert binding_issues(consistent) == []

    profile_conflict = [_unit(unit_id="shot-1", speaker="a"),
                        _unit(unit_id="shot-2", speaker="b")]
    codes = {i["code"] for i in binding_issues(profile_conflict)}
    assert "voice_profile_speaker_conflict" in codes

    speaker_conflict = [_unit(unit_id="shot-1", voice_profile_id="p1"),
                        _unit(unit_id="shot-2", voice_profile_id="p2")]
    codes = {i["code"] for i in binding_issues(speaker_conflict)}
    assert "speaker_profile_conflict" in codes
