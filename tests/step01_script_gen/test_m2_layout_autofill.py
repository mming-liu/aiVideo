# -*- coding: utf-8 -*-
"""M2.1：ff/lf/kfs 在场角色布局确定性补齐."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _script():
    return {
        "characters": [{"identifier": "阿甲"}, {"identifier": "阿乙"}],
        "shots": [{
            "index": 1,
            "ff_desc": "阿甲画左站立，阿乙在画右",
            "lf_desc": "阿甲画右坐下",
            "frame_layouts": {
                "ff": [{"name": "阿乙", "on_screen": True, "pose": "站立",
                        "position": "画右", "props": [],
                        "support": {"id": "boat_1", "kind": "小船", "surface": "船板"},
                        "states": ["站立"]}],
                "lf": [],
                "kfs": [],
            },
            "keyframes": [{"desc": "阿甲转身，阿乙看着他"}],
        }],
    }


def test_autofill_ff_lf_kfs():
    from step01_script_gen.main import _sync_missing_onscreen_layout_entries
    script = _script()
    assert _sync_missing_onscreen_layout_entries(script) == 4
    shot = script["shots"][0]
    ff = [e["name"] for e in shot["frame_layouts"]["ff"]]
    lf = [e["name"] for e in shot["frame_layouts"]["lf"]]
    kf = [e["name"] for e in shot["frame_layouts"]["kfs"][0]]
    assert ff == ["阿乙", "阿甲"]
    assert lf == ["阿甲"]
    assert kf == ["阿甲", "阿乙"]
    jia = shot["frame_layouts"]["ff"][1]
    assert jia["support"]["id"] == "boat_1"
    assert jia["on_screen"] is True


def test_negation_context_is_not_filled():
    from step01_script_gen.main import _sync_missing_onscreen_layout_entries
    script = _script()
    script["shots"][0]["ff_desc"] = "空无一人，不见阿甲，只有阿乙在画右"
    before = len(script["shots"][0]["frame_layouts"]["ff"])
    _sync_missing_onscreen_layout_entries(script)
    assert len(script["shots"][0]["frame_layouts"]["ff"]) == before
