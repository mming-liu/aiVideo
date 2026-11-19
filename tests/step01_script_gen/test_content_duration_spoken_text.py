# -*- coding: utf-8 -*-
"""R7：时长估算必须使用实际播出正文（前缀不占时）."""
from __future__ import annotations

from shared.dialogue import strip_narration_label
from step01_script_gen.timecode import _shot_content_duration


def test_narration_label_is_stripped():
    text = "若非如此，前几日往泰州府去时，杨公子也不会花了两百贯宝钞的高价把她买下"
    assert strip_narration_label("旁白：" + text) == text
    assert strip_narration_label("画外音：" + text) == text
    assert strip_narration_label(text) == text


def test_content_duration_ignores_label():
    text = "若非如此，前几日往泰州府去时，杨公子也不会花了两百贯宝钞的高价把她买下"
    base = {"index": 3, "duration_sec": 0.0, "motion_desc": "", "keyframes": [],
            "narration_segment": text}
    labeled = dict(base, narration_segment="旁白：" + text)
    a = _shot_content_duration(base, fps=24, shot_cap=100)
    b = _shot_content_duration(labeled, fps=24, shot_cap=100)
    assert a == b
