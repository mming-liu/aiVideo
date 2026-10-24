# -*- coding: utf-8 -*-
"""P1b narrow：逐字净缩短、独立载体证据、范围守卫与重验入口（离线）."""
import copy
import json
from pathlib import Path

import pytest

from shared import content_verification as cv
from shared import presentation_contract as pc
from step01_script_gen import outline as o
from step01_script_gen import plan_content_repair as pcr
from tests.shared.test_fact_split import _view


NOVEL = "甲留在门外。直到天黑。乙走进屋。甲留在门外直到天黑。"


def _card_view():
    v = copy.deepcopy(_view())
    v["mode"] = pc.MODE_HYBRID
    v["event_binding"] = "declared"
    v["narration_units"] = []
    for _b in v["beats"]:
        _b["voice_ref"] = None
    v["screen_cards"] = [{
        "card_id": "c01", "beat_uid": "b01", "template": "character",
        "text": "甲留在门外。乙走进屋。", "placement": "info_panel"}]
    v["frozen_facts"] = [{"uid": "f01", "claim": "甲留在门外",
                          "evidence": "甲留在门外。乙走进屋。"}]
    v["fact_bindings"] = [{"fact_uid": "f01", "carriers": [
        {"kind": "screen_card", "uid": "c01"}]}]
    return v


def _voice_view(*, text="甲留在门外。直到天黑。乙走进屋。", hook=False):
    v = copy.deepcopy(_view())
    v["narration_units"] = [{"uid": "n01", "text": text,
                             "source": {"text": text}}]
    v["frozen_facts"] = [{"uid": "f01", "claim": "甲留在门外直到天黑",
                          "evidence": text}]
    v["fact_bindings"] = [{"fact_uid": "f01", "carriers": [
        {"kind": "narration", "uid": "n01"}]}]
    v["beats"] = [
        {"uid": "b01", "event_uids": [], "text": "甲留在门外", "purpose": "",
         "evidence": "", "source": {}, "voice_ref": {
             "kind": "narration", "uid": "n01"}},
        # 未绑定该事实的节拍：即使它含被删文字，也不是合法载体证据
        {"uid": "b02", "event_uids": [], "text": "直到天黑", "purpose": "",
         "evidence": "", "source": {}, "voice_ref": None},
    ]
    v["hook_bindings"] = ({"spoken_uids": ["n01"], "carriers": []}
                          if hook else {"spoken_uids": [], "carriers": []})
    return v


def _slot(view, code, uid):
    _found = pcr.content_slots(view, [{"code": code, "uid": uid,
                                       "kind": "verification",
                                       "path": "x"}])
    assert _found["slots"], _found
    return _found["slots"][0]


def _apply(view, slot, action, text, scope, *, novel=NOVEL, accept=None):
    _ans = {"schema": pcr.SLOT_ANSWER_SCHEMA, "answers": [
        {"slot_id": slot["slot_id"], "action": action, "text": text}]}
    _delta, _dp = pcr.compile_delta(view, [slot], _ans, novel_text=novel)
    if _delta is None:
        return None, (_dp, None)
    _cand, _p, _rk = pc.apply_delta_view(
        view, _delta, editable_scope=scope, accept=accept or (lambda _v: []))
    return _cand, (_dp + _p, _rk)


# ---- 合法收窄 ----

def test_card_narrow_shortens_only_text_and_keeps_other_fields():
    v = _card_view()
    slot = _slot(v, "presentation_reading_budget_exceeded", "c01")
    assert slot["kind"] == "card_text"
    assert slot["affected_facts"] == ["f01"]
    assert slot["original_text"] == "甲留在门外。乙走进屋。"
    assert slot["required_content_manifest"][0]["fact_uid"] == "f01"
    scope = pcr.build_editable_scope(v, [slot], answer={
        "answers": [{"slot_id": slot["slot_id"], "action": pcr.ACT_NARROW_CARD,
                     "text": "甲留在门外。"}]})
    assert scope["allowed_narrow_cards"] == [
        {"card_id": "c01", "original_text": "甲留在门外。乙走进屋。"}]
    cand, (_p, _rk) = _apply(v, slot, pcr.ACT_NARROW_CARD, "甲留在门外。", scope)
    assert _rk["kind"] == "applied", (_rk, _p)
    assert cand["screen_cards"][0]["text"] == "甲留在门外。"
    for _k in ("card_id", "beat_uid", "template", "placement"):
        assert cand["screen_cards"][0][_k] == v["screen_cards"][0][_k]


