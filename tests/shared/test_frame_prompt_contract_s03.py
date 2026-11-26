# -*- coding: utf-8 -*-
"""S03 verification: person count, explicit state/pose/props/support projection, and contradictions."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.prompt_facts import project_frame_facts  # noqa: E402
from shared.style_prefix import add_image_strict_constraints  # noqa: E402
from shared.vimax_bridge import compose_frame_prompt  # noqa: E402


def _anon_chars():
    return [
        {
            "identifier": "角色甲",
            "aliases": ["掌柜"],
            "gender": "male",
            "age_range": "青年",
            "static_features": "方脸短须",
            "dynamic_features": "深灰布袍",
        },
        {
            "identifier": "角色乙",
            "aliases": ["姑娘"],
            "gender": "female",
            "age_range": "青年",
            "static_features": "杏眼细眉",
            "dynamic_features": "青布罗裙",
        },
    ]


def test_compose_prompt_counts_all_on_screen_even_when_prose_uses_titles():
    chars = _anon_chars()
    shot = {
        "index": 1,
        "location": "木器作坊",
        "ff_desc": "灯影下，掌柜扶着姑娘看向木案上的裂纹。",
        "lf_desc": "两人站起身。",
        "keyframes": [{"desc": "姑娘低头查看伤处。", "t": 0.5}],
        "frame_layouts": {
            "ff": [
                {
                    "name": "角色甲",
                    "on_screen": True,
                    "position": "画左",
                    "pose": "半俯身",
                    "props": ["油灯"],
                    "support": {"id": "长凳", "kind": "木凳", "surface": "凳面"},
                    "states": ["心口暗红", "左袖湿透"],
                },
                {
                    "name": "姑娘",
                    "on_screen": True,
                    "position": "画右",
                    "pose": "站立回望",
                    "props": [],
                    "support": {"id": "青砖地", "kind": "地面", "surface": "砖面"},
                    "states": ["未受伤"],
                },
            ],
            "lf": [
                {"name": "角色甲", "on_screen": False},
                {"name": "角色乙", "on_screen": True, "position": "画中", "pose": "抱拳"},
            ],
            "kfs": [
                [
                    {
                        "name": "角色甲",
                        "on_screen": True,
                        "position": "画左",
                        "pose": "单膝跪地",
                        "states": ["心口暗红"],
                    },
                    {
                        "name": "角色乙",
                        "on_screen": True,
                        "position": "画右",
                        "pose": "递出药瓶",
                        "props": ["瓷瓶"],
                    },
                ]
            ],
        },
    }

    ff_prompt = compose_frame_prompt(shot, chars, "first_frame")
    assert "总共2人" in ff_prompt
    assert "角色甲" in ff_prompt and "角色乙" in ff_prompt
    assert "心口暗红" in ff_prompt
    assert "左袖湿透" in ff_prompt
    assert "未受伤" in ff_prompt
    assert "油灯" in ff_prompt and "【道具锚定】" in ff_prompt
    assert "承载关系" in ff_prompt

    lf_prompt = compose_frame_prompt(shot, chars, "last_frame")
    assert "总共1人" in lf_prompt
    assert "角色乙" in lf_prompt

    kf_prompt = compose_frame_prompt(shot, chars, "keyframe", kf_index=0)
    assert "总共2人" in kf_prompt
    assert "心口暗红" in kf_prompt
    assert "瓷瓶" in kf_prompt


def test_all_offscreen_frame_emits_zero_person_constraint():
    chars = _anon_chars()
    shot = {
        "ff_desc": "案几上一盏残灯微微晃动。",
        "frame_layouts": {
            "ff": [{"name": "角色甲", "on_screen": False}],
        },
    }
    out = add_image_strict_constraints(shot["ff_desc"], "首帧", shot=shot, characters=chars)
    assert "总共0人" in out


def test_contradictory_states_and_props_keep_both_sides_and_flag_invalid():
    chars = _anon_chars()
    shot = {
        "ff_desc": "作坊内对峙。",
        "frame_layouts": {
            "ff": [
                {
                    "name": "角色甲",
                    "on_screen": True,
                    "pose": "徒手",
                    "props": ["铁尺"],
                    "states": ["未持铁尺", "手持铁尺"],
                }
            ]
        },
    }
    proj = project_frame_facts(shot, "first_frame", chars)
    assert proj["spec_status"] == "invalid"
    codes = {i["code"] for i in proj["spec_issues"]}
    assert "contradictory_entity_states" in codes
    assert "contradictory_entity_prop" in codes
    prompt = compose_frame_prompt(shot, chars, "first_frame")
    assert "未持铁尺" in prompt
    assert "手持铁尺" in prompt
