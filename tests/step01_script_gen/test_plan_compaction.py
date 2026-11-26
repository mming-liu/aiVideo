# -*- coding: utf-8 -*-
"""S27/S30: deterministic visual-beat compaction (no model, no chapter data)."""
from __future__ import annotations

import pytest

from tools.compact_plan_visual_beats import merge_visual_beats, speaker_map_for_view


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


def test_merge_keeps_all_event_bindings_in_order():
    view = _view()
    view["beats"][0]["event_uids"] = ["e1", "e2"]
    view["beats"][1]["event_uids"] = ["e2", "e3"]
    candidate, mapping, _meta = merge_visual_beats(view, group_size=2)
    assert candidate["beats"][0]["event_uids"] == ["e1", "e2", "e3"]


def test_merge_rejects_different_sources_instead_of_dropping_them():
    view = _view()
    view["beats"][0]["source"] = {"chapter": 1, "quote": "甲"}
    view["beats"][1]["source"] = {"chapter": 1, "quote": "乙"}
    with pytest.raises(ValueError, match="source_relation_not_mergeable"):
        merge_visual_beats(view, group_size=2)


def test_speaker_map_drops_orphan_plan_units():
    """合并后视图只剩 d1；计划里孤立的 d2 不得继续声明，否则编译拒收."""
    view = _view()
    view["dialogue_units"] = [{"uid": "d1", "text": "钥匙给我"}]
    plan = {"voice_units": [
        {"unit_uid": "d1", "speaker": "甲", "source_kind": "novel_speech"},
        {"unit_uid": "d2", "speaker": "乙", "source_kind": "novel_speech"},
    ]}
    assert speaker_map_for_view(view, plan) == {"d1": "甲"}
