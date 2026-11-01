# -*- coding: utf-8 -*-
from __future__ import annotations
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def test_fixed_camera_last_frame_rejects_relayout_clause():
    from shared.vimax_bridge import frame_prompt_problems
    shot = {"camera_movement": "固定机位"}
    problems = frame_prompt_problems(
        "尾帧正文。构图需按尾帧描述重新布局，可与首帧人物位置不同。", shot, "last_frame")
    assert problems and problems[0]["code"] == "fixed_camera_relayout_conflict"


def test_fixed_camera_last_frame_accepts_layout_lock():
    from shared.vimax_bridge import frame_prompt_problems
    shot = {"camera_movement": "固定机位"}
    assert frame_prompt_problems(
        "尾帧正文。构图与首帧保持一致：同一机位、同一景别，人物相对位置不变。",
        shot, "last_frame") == []


def test_moving_camera_allows_relayout():
    from shared.vimax_bridge import frame_prompt_problems
    shot = {"camera_movement": "镜头横移"}
    assert frame_prompt_problems(
        "构图需按尾帧描述重新布局，可与首帧人物位置不同。", shot, "last_frame") == []


def test_first_frame_is_not_checked():
    from shared.vimax_bridge import frame_prompt_problems
    shot = {"camera_movement": "固定机位"}
    assert frame_prompt_problems(
        "构图需按尾帧描述重新布局，可与首帧人物位置不同。", shot, "first_frame") == []
