"""G01 fact projection tests (anonymous fixtures, no external calls)."""
from __future__ import annotations

from shared.prompt_facts import audit_prompt_against_projection, project_frame_facts


def _shot():
    ff = [
        {"name": "甲", "on_screen": True, "position": "车板中央", "pose": "坐在车板上",
         "props": ["短杖"], "states": ["平静"],
         "support": {"id": "cart_1", "kind": "马车", "surface": "车板"}},
        {"name": "乙", "on_screen": True, "position": "台面右侧", "pose": "站在平台上",
         "props": ["伞"]},
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


def test_video_layout_uses_projection_not_ff_desc_names():
    from step02_video_gen.prompt_builder import _build_character_layout_block

    shot = {
        "index": 1, "ff_desc": "甲坐在车板上，乙在远处岸边。", "main_subject": "甲",
        "frame_layouts": {"ff": [
            {"name": "甲", "on_screen": True, "position": "车板中央", "pose": "坐在车板上",
             "support": {"id": "cart_1", "kind": "马车", "surface": "车板"}},
        ]},
    }
    block = _build_character_layout_block(shot, _chars())
    assert "甲" in block
    assert "乙" not in block
    assert "马车 cart_1 车板" in block


def test_qc_expected_characters_use_projection_not_chapter_bank():
    from shared.image_qc import build_qc_prompt

    shot = {
        "index": 1, "location": "湖湾", "scene": "湖湾",
        "main_subject": "甲", "ff_desc": "甲站在车板上。",
        "frame_layouts": {"ff": [
            {"name": "甲", "on_screen": True, "position": "车板", "pose": "站在车板上"},
        ]},
    }
    prompt = build_qc_prompt(shot, "first_frame", _chars())
    line = next(x for x in prompt.splitlines() if x.startswith("应出现人物："))
    assert "甲" in line
    assert "乙" not in line


def test_qc_claim_checks_include_explicit_support_surface():
    from shared.image_qc import build_claim_checks_spec

    qs = build_claim_checks_spec(_shot(), "first_frame")
    questions = [q["q"] for q in qs]
    assert any("甲位于马车的车板上" in q for q in questions)
    assert all(q.get("key") for q in qs if "位于马车的车板上" in q["q"])


def test_qc_all_offscreen_spec_is_empty_not_chapter_bank():
    from shared.image_qc import build_qc_prompt

    shot = {
        "index": 1, "location": "湖湾", "scene": "湖湾",
        "main_subject": "甲", "ff_desc": "湖面空镜。",
        "frame_layouts": {"ff": [
            {"name": "甲", "on_screen": False, "position": "岸上", "pose": "站着"},
        ]},
    }
    prompt = build_qc_prompt(shot, "first_frame", _chars())
    line = next(x for x in prompt.splitlines() if x.startswith("应出现人物："))
    assert line.strip() == "应出现人物：无"


def test_video_projection_aggregates_frames_and_omits_never_seen():
    from shared.prompt_facts import project_video_facts

    shot = _shot()
    shot["frame_layouts"]["lf"] = [
        {"name": "甲", "on_screen": True, "position": "车板", "pose": "坐着"},
    ]
    p = project_video_facts(shot, _chars())
    assert {e["name"] for e in p["entities"]} == {"甲", "乙"}
    assert p["omitted"] == [{"name": "丙", "reason": "off_screen"}]
    assert p["support_contract"]["explicit"] is False


def test_projection_without_spec_is_explicit_gap_not_guess():
    p = project_frame_facts({"index": 2, "ff_desc": "甲站在岸边"}, "first_frame", _chars())
    assert p["entities"] == []
    assert {o["name"] for o in p["omitted"]} == {"甲", "乙", "丙"}
