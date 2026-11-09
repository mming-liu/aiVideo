# -*- coding: utf-8 -*-
"""M8.1 I2V 首尾帧模式：150–220 字精简运动 prompt."""
from __future__ import annotations

from shared.i2v_motion_prompt import MAX_CHARS, MIN_CHARS, build_motion_prompt


def _shot(motion: str, **extra):
    return {"index": 3, "motion_desc": motion, "camera_movement": "镜头缓慢推近",
            "ff_desc": "甲站在门口，手垂在身侧",
            "lf_desc": "甲已走进院中，抬手推门",
            **extra}


def test_typical_prompt_length_in_range_and_keeps_motion_and_camera():
    text = build_motion_prompt(_shot(
        "他先从门槛站起身，抬头看向院中；随后迈步向前，右手抬起推门；"
        "门轴转动，光线从门缝里铺开"))
    assert MIN_CHARS <= len(text) <= MAX_CHARS
    assert "站起身" in text and "推门" in text
    assert "镜头缓慢推近" in text
    assert "首帧" in text and "末帧" in text


def test_short_motion_is_padded_with_generic_continuity_constraints():
    text = build_motion_prompt(_shot("他抬头"))
    assert MIN_CHARS <= len(text) <= MAX_CHARS
    assert "动作连续推进" in text
    assert "画面无字幕" in text


def test_long_motion_is_trimmed_at_clause_boundaries():
    long_motion = "；".join("第%d步动作描述，包含转身与抬手的连续过程" % i
                            for i in range(1, 20))
    text = build_motion_prompt(_shot(long_motion))
    assert MIN_CHARS <= len(text) <= MAX_CHARS
    assert text.count("；") == 0
    assert not text.endswith("，。")


def test_prompt_is_deterministic_and_does_not_copy_static_frame_text():
    shot = _shot("他抬头看向院中，随后迈步向前")
    first = build_motion_prompt(shot)
    second = build_motion_prompt(dict(shot))
    assert first == second
    # 首尾帧的静态外观文本不整段搬进运动 prompt。
    assert "甲站在门口，手垂在身侧" not in first
    assert "甲已走进院中，抬手推门" not in first


def test_custom_bounds_are_respected():
    text = build_motion_prompt(_shot("他抬头"), min_chars=120, max_chars=180)
    assert 120 <= len(text) <= 180


def test_invalid_bounds_are_rejected():
    try:
        build_motion_prompt(_shot("他抬头"), min_chars=220, max_chars=150)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("min_chars >= max_chars 必须报错")
