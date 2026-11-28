# -*- coding: utf-8 -*-
"""Duration conversion must not shorten a planned shot (7.875 -> 8, never 7)."""
from __future__ import annotations


def test_bailian_integer_duration_rounds_not_floors():
    from _video_gen.bailian_provider import _int_duration
    assert _int_duration(7.875) == 8
    assert _int_duration(5.25) == 6
    assert _int_duration(6.5) == 7
    assert _int_duration(3.2) == 4


def test_model_cap_duration_rounds_not_floors():
    from shared.model_caps import clamp_duration, SeedanceCap
    cap = SeedanceCap((4, 12), ("480p", "720p"), True, True)
    assert clamp_duration(7.875, cap) == 8
    assert clamp_duration(5.25, cap) == 6
    assert clamp_duration(6.5, cap) == 7
