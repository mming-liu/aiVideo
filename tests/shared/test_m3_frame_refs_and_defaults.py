# -*- coding: utf-8 -*-
"""M3.1/M3.2/M3.4：尾帧参考说明、终态前缀、默认帧集合."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def test_first_frame_as_last_frame_ref_does_not_lock_pose():
    from shared.ref_compose import describe_ref, build_ref_manifest
    path = "output/x/vimax_working/shots/1/first_frame.png"
    label = describe_ref(path, consumer_kind="last_frame")
    assert "站位以它为准" not in label
    assert "必须按本尾帧描述发生演变" in label
    manifest = build_ref_manifest([path], consumer_kind="last_frame")
    assert "必须按本尾帧描述发生演变" in manifest


def test_first_frame_default_label_is_unchanged():
    from shared.ref_compose import describe_ref
    path = "output/x/vimax_working/shots/1/first_frame.png"
    assert "动作起点与人物站位以它为准" in describe_ref(path)


def test_last_frame_prompt_prefixes_terminal_state():
    from shared.vimax_bridge import compose_frame_prompt
    shot = {
        "index": 1, "scene": "岸边", "location": "岸边",
        "ff_desc": "人物站立持箫", "lf_desc": "人物抵唇吹奏",
        "camera_movement": "固定机位",
        "frame_layouts": {"ff": [], "lf": [], "kfs": []},
        "keyframes": [],
    }
    prompt = compose_frame_prompt(shot, [], "last_frame")
    assert "【尾帧终态（须与首帧起始态区分）】" in prompt
    assert "人物抵唇吹奏" in prompt


def test_default_frames_ff_lf_when_model_does_not_use_kf():
    from step02a_frame_gen.main import _default_frames
    assert _default_frames(None, False) == {"ff", "lf"}
    assert _default_frames(None, True) is None
