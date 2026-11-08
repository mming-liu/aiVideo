# -*- coding: utf-8 -*-
"""S23：帧 Prompt 组装层回归（人物名单 / 受惊词 / 尾帧哈希 / 角色锚定）."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _char(ident: str, *, static: str = "青年", dynamic: str = "闲适") -> dict:
    return {"identifier": ident, "static_features": static, "dynamic_features": dynamic}


def _shot_with_extra() -> tuple[dict, list[dict]]:
    characters = [_char("甲"), _char("乙", static="青年女子", dynamic="温顺")]
    entries = [
        {"name": "甲", "on_screen": True, "position": "船头",
         "pose": "盘膝坐于船板中央，持箫吹奏", "gaze_target": "画右"},
        {"name": "乙", "on_screen": True, "position": "船头右侧",
         "pose": "立于船板，撑伞侧身", "gaze_target": "甲"},
        {"name": "农人", "on_screen": True, "position": "远处田埂",
         "pose": "牵牛缓行", "gaze_target": "画右"},
    ]
    shot = {
        "index": 1,
        "ff_desc": "甲与乙在船头；远处田埂农人牵牛缓行，牛背小童。",
        "lf_desc": "甲与乙仍在船头；远处田埂农人牵牛缓行，牛背小童。",
        "frame_layouts": {"ff": entries, "lf": entries},
    }
    return shot, characters


def test_strict_constraints_include_unregistered_on_screen_character():
    """S23-1：frame_layouts on_screen 的非注册角色并入允许名单，不再硬说只允许 2 人."""
    from shared.style_prefix import add_image_strict_constraints

    shot, characters = _shot_with_extra()
    for frame_type in ("首帧", "尾帧"):
        out = add_image_strict_constraints("正文", frame_type, shot, characters)
        assert "农人" in out, (frame_type, out)
        assert "禁止出现任何其他人物" not in out, (frame_type, out)
        assert "描述中明确提到的人物" in out, (frame_type, out)
        assert "包括随行、远景等伴随人物" in out, (frame_type, out)


def test_registered_only_still_uses_closed_person_count():
    """S23-1 反向对照：只有注册角色时仍保留原“总共 N 人”封闭名单."""
    from shared.style_prefix import add_image_strict_constraints

    characters = [_char("甲"), _char("乙")]
    entries = [
        {"name": "甲", "on_screen": True, "position": "船头", "pose": "坐着", "gaze_target": "画右"},
        {"name": "乙", "on_screen": True, "position": "船头", "pose": "站着", "gaze_target": "甲"},
    ]
    shot = {
        "index": 1,
        "ff_desc": "甲与乙在船头。",
        "lf_desc": "甲与乙仍在船头。",
        "frame_layouts": {"ff": entries, "lf": entries},
    }
    out = add_image_strict_constraints("正文", "首帧", shot, characters)
    assert "总共2人" in out, out
    assert "农人" not in out, out


def test_shock_reaction_stripped_from_non_violent_first_and_last_frames():
    """S23-2：无威胁/非凶案镜的首尾帧都剥离“六神无主/牙齿打颤”."""
    from shared.vimax_bridge import _inject_frame_spec

    characters = [
        _char("乙", static="十八九岁女子",
              dynamic="娇媚柔顺，遇血案则六神无主、牙齿打颤"),
    ]
    entries = [
        {"name": "乙", "on_screen": True, "position": "船板",
         "pose": "立于船板", "gaze_target": "画右"},
    ]
    shot = {
        "index": 1,
        "ff_desc": "乙立于船板。",
        "lf_desc": "乙仍立于船板。",
        "frame_layouts": {"ff": entries, "lf": entries},
    }
    for frame_kind, desc in (("first_frame", shot["ff_desc"]),
                             ("last_frame", shot["lf_desc"])):
        out = _inject_frame_spec(desc, shot, characters, frame_kind)
        assert "六神无主" not in out, (frame_kind, out)
        assert "牙齿打颤" not in out, (frame_kind, out)
        assert "娇媚柔顺" in out, (frame_kind, out)


def test_violent_last_frame_keeps_action_result():
    """S23-2 反向对照：凶案尾帧（武器/惰性状态）不得误删动作结果."""
    from shared.vimax_bridge import _inject_frame_spec

    characters = [
        _char("乙", static="十八九岁女子", dynamic="娇媚柔顺，牙齿打颤"),
    ]
    entries = [
        {"name": "乙", "on_screen": True, "position": "船板",
         "pose": "站在船板", "gaze_target": "画右"},
    ]
    shot = {
        "index": 1,
        "ff_desc": "乙立于船板。",
        "lf_desc": "乙持匕首站在船板，匕首寒光凛冽。",
        "frame_layouts": {"ff": entries, "lf": entries},
    }
    out = _inject_frame_spec(shot["lf_desc"], shot, characters, "last_frame")
    assert "牙齿打颤" in out, out


def test_action_result_words_cover_shock_phrases():
    from shared.content_safety import strip_action_result
    assert strip_action_result("娇媚柔顺，遇血案则六神无主、牙齿打颤") == "娇媚柔顺"


def test_fixed_camera_last_frame_hash_invalidates_only_last_frame():
    """S23-3：固定机位尾帧策略只改尾帧哈希；首帧哈希不受影响.

    2026-11-08（WP2-5）：摘要现在只吃**本帧**用到的描述，首帧不再因为改了
    尾帧描述而失效；因此这里改为直接比较"固定机位 vs 横移"的两种帧哈希，
    不再拿 frame_kind 缺省的旧口径当基准。
    """
    from shared.vimax_bridge import _get_frame_content_hash

    shot = {
        "camera_movement": "固定机位",
        "main_subject": "甲",
        "location": "河边",
        "ff_desc": "甲在船头。",
        "lf_desc": "船行至开阔水面。",
    }
    moving = {**shot, "camera_movement": "镜头横移"}

    # 固定机位策略只进尾帧摘要
    assert (_get_frame_content_hash(shot, frame_kind="first_frame")
            == _get_frame_content_hash(moving, frame_kind="first_frame"))
    assert (_get_frame_content_hash(shot, frame_kind="last_frame")
            != _get_frame_content_hash(moving, frame_kind="last_frame"))

    # 首帧摘要不吃尾帧描述（改了尾帧不该废掉首帧）
    assert (_get_frame_content_hash({**shot, "lf_desc": "另一段结尾"}, frame_kind="first_frame")
            == _get_frame_content_hash(shot, frame_kind="first_frame"))
    assert (_get_frame_content_hash({**shot, "lf_desc": "另一段结尾"}, frame_kind="last_frame")
            != _get_frame_content_hash(shot, frame_kind="last_frame"))


def test_char_anchor_deduplicates_pose_and_includes_unregistered_on_screen():
    """S23-4：spec pose 不重复拼两遍，pose/position 有分隔，非注册角色进锚定块."""
    from shared.vimax_bridge import _extract_char_layout

    characters = [_char("甲")]
    entries = [
        {"name": "甲", "on_screen": True, "position": "船头",
         "pose": "盘膝坐于船板中央，持箫吹奏", "gaze_target": "画右"},
        {"name": "农人", "on_screen": True, "position": "远处田埂",
         "pose": "牵牛缓行", "gaze_target": "画右"},
    ]
    shot = {
        "index": 1,
        "ff_desc": "甲盘膝坐于船板中央，持箫吹奏；远处田埂农人牵牛缓行。",
        "frame_layouts": {"ff": entries},
    }
    out = _extract_char_layout(
        shot, characters, role_scan_text=shot["ff_desc"], frame_kind="first_frame")
    assert "农人" in out, out
    assert out.count("盘膝坐于船板中央") == 1, out
    assert "持箫吹奏船头" not in out, out
    assert "，位于船头" in out, out