def test_voice_narrow_shortens_text_and_keeps_source_and_uid():
    v = _voice_view()
    slot = _slot(v, "verify_voice_text_mismatch", "n01")
    assert slot["kind"] == "voice_text"
    assert slot["affected_facts"] == ["f01"]
    assert slot["unit_kind"] == "narration"
    scope = pcr.build_editable_scope(v, [slot], answer={
        "answers": [{"slot_id": slot["slot_id"], "action": pcr.ACT_NARROW_VOICE,
                     "text": "甲留在门外。直到天黑。"}]})
    assert scope["allowed_narrow_voices"] == [
        {"uid": "n01", "kind": "narration",
         "original_text": "甲留在门外。直到天黑。乙走进屋。"}]
    cand, (_p, _rk) = _apply(v, slot, pcr.ACT_NARROW_VOICE,
                             "甲留在门外。直到天黑。", scope)
    assert _rk["kind"] == "applied", (_rk, _p)
    assert cand["narration_units"][0]["uid"] == "n01"
    assert cand["narration_units"][0]["text"] == "甲留在门外。直到天黑。"
    assert cand["narration_units"][0]["source"] == v["narration_units"][0]["source"]
    assert cand["fact_bindings"][0]["carriers"] == [
        {"kind": "narration", "uid": "n01"}]


# ---- 拒收 ----

def test_narrow_removing_an_explicit_component_is_unpreserved_even_if_unbound_text_has_it():
    v = _voice_view()
    slot = _slot(v, "verify_voice_text_mismatch", "n01")
    # 显式分量：被删的限定必须由目标或**已绑定**载体单独承担；b02 未绑定，不算证据。
    slot["required_content_manifest"][0]["obligations"][0]["components"] = [
        {"component_id": "time", "text": "直到天黑", "carrier_uid": "n01"}]
    scope = pcr.build_editable_scope(v, [slot], answer={
        "answers": [{"slot_id": slot["slot_id"], "action": pcr.ACT_NARROW_VOICE,
                     "text": "甲留在门外。"}]})
    cand, (_p, _rk) = _apply(v, slot, pcr.ACT_NARROW_VOICE,
                             "甲留在门外。", scope)
    assert cand is None and _rk is None
    assert [x["code"] for x in _p] == ["repair_narrow_unpreserved"], _p


def test_narrow_must_be_strictly_shorter_verbatim_and_in_the_novel():
    v = _voice_view()
    slot = _slot(v, "verify_voice_text_mismatch", "n01")
    _full = "甲留在门外。直到天黑。乙走进屋。"
    _same = pcr._narrow_problems(slot, _full, original=_full, kind="narration",
                                 novel_text=NOVEL)
    assert [x["code"] for x in _same] == ["repair_narrow_not_shortened"]
    _foreign = pcr._narrow_problems(slot, "完全不同。", original=_full,
                                    kind="narration", novel_text=NOVEL)
    assert [x["code"] for x in _foreign] == ["repair_narrow_invalid"]
    _bad = pcr._narrow_problems(slot, "甲留在门外。直到天黑。", original=_full,
                                kind="narration", novel_text=NOVEL)
    assert _bad == []


def test_hook_bound_target_is_rejected_before_any_acceptance():
    v = _voice_view(hook=True)
    slot = _slot(v, "verify_voice_text_mismatch", "n01")
    assert slot["affects_hook"] is True
    scope = pcr.build_editable_scope(v, [slot])
    _accepted = []
    cand, (_p, _rk) = _apply(
        v, slot, pcr.ACT_NARROW_VOICE, "甲留在门外。直到天黑。", scope,
        accept=lambda _v: (_accepted.append(_v), [])[1])
    assert cand is None and _rk is None
    assert [x["code"] for x in _p] == ["repair_narrow_hook_bound"], _p
    assert _accepted == []


