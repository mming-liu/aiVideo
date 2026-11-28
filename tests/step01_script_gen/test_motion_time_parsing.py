# -*- coding: utf-8 -*-
from __future__ import annotations

from step01_script_gen.timecode import _extract_motion_duration


def test_motion_duration_accepts_range_without_seconds_suffix():
    text = "0-2秒：仰卧饮酒；2-4秒：环颈低语；4-6望星空"
    assert _extract_motion_duration(text) == 6.0