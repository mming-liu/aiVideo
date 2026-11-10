# -*- coding: utf-8 -*-
"""R2-3：同一笔 delta **删了又引用**必须被点名拒收（R1 `n02` 形态）.

样本复用 `plan_revision_mixed_legal_sample_v1.json`，不依赖现场产物。
"""
from __future__ import annotations

import json
from pathlib import Path

from shared import presentation_contract as pc

_ROOT = Path(__file__).resolve().parent.parent.parent
_SAMPLE = json.loads(
    (_ROOT / "tests/step01_script_gen/fixtures"
     / "plan_revision_mixed_legal_sample_v1.json").read_text(encoding="utf-8"))
_VIEW, _ = pc.view_from_plan(_SAMPLE["base_plan"])
_REQ = pc.delta_request(_VIEW)
_NARR = [u["uid"] for u in _VIEW["narration_units"]]
_N01 = _NARR[0]
_N02 = _NARR[1]


def _delta(**over):
    d = {"schema": pc.DELTA_SCHEMA, "rule_version": pc.DELTA_RULE_VERSION,
         "base_plan_hash": _REQ["base_plan_hash"],
         "fact_basis_hash": _REQ["fact_basis_hash"]}
    d.update(over)
    return d


def _apply(delta):
    return pc.apply_delta_view(_VIEW, delta, mode="legacy_audio",
                               accept=lambda v: [])


def _codes(problems):
    return [p.get("code") for p in problems]


def test_delete_and_reference_same_uid_is_rejected_with_available_ids():
    beat = next(b for b in _VIEW["beats"] if (b.get("voice_ref") or {}).get("uid") == _N01)
    delta = _delta(
        delete={"narration_units": [_N01]},
        upsert={"beats": [dict(beat, voice_ref={"kind": "narration", "uid": _N01})]},
        order={"narration_units": [u for u in _NARR if u != _N01]},
        fact_bindings=[{"fact_uid": "f01",
                        "carriers": [{"kind": "narration", "uid": _N01}]}],
    )
    view, problems, result = _apply(delta)
    assert view is None
    assert result["kind"] == "illegal" and result["stage"] == "response"
    assert "delta_deleted_uid_referenced" in _codes(problems)
    reasons = " ".join(p.get("reason") or "" for p in problems)
    assert "upsert.beats[0].voice_ref" in reasons
    assert "fact_bindings[0].carriers[0]" in reasons
    assert "可用 id" in reasons and _N02 in reasons


def _minimal_view():
    return {
        "schema": pc.PRESENTATION_SCHEMA,
        "mode": pc.MODE_LEGACY,
        "event_binding": "unknown",
        "frozen_facts": [{"uid": "f01", "claim": "甲进屋", "evidence": "甲进屋。"}],
        "frozen_events": ["事件1"],
        "beats": [{"uid": "b01", "text": "甲推门进屋。", "event_uids": ["事件1"],
                   "purpose": "", "evidence": "甲进屋。", "source": {},
                   "voice_ref": {"kind": "narration", "uid": "n01"}}],
        "dialogue_units": [],
        "narration_units": [{"uid": "n01", "text": "甲进屋。",
                             "source": {"text": "甲进屋。"}}],
        "screen_cards": [],
        "fact_bindings": [{"fact_uid": "f01",
                           "carriers": [{"kind": "narration", "uid": "n01"}]}],
        "hook_bindings": {"spoken_uids": [], "carriers": []},
        "evidence_updates": [],
        "plan_basis": {"fact_uids": ["f01"]},
    }


def test_legal_upsert_of_the_same_uid_is_not_flagged():
    # 改正文应 upsert 同一 uid，而不是 delete 后再引用。
    view0 = _minimal_view()
    req = pc.delta_request(view0)
    narr = view0["narration_units"][0]
    delta = {"schema": pc.DELTA_SCHEMA, "rule_version": pc.DELTA_RULE_VERSION,
             "base_plan_hash": req["base_plan_hash"],
             "fact_basis_hash": req["fact_basis_hash"],
             "upsert": {"narration_units": [dict(narr, text=narr["text"])]},
             "order": {"narration_units": ["n01"]}}
    view, problems, result = pc.apply_delta_view(
        view0, delta, mode="legacy_audio", accept=lambda v: [])
    assert "delta_deleted_uid_referenced" not in _codes(problems)
    assert result["kind"] in ("no_change", "applied"), (result, _codes(problems))


def test_forged_voice_ref_reports_available_ids():
    beat = next(b for b in _VIEW["beats"] if (b.get("voice_ref") or {}).get("uid") == _N01)
    delta = _delta(
        upsert={"beats": [dict(beat, text=beat["text"] + "（补一笔）",
                               voice_ref={"kind": "narration", "uid": "n99"})]},
    )
    view, problems, result = _apply(delta)
    assert view is None
    assert result["kind"] == "illegal"
    assert "view_voice_ref_dangling" in _codes(problems)
    reasons = " ".join(p.get("reason") or "" for p in problems)
    assert "当前可用" in reasons and _N01 in reasons