def test_scope_rejects_changing_voice_source_or_card_non_text_fields():
    v = _voice_view()
    slot = _slot(v, "verify_voice_text_mismatch", "n01")
    scope = pcr.build_editable_scope(v, [slot])
    _bad = copy.deepcopy(v)
    _bad["narration_units"][0]["text"] = "甲留在门外直到天黑。"
    _bad["narration_units"][0]["source"] = {"text": "篡改出处"}
    codes = [x["code"] for x in pc.scope_violations(v, _bad, scope)]
    assert "scope_voice_unit_modified" in codes, codes

    _cv = _card_view()
    _cslot = _slot(_cv, "presentation_reading_budget_exceeded", "c01")
    _cscope = pcr.build_editable_scope(_cv, [_cslot])
    _badc = copy.deepcopy(_cv)
    _badc["screen_cards"][0]["text"] = "甲留在门外。"
    _badc["screen_cards"][0]["placement"] = "background"
    ccodes = [x["code"] for x in pc.scope_violations(_cv, _badc, _cscope)]
    assert "scope_card_field_modified" in ccodes, ccodes


def test_manifest_lists_carriers_separately_and_never_concatenates():
    v = _card_view()
    v["fact_bindings"][0]["carriers"].append({"kind": "visual", "uid": "b01"})
    slot = _slot(v, "presentation_reading_budget_exceeded", "c01")
    _m = slot["required_content_manifest"][0]
    assert [c["uid"] for c in _m["other_carriers"]] == ["b01"]
    assert _m["other_carriers"][0]["text"] == v["beats"][0]["text"]
    # 每个独立载体单独成项；没有跨载体拼成一个字符串的字段。
    assert "text_joined" not in _m["other_carriers"][0]


def test_narrow_voice_answer_requires_text_and_forbids_target_uid():
    v = _voice_view()
    slot = _slot(v, "verify_voice_text_mismatch", "n01")
    _ans = {"schema": pcr.SLOT_ANSWER_SCHEMA, "answers": [
        {"slot_id": slot["slot_id"], "action": pcr.ACT_NARROW_VOICE,
         "target_uid": "n01"}]}
    _codes = {p["code"] for p in pcr.answer_problems(_ans, [slot])}
    assert "repair_answer_field_not_applicable" in _codes
    _ans2 = {"schema": pcr.SLOT_ANSWER_SCHEMA, "answers": [
        {"slot_id": slot["slot_id"], "action": pcr.ACT_NARROW_VOICE}]}
    assert "repair_answer_field_missing" in {
        p["code"] for p in pcr.answer_problems(_ans2, [slot])}
    _schema = pcr.answer_json_schema([slot])["schema"]
    _props = _schema["properties"]["answers"]["items"]["anyOf"][0]["properties"]
    assert pcr.ACT_NARROW_VOICE in _props["action"]["enum"]


def test_actual_source_problem_without_uid_resolves_to_narration_slot():
    """生产入口的真实问题不带 uid，但带显式 `narration_lines[索引]`；槽位按索引解析."""
    _s = json.loads((Path(__file__).resolve().parents[2] / "tools" / "samples"
                     / "p1b_source_defect.json").read_text(encoding="utf-8"))
    _plan, _novel = _s["plan"], _s["novel"]
    _v, _err = pc.view_from_plan(_plan)
    assert _v is not None and not _err, _err
    _probs = o.plan_acceptance_problems(_plan, _novel)
    _target = [p for p in _probs
               if p.get("code") == "source_narration_not_in_novel"]
    assert _target and not _target[0].get("uid"), _target
    _found = pcr.content_slots(_v, _target)
    assert _found["slots"], _found
    _slot = _found["slots"][0]
    assert _slot["unit_uid"] == "n01"
    assert _slot["affected_facts"] == ["f01"]
    _scope = pcr.build_editable_scope(_v, [_slot])
    _ans = {"schema": pcr.SLOT_ANSWER_SCHEMA, "answers": [
        {"slot_id": _slot["slot_id"], "action": pcr.ACT_NARROW_VOICE,
         "text": _s["expected"]["answer_text"]}]}
    _d, _dp = pcr.compile_delta(_v, [_slot], _ans, novel_text=_novel)
    assert _d is not None and not _dp, _dp
    _cand, _cp, _rk = pc.apply_delta_view(
        _v, _d, editable_scope=_scope, accept=lambda _v: [])
    assert _rk["kind"] == "applied" and not _cp, (_rk, _cp)
    _cand_plan = pc.plan_from_view(_cand)
    assert _cand_plan["narration_lines"][0]["text"] == _s["expected"]["answer_text"]
    assert [p.get("code") for p in o.plan_acceptance_problems(_cand_plan, _novel)] \
        == ["verify_record_missing"]
