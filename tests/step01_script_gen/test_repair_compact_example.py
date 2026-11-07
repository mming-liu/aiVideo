# -*- coding: utf-8 -*-
"""S03 回归：compact 回答示例的占位值必须**类型正确**（2026-11-07 实跑教训）."""
import json

from step01_script_gen import main as m


def _example(allowed):
    shot = {"index": 7, "prompt": "真实正文", "ff_desc": "真实首帧",
            "keyframes": [{"desc": "x"}],
            "frame_layouts": {"kfs": [[{"name": "甲"}]]}}
    return json.loads(m._repair_answer_example(
        {"shot_7": shot}, ["shot_7"], {"shot_7": allowed}, compact=True))


def test_keyframes_never_appears_as_a_string_update():
    rep = _example({"keyframes", "frame_layouts.kfs"})["shot_replacements"][0]
    paths = [u["field_path"] for u in rep.get("updates") or []]
    assert "keyframes" not in paths
    # 结构字段只在成对补帧形状里出现
    assert rep.get("keyframe_additions")


def test_frame_layouts_placeholder_is_a_list_not_a_string():
    rep = _example({"frame_layouts.kfs"})["shot_replacements"][0]
    for u in rep.get("updates") or []:
        if str(u["field_path"]).startswith("frame_layouts"):
            assert isinstance(u["value"], list)


def test_compact_example_carries_no_real_field_content():
    text = json.dumps(_example({"prompt", "ff_desc"}), ensure_ascii=False)
    assert "真实正文" not in text and "真实首帧" not in text
