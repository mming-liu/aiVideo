# -*- coding: utf-8 -*-
"""S02 verification: frame contract inspection and fact projection."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.frame_spec import (  # noqa: E402
    get_frame_spec,
    inspect_frame_spec,
    spec_roster_or_none,
)
from shared.prompt_facts import (  # noqa: E402
    audit_prompt_against_projection,
    project_frame_facts,
    project_video_facts,
)


def test_inspect_frame_spec_distinguishes_all_offscreen_from_missing_and_invalid():
    all_off = {
        "frame_layouts": {
            "ff": [{"name": "角色甲", "on_screen": False, "position": "画外"}],
        }
    }
    res_off = inspect_frame_spec(all_off, "first_frame")
    assert res_off["source"] == "frame_layouts"
    assert res_off["status"] == "valid"
    assert len(res_off["entries"]) == 1
    assert res_off["issues"] == []
    assert spec_roster_or_none(all_off, "first_frame") == set()
    assert spec_roster_or_none(all_off, "ff") == set()

    empty_list = {"frame_layouts": {"ff": []}}
    res_empty = inspect_frame_spec(empty_list, "first_frame")
    assert res_empty["source"] == "frame_layouts"
    assert res_empty["status"] == "missing"
    assert spec_roster_or_none(empty_list, "first_frame") is None

    bad = {
        "frame_layouts": {
            "ff": [
                {"name": "角色甲", "on_screen": True},
                {"name": "角色甲", "on_screen": False},
                {"name": ""},
                "not_a_dict",
            ],
            "kfs": [[{"name": "角色乙", "on_screen": True}]],
        }
    }
    res_bad = inspect_frame_spec(bad, "first_frame")
    codes = {i["code"] for i in res_bad["issues"]}
    assert res_bad["status"] == "invalid"
    assert {"conflicting_entity_on_screen", "blank_entity_name", "invalid_spec_entry_type"} <= codes

    kf_oob = inspect_frame_spec(bad, "keyframe", kf_index=5)
    assert kf_oob["status"] == "invalid"
    assert kf_oob["issues"][0]["code"] == "kf_index_out_of_range"
    assert get_frame_spec(bad, "keyframe", kf_index=0)[0]["name"] == "角色乙"


def test_inspect_frame_spec_legacy_char_layout_fallback():
    legacy = {"char_layout": [{"name": "角色甲", "position": "画左"}]}
    res = inspect_frame_spec(legacy, "first_frame")
    assert res["source"] == "char_layout"
    assert res["status"] == "valid"
    assert res["entries"][0]["name"] == "角色甲"
    assert spec_roster_or_none(legacy, "first_frame") is None


def test_project_frame_facts_states_aliases_and_nullables():
    chars = [
        {
            "identifier": "角色甲",
            "aliases": ["掌柜", "老周"],
            "static_features": "灰布长衫",
            "dynamic_features": None,
        },
        {
            "identifier": "角色乙",
            "aliases": ["伙计", "老周"],
            "static_features": None,
            "dynamic_features": "左臂缠纱",
        },
    ]
    shot = {
        "keyframes": [{}],
        "frame_layouts": {
            "ff": [
                {
                    "name": "掌柜",
                    "on_screen": True,
                    "position": "柜台后",
                    "pose": "低头验货",
                    "props": ["铜秤"],
                    "support": {"id": "木柜台", "kind": "furniture", "surface": "台面"},
                    "states": [],
                }
            ],
            "lf": [
                {
                    "name": "角色乙",
                    "on_screen": True,
                    "position": "门口",
                    "pose": "持灯",
                    "props": None,
                }
            ],
            "kfs": [[{"name": "老周", "on_screen": True, "states": ["心口暗红"]}]],
        },
    }

    ff = project_frame_facts(shot, "first_frame", chars)
    assert ff["spec_source"] == "frame_layouts"
    assert ff["spec_status"] == "valid"
    assert len(ff["entities"]) == 1
    e0 = ff["entities"][0]
    assert e0["name"] == "角色甲"
    assert e0["spec_name"] == "掌柜"
    assert e0["character_id"] == "角色甲"
    assert e0["identity"] == "灰布长衫"
    assert e0["current"]["states"] == []
    assert e0["sources"]["states"] == "frame_layouts.states"
    assert ff["state_contract"]["explicit"] is True
    assert ff["support_contract"]["explicit"] is True
    assert [o["name"] for o in ff["omitted"]] == ["角色乙"]
    # Shared ambiguous alias ("老周") in prompt does not trigger false off-screen hit;
    # explicit canonical/unique alias ("角色乙" or "伙计") does trigger off-screen hit.
    assert audit_prompt_against_projection("掌柜与老周同框", ff, chars) == []
    assert audit_prompt_against_projection("掌柜与伙计同框", ff, chars)[0]["entity"] == "角色乙"

    lf = project_frame_facts(shot, "last_frame", chars)
    assert lf["spec_status"] == "valid"
    assert lf["entities"][0]["current"]["states"] is None
    assert lf["entities"][0]["sources"]["states"] == "missing"
    assert lf["state_contract"]["explicit"] is False
    assert lf["support_contract"]["explicit"] is False

    kf = project_frame_facts(shot, "keyframe", chars, kf_index=0)
    assert kf["spec_status"] == "invalid"
    assert any(i["code"] == "ambiguous_character_alias" for i in kf["spec_issues"])
    assert kf["entities"][0]["character_id"] is None

    vid = project_video_facts(shot, chars)
    assert vid["spec_status"] == "invalid"
    assert any(i["code"] == "ambiguous_character_alias" for i in vid["spec_issues"])
