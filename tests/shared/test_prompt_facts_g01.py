"""G01 fact projection tests (anonymous fixtures, no external calls)."""
from __future__ import annotations

from shared.prompt_facts import audit_prompt_against_projection, project_frame_facts


def _shot():
    ff = [
        {"name": "甲", "on_screen": True, "position": "车板中央", "pose": "坐在车板上",
         "props": ["短杖"], "states": ["平静"],
         "support": {"id": "cart_1", "kind": "马车", "surface": "车板"}},
        {"name": "乙", "on_screen": True, "position": "台面右侧", "pose": "站在平台上",
         "props": ["伞"], "support_kind": "平台",
         "support_id": "platform_2", "support_surface": "台面"},
    ]
    return {"index": 1, "location": "湖湾", "ff_desc": "甲坐在车板上，乙站在平台上。",
            "lf_desc": "甲坐在车板上，乙站在平台上。",
            "frame_layouts": {"ff": ff, "lf": ff}}


def _chars():
    return [
        {"identifier": "甲", "static_features": "素白长衫", "dynamic_features": "持短杖"},
        {"identifier": "乙", "static_features": "青衫", "dynamic_features": "撑伞"},
        {"identifier": "丙", "static_features": "远处岸上", "dynamic_features": "牵牛"},
    ]


def test_projection_selects_on_screen_entities_and_records_omissions():
    p = project_frame_facts(_shot(), "first_frame", _chars())
    names = [e["name"] for e in p["entities"]]
    assert names == ["甲", "乙"]
    assert p["omitted"] == [{"name": "丙", "reason": "off_screen"}]
    a = p["entities"][0]
    assert a["identity"] == "素白长衫"
    assert a["current"]["support"] == {"id": "cart_1", "kind": "马车", "surface": "车板"}
    assert a["current"]["states"] == ["平静"]
    assert "characters[甲].static_features" in a["sources"]["identity"]
    assert p["state_contract"]["explicit"] is False


def test_projection_audit_catches_offscreen_name_only():
    p = project_frame_facts(_shot(), "first_frame", _chars())
    problems = audit_prompt_against_projection("甲坐在车板上，丙在岸边。", p, _chars())
    assert [x["entity"] for x in problems] == ["丙"]
    assert audit_prompt_against_projection("甲坐在车板上，乙站在平台上。", p, _chars()) == []


def test_projection_without_spec_is_explicit_gap_not_guess():
    p = project_frame_facts({"index": 2, "ff_desc": "甲站在岸边"}, "first_frame", _chars())
    assert p["entities"] == []
    assert {o["name"] for o in p["omitted"]} == {"甲", "乙", "丙"}
