"""S27: multi-person boat composition and lite endpoint mapping (offline)."""
from __future__ import annotations

from shared.model_alias import canonical_image_model_id
from shared.vimax_bridge import _inject_boat_composition_anchor, compose_frame_prompt


def _shot():
    ff = [
        {"name": "角色A", "on_screen": True, "position": "前舱", "pose": "盘膝坐着",
         "support": {"id": "boat_x", "kind": "敞口木舟", "surface": "前舱木甲板内侧"}},
        {"name": "角色B", "on_screen": True, "position": "中舱", "pose": "站立撑伞",
         "support": {"id": "boat_x", "kind": "敞口木舟", "surface": "中舱木甲板内侧"}},
    ]
    return {"index": 1, "ff_desc": "角色A和角色B在船内。", "lf_desc": "角色A和角色B在船内。",
            "frame_layouts": {"ff": ff, "lf": ff}}


def test_multi_person_boat_composition_is_relative_and_front_loaded():
    out = _inject_boat_composition_anchor("原描述", _shot(), "first_frame")
    assert out.startswith("【画面构图】")
    assert "微俯视斜侧视角" in out
    assert "船体横向占画面宽度80%以上" in out
    assert "角色A盘膝坐着于该船前舱木甲板内侧左侧" in out
    assert "角色B站立撑伞于该船中舱木甲板内侧右侧" in out
    assert "严禁角色B踩在船外荷叶或水面中" in out


def test_single_person_boat_gets_no_composition_anchor():
    shot = _shot()
    shot["frame_layouts"]["ff"] = [shot["frame_layouts"]["ff"][0]]
    assert _inject_boat_composition_anchor("原描述", shot, "first_frame") == "原描述"


def test_lite_shorthand_maps_to_explicit_lite_endpoint():
    assert (canonical_image_model_id("doubao-seedream-5-0-lite")
            == "doubao-seedream-5-0-lite-260128")
    assert (canonical_image_model_id("doubao-seedream-5.0.lite")
            == "doubao-seedream-5-0-lite-260128")
    assert canonical_image_model_id("doubao-seedream-5-0-260128") == "doubao-seedream-5-0-260128"


def test_compose_prompt_contains_composition_anchor():
    out = compose_frame_prompt(_shot(), [{"identifier": "角色A"}, {"identifier": "角色B"}],
                               "first_frame")
    assert out.count("【画面构图】") == 1
    assert "角色B双脚及裙摆完全踩在木甲板内侧" in out
