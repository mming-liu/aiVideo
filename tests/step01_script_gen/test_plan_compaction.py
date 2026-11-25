# -*- coding: utf-8 -*-
"""S27/S30: deterministic visual-beat compaction (no model, no chapter data)."""
from __future__ import annotations

from tools.compact_plan_visual_beats import merge_visual_beats


def _beat(uid, kind, text):
    return {"uid": uid, "event_uids": [], "text": text, "purpose": "目的 " + text,
            "evidence": text, "source": {}, "voice_ref": {"kind": kind, "uid": uid}}


def _view():
    return {
        "schema": "presentation.view.v1", "mode": "legacy_audio",
        "frozen_facts": [{"uid": "f1", "claim": "x", "evidence": "x"}],
        "frozen_events": [], "beats": [
            _beat("b1", "visual", "甲看向门口"),
            _beat("b2", "visual", "乙握紧钥匙"),
            _beat("b3", "dialogue", "甲说：钥匙给我"),
            _beat("b4", "visual", "丙离开房间"),
        ],
        "dialogue_units": [{"uid": "d1", "text": "钥匙给我"}],
        "narration_units": [], "screen_cards": [],
        "fact_bindings": [{"fact_uid": "f1", "carriers": [
            {"kind": "visual", "uid": "b1"}, {"kind": "visual", "uid": "b2"},
            {"kind": "visual", "uid": "b4"}]}],
        "hook_bindings": {"spoken_uids": [], "carriers": []},
        "evidence_updates": [], "plan_basis": {}, "carrier_capacity": 2,
    }


def test_merges_only_consecutive_visual_beats_and_remaps_bindings():
    candidate, mapping, meta = merge_visual_beats(_view(), group_size=2)
    assert [b["uid"] for b in candidate["beats"]] == ["b1", "b3", "b4"]
    assert candidate["beats"][0]["text"] == "甲看向门口；乙握紧钥匙"
    assert mapping == {"b1": "b1", "b2": "b1"}
    assert [c["uid"] for c in candidate["fact_bindings"][0]["carriers"]] == ["b1", "b4"]
    assert meta["beats_before"] == 4 and meta["beats_after"] == 3
    # 台词节拍绝不并入视觉节拍
    assert candidate["beats"][1]["voice_ref"] == {"kind": "dialogue", "uid": "b3"}


def test_single_visual_beats_are_not_merged():
    candidate, mapping, meta = merge_visual_beats(_view(), group_size=3)
    assert mapping == {}
    assert meta["beats_after"] == meta["beats_before"] == 4
