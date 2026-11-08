"""S27: multi-person boat composition and lite endpoint mapping (offline)."""
from __future__ import annotations

from shared.model_alias import canonical_image_model_id
from shared.vimax_bridge import compose_frame_prompt


def _shot():
    ff = [
        {"name": "角色A", "on_screen": True, "position": "前舱", "pose": "盘膝坐着",
         "support": {"id": "boat_x", "kind": "敞口木舟", "surface": "前舱木甲板内侧"}},
        {"name": "角色B", "on_screen": True, "position": "中舱", "pose": "站立撑伞",
         "support": {"id": "boat_x", "kind": "敞口木舟", "surface": "中舱木甲板内侧"}},
    ]
    return {"index": 1, "ff_desc": "角色A和角色B在船内。", "lf_desc": "角色A和角色B在船内。",
            "frame_layouts": {"ff": ff, "lf": ff}}


def test_multi_person_boat_uses_support_relation_without_fixed_composition():
    """同载关系保留，但不再由代码指定微俯视/80% 船宽/左右站位."""
    out = compose_frame_prompt(_shot(), [{"identifier": "角色A"}, {"identifier": "角色B"}],
                               "first_frame")
    assert "承载关系" in out
    assert "角色A位于同一敞口木舟的前舱木甲板内侧" in out
    assert "角色B位于同一敞口木舟的中舱木甲板内侧" in out
    assert "不落入承载面外侧" in out
    for invented in ("微俯视", "80%", "【画面构图】", "【船内站位】"):
        assert invented not in out


def test_single_person_boat_gets_no_shared_support_wording():
    shot = _shot()
    shot["frame_layouts"]["ff"] = [shot["frame_layouts"]["ff"][0]]
    out = compose_frame_prompt(shot, [{"identifier": "角色A"}], "first_frame")
    assert "同一敞口木舟" not in out

def test_lite_shorthand_maps_to_explicit_lite_endpoint():
    assert (canonical_image_model_id("doubao-seedream-5-0-lite")
            == "doubao-seedream-5-0-lite-260128")
    assert (canonical_image_model_id("doubao-seedream-5.0.lite")
            == "doubao-seedream-5-0-lite-260128")
    assert canonical_image_model_id("doubao-seedream-5-0-260128") == "doubao-seedream-5-0-260128"


def test_compose_prompt_has_single_support_relation_block():
    out = compose_frame_prompt(_shot(), [{"identifier": "角色A"}, {"identifier": "角色B"}],
                               "first_frame")
    assert out.count("承载关系") == 1
    assert "角色B双脚及裙摆完全踩在木甲板内侧" not in out
