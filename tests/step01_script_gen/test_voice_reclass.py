# -*- coding: utf-8 -*-
"""P1a：声音 move / bind 的槽位、编译、守卫与投影回读（离线）.

夹具是**匿名最小样本**：一条被放进对白的叙述、或一条被放进旁白的发言；
期望只由样本与契约给出，不依赖某一章的具体人物/编号。
"""
import copy

import pytest

from shared import content_verification as cv
from shared import presentation_contract as pc
from shared.fact_metadata import LEGACY_ACCEPTANCE_FIELD, LEGACY_ACCEPTANCE_VERSION
from step01_script_gen import outline as o
from step01_script_gen import plan_content_repair as pcr


NOVEL = "甲留在门外。乙走进屋。"


def _plan(**kw) -> dict:
    _p = {
        "main_events": ["甲留在门外"],
        "climax": "甲留在门外",
        "required_facts": [],
        "causal_chain": [],
        "shot_beats": [],
        "dialogue_lines": [],
        "omittable": [],
        "hook_spoken": [],
        "time_budget": "约5秒",
        "characters": ["甲"],
        "emotional_arc": "平静",
        "key_scenes": ["门外"],
        "character_motives": ["甲：等待"],
        "end_hook": "",
        "hook_carrier": "",
        "narration_lines": [],
        LEGACY_ACCEPTANCE_FIELD: LEGACY_ACCEPTANCE_VERSION,
    }
    _p.update(kw)
    return _p


def _record(unit_uid: str, source_kind: str, text: str) -> dict:
    return {
        "schema": cv.SCHEMA_VERIFICATION, "status": "ok",
        "plan_digest": "0" * 64, "novel_digest": "x" * 64,
        "rule_version": cv.DEFAULT_RULE_VERSION,
        "facts": [], "source_support": [],
        "voice_units": [{"unit_uid": unit_uid, "verdict": "supported",
                         "source_kind": source_kind, "speaker": "",
                         "echo_text": text, "reason": ""}],
    }


def _voice_problem(uid: str = "d01") -> dict:
    return {"code": "verify_voice_not_speech", "kind": "verification", "uid": uid,
            "path": "voice_units", "reason": "来源类型与所在集合不符"}


def _view(plan):
    _v, _err = pc.view_from_plan(plan)
    assert _v is not None and not _err, _err
    return _v


def _apply(view, slot, action, scope, *, extra=None, novel=NOVEL, accept=None):
    _ans = {"schema": pcr.SLOT_ANSWER_SCHEMA, "answers": [
        dict({"slot_id": slot["slot_id"], "action": action}, **(extra or {}))]}
    _delta, _dp = pcr.compile_delta(view, [slot], _ans, novel_text=novel)
    if _delta is None:
        return None, _dp, None, _ans
    _cand, _ap, _rk = pc.apply_delta_view(
        view, _delta, editable_scope=scope,
        accept=accept or (lambda _v: []))
    return _cand, _ap, _rk, _ans


# ---- 对白→旁白 ----

def test_dialogue_to_narration_keeps_text_derives_source_and_updates_refs():
    _text = "甲留在门外。"
    _plan0 = _plan(
        shot_beats=["节拍：甲留在门外｜载体：台词1"],
        required_facts=[_text + "｜原文证据：“" + _text + "”｜承载：B1/台词1"],
        dialogue_lines=[_text])
    _v = _view(_plan0)
    _rec = _record("d01", "novel_narration", _text)
    _found = pcr.content_slots(_v, [_voice_problem("d01")],
                               verification_record=_rec)
    assert not _found["blocked"] and not _found["unexpressible"], _found
    _slot = _found["slots"][0]
    assert _slot["kind"] == "voice_kind"
    assert _slot["reclassify"] == {"uid": "d01", "from": "dialogue",
                                   "to": "narration",
                                   "to_temp_uid": pcr._voice_temp_uid("d01")}
    _scope = pcr.build_editable_scope(_v, [_slot])
    assert _scope["allowed_reclassify_voices"] == [_slot["reclassify"]]
    assert _scope["allowed_rebind_fact_uids"] == ["f01"]
    # 结构化声明同源：迁移动作只收 slot_id/action，不出现正文/目标字段。
    _sch = pcr.answer_json_schema([_slot])["schema"]
    _branch = _sch["properties"]["answers"]["items"]["anyOf"][0]
    assert _branch["required"] == ["slot_id", "action"], _branch
    assert "text" not in _branch["properties"], _branch
    _cand, _p, _rk, _ans = _apply(_v, _slot, pcr.ACT_RECLASSIFY, _scope)
    assert _rk["kind"] == "applied", (_rk, _p)
    assert _cand["dialogue_units"] == []
    assert [(u["uid"], u["text"], u["source"]) for u in _cand["narration_units"]] \
        == [("n01", _text, {"text": _text})]
    assert _cand["beats"][0]["voice_ref"] == {"kind": "narration", "uid": "n01"}
    assert _cand["fact_bindings"][0]["carriers"] == [
        {"kind": "narration", "uid": "n01"}]
    # 投影回读：正文/来源/落点/事实引用全部保持（不是只看 uid 数量）
    _plan2 = pc.plan_from_view(_cand)
    assert _plan2["dialogue_lines"] == []
    assert _plan2["narration_lines"] == [
        {"text": _text, "source": _text, "fact_refs": [1]}]
    assert _plan2["shot_beats"] == ["节拍：甲留在门外｜载体：旁白1"]
    assert _plan2["required_facts"] == [
        _text + "｜原文证据：“" + _text + "”｜承载：B1/旁白1"]
    _back, _err = pc.view_from_plan(_plan2)
    assert _err == []
    assert _back["narration_units"] == _cand["narration_units"]
    assert _back["beats"][0]["voice_ref"] == _cand["beats"][0]["voice_ref"]


def test_dialogue_to_narration_rejects_when_text_is_not_verbatim_in_novel():
    _text = "甲留在门外。"
    _plan0 = _plan(
        shot_beats=["节拍：甲留在门外｜载体：台词1"],
        required_facts=[_text + "｜原文证据：“" + _text + "”｜承载：B1/台词1"],
        dialogue_lines=[_text])
    _v = _view(_plan0)
    _slot = pcr.content_slots(_v, [_voice_problem("d01")],
                              verification_record=_record(
                                  "d01", "novel_narration", _text))["slots"][0]
    _scope = pcr.build_editable_scope(_v, [_slot])
    _cand, _p, _rk, _ans = _apply(_v, _slot, pcr.ACT_RECLASSIFY, _scope,
                                 novel="完全不同的原文。")
    assert _cand is None and _rk is None
    assert [x["code"] for x in _p] == ["repair_voice_source_not_found"]


# ---- 旁白→对白 ----

def test_narration_to_dialogue_keeps_text_and_drops_legacy_source():
    _text = "甲说：留下。"
    _plan0 = _plan(
        shot_beats=["节拍：甲说话｜载体：旁白1"],
        required_facts=[_text + "｜原文证据：“" + _text + "”｜承载：B1/旁白1"],
        narration_lines=[{"text": _text, "source": _text, "fact_refs": [1]}])
    _v = _view(_plan0)
    _slot = pcr.content_slots(_v, [_voice_problem("n01")],
                              verification_record=_record(
                                  "n01", "novel_speech", _text))["slots"][0]
    _scope = pcr.build_editable_scope(_v, [_slot])
    _cand, _p, _rk, _ans = _apply(_v, _slot, pcr.ACT_RECLASSIFY, _scope)
    assert _rk["kind"] == "applied", (_rk, _p)
    assert _cand["narration_units"] == []
    assert [(u["uid"], u["text"], u["source"]) for u in _cand["dialogue_units"]] \
        == [("d01", _text, {})]
    assert _cand["beats"][0]["voice_ref"] == {"kind": "dialogue", "uid": "d01"}
    _plan2 = pc.plan_from_view(_cand)
    assert _plan2["dialogue_lines"] == [_text]
    assert _plan2["narration_lines"] == []
    assert _plan2["shot_beats"] == ["节拍：甲说话｜载体：台词1"]
    assert _plan2["required_facts"] == [
        _text + "｜原文证据：“" + _text + "”｜承载：B1/台词1"]


# ---- 事实绑声音 ----

def test_bind_fact_to_existing_voice_replaces_only_the_authorized_binding():
    _text = "甲说会走。"
    _plan0 = _plan(
        shot_beats=["节拍：甲站在门口｜载体：画面", "节拍：甲开口｜载体：台词1"],
        required_facts=["甲说会走｜原文证据：“" + _text + "”｜承载：B1/画面",
                        "乙走进屋｜原文证据：“乙走进屋。”｜承载：B1/画面"],
        dialogue_lines=[_text])
    _v = _view(_plan0)
    _found = pcr.content_slots(_v, [{"code": "verify_fact_unsupported",
                                     "kind": "fact", "uid": "f01"}])
    _slot = _found["slots"][0]
    assert pcr.ACT_BIND_VOICE in _slot["allowed_actions"]
    assert _slot["voice_target_uids"] == ["d01"]
    assert "d01" not in _slot["target_uids"]
    _scope = pcr.build_editable_scope(_v, [_slot])
    _cand, _p, _rk, _ans = _apply(_v, _slot, pcr.ACT_BIND_VOICE, _scope,
                                 extra={"target_uid": "d01"})
    assert _rk["kind"] == "applied", (_rk, _p)
    assert _cand["fact_bindings"][0]["carriers"] == [
        {"kind": "dialogue", "uid": "d01"}]
    assert _cand["fact_bindings"][1]["carriers"] == [
        {"kind": "visual", "uid": "b01"}]
    _plan2 = pc.plan_from_view(_cand)
    assert _plan2["required_facts"][0].endswith("｜承载：B2/台词1"), _plan2
    assert _plan2["required_facts"][1].endswith("｜承载：B1/画面"), _plan2


# ---- 拒收 ----

def test_hook_bound_voice_cannot_be_reclassified():
    _text = "甲留在门外。"
    _plan0 = _plan(
        shot_beats=["节拍：甲留在门外｜载体：台词1"],
        required_facts=[_text + "｜原文证据：“" + _text + "”｜承载：B1/台词1"],
        dialogue_lines=[_text], hook_spoken=[_text])
    _v = _view(_plan0)
    _slot = pcr.content_slots(_v, [_voice_problem("d01")],
                              verification_record=_record(
                                  "d01", "novel_narration", _text))["slots"][0]
    _scope = pcr.build_editable_scope(_v, [_slot])
    _cand, _p, _rk, _ans = _apply(_v, _slot, pcr.ACT_RECLASSIFY, _scope)
    assert _cand is None and _rk is None
    assert [x["code"] for x in _p] == ["repair_voice_hook_bound"]


def test_reclassify_answer_cannot_carry_text_or_a_foreign_target():
    _text = "甲留在门外。"
    _plan0 = _plan(
        shot_beats=["节拍：甲留在门外｜载体：台词1"],
        required_facts=[_text + "｜原文证据：“" + _text + "”｜承载：B1/台词1"],
        dialogue_lines=[_text])
    _v = _view(_plan0)
    _slot = pcr.content_slots(_v, [_voice_problem("d01")],
                              verification_record=_record(
                                  "d01", "novel_narration", _text))["slots"][0]
    _bad_text = {"schema": pcr.SLOT_ANSWER_SCHEMA, "answers": [
        {"slot_id": _slot["slot_id"], "action": pcr.ACT_RECLASSIFY,
         "text": "改写"}]}
    assert [x["code"] for x in pcr.answer_problems(_bad_text, [_slot])] == \
        ["repair_answer_field_not_applicable"]
    _bad_tgt = {"schema": pcr.SLOT_ANSWER_SCHEMA, "answers": [
        {"slot_id": _slot["slot_id"], "action": pcr.ACT_RECLASSIFY,
         "target_uid": "n01"}]}
    assert [x["code"] for x in pcr.answer_problems(_bad_tgt, [_slot])] == \
        ["repair_answer_field_not_applicable"]


def test_bind_voice_target_must_come_from_the_voice_target_set():
    _text = "甲说会走。"
    _plan0 = _plan(
        shot_beats=["节拍：甲站在门口｜载体：画面", "节拍：甲开口｜载体：台词1"],
        required_facts=["甲说会走｜原文证据：“" + _text + "”｜承载：B1/画面"],
        dialogue_lines=[_text])
    _v = _view(_plan0)
    _slot = pcr.content_slots(_v, [{"code": "verify_fact_unsupported",
                                    "kind": "fact", "uid": "f01"}])["slots"][0]
    _bad = {"schema": pcr.SLOT_ANSWER_SCHEMA, "answers": [
        {"slot_id": _slot["slot_id"], "action": pcr.ACT_BIND_VOICE,
         "target_uid": "b01"}]}
    assert [x["code"] for x in pcr.answer_problems(_bad, [_slot])] == \
        ["repair_answer_target_not_allowed"]


# ---- 范围守卫 ----

def test_scope_rejects_unauthorized_voice_edits_and_accepts_the_authorized_move():
    _text = "甲留在门外。"
    _plan0 = _plan(
        shot_beats=["节拍：甲留在门外｜载体：台词1"],
        required_facts=[_text + "｜原文证据：“" + _text + "”｜承载：B1/台词1"],
        dialogue_lines=[_text])
    _v = _view(_plan0)
    _slot = pcr.content_slots(_v, [_voice_problem("d01")],
                              verification_record=_record(
                                  "d01", "novel_narration", _text))["slots"][0]
    _scope = pcr.build_editable_scope(_v, [_slot])
    _ans = {"schema": pcr.SLOT_ANSWER_SCHEMA, "answers": [
        {"slot_id": _slot["slot_id"], "action": pcr.ACT_RECLASSIFY}]}
    _delta, _dp = pcr.compile_delta(_v, [_slot], _ans, novel_text=NOVEL)
    _cand, _p, _rk = pc.apply_delta_view(_v, _delta, editable_scope=_scope,
                                         accept=lambda _x: [])
    assert _rk["kind"] == "applied", (_rk, _p)
    # 未授权声音的正文/来源改动必须拒
    _bad = copy.deepcopy(_cand)
    _bad["narration_units"][0]["text"] = "甲留在门内。"
    _codes = [x["code"] for x in pc.scope_violations(_v, _bad, _scope)]
    assert "scope_voice_unit_modified" in _codes
    # 未授权的节拍声音归属改动必须拒
    _bad2 = copy.deepcopy(_cand)
    _bad2["beats"][0]["voice_ref"] = {"kind": "narration", "uid": "n99"}
    _codes2 = [x["code"] for x in pc.scope_violations(_v, _bad2, _scope)]
    assert "scope_beat_voice_ref_modified" in _codes2
    # 未授权事实的绑定改动必须拒（授权只给迁移关联的事实 f01）
    _bad3 = copy.deepcopy(_cand)
    _bad3["fact_bindings"][0]["carriers"] = [{"kind": "visual", "uid": "b01"}]
    _codes3 = [x["code"] for x in pc.scope_violations(_v, _bad3, _scope)]
    assert "scope_uneditable_binding_modified" in _codes3


def test_scope_allows_only_the_old_to_new_voice_ref_swap_on_the_affected_beat():
    _text = "甲留在门外。"
    _plan0 = _plan(
        shot_beats=["节拍：甲留在门外｜载体：台词1"],
        required_facts=[_text + "｜原文证据：“" + _text + "”｜承载：B1/台词1"],
        dialogue_lines=[_text])
    _v = _view(_plan0)
    _slot = pcr.content_slots(_v, [_voice_problem("d01")],
                              verification_record=_record(
                                  "d01", "novel_narration", _text))["slots"][0]
    _scope = pcr.build_editable_scope(_v, [_slot])
    _rc = _slot["reclassify"]
    _cand = copy.deepcopy(_v)
    _cand["dialogue_units"] = []
    _cand["narration_units"] = [{"uid": _rc["to_temp_uid"], "text": _text,
                                 "source": {"text": _text}}]
    _cand["beats"][0]["voice_ref"] = {"kind": "narration",
                                      "uid": _rc["to_temp_uid"]}
    _cand["fact_bindings"][0]["carriers"] = [{"kind": "narration",
                                              "uid": _rc["to_temp_uid"]}]
    assert pc.scope_violations(_v, _cand, _scope) == []


def test_scope_rejects_a_tampered_new_voice_source():
    _text = "甲留在门外。"
    _plan0 = _plan(
        shot_beats=["节拍：甲留在门外｜载体：台词1"],
        required_facts=[_text + "｜原文证据：“" + _text + "”｜承载：B1/台词1"],
        dialogue_lines=[_text])
    _v = _view(_plan0)
    _slot = pcr.content_slots(_v, [_voice_problem("d01")],
                              verification_record=_record(
                                  "d01", "novel_narration", _text))["slots"][0]
    _scope = pcr.build_editable_scope(_v, [_slot])
    _cand, _p, _rk = pc.apply_delta_view(
        _v, pcr.compile_delta(_v, [_slot], {"schema": pcr.SLOT_ANSWER_SCHEMA,
                                            "answers": [{"slot_id": _slot["slot_id"],
                                                         "action": pcr.ACT_RECLASSIFY}]},
                              novel_text=NOVEL)[0],
        editable_scope=_scope, accept=lambda _x: [])
    assert _rk["kind"] == "applied", (_rk, _p)
    _bad = copy.deepcopy(_cand)
    _bad["narration_units"][0]["source"] = {"text": "被篡改的出处"}
    _codes = [x["code"] for x in pc.scope_violations(_v, _bad, _scope)]
    assert "scope_voice_unit_modified" in _codes, _codes


def test_only_the_targeted_voice_is_moved_when_two_units_exist():
    _t1, _t2 = "甲留在门外。", "乙走进屋。"
    _plan0 = _plan(
        shot_beats=["节拍：甲留在门外｜载体：台词1", "节拍：乙走进屋｜载体：台词2"],
        required_facts=[_t1 + "｜原文证据：“" + _t1 + "”｜承载：B1/台词1",
                        _t2 + "｜原文证据：“" + _t2 + "”｜承载：B2/台词2"],
        dialogue_lines=[_t1, _t2])
    _v = _view(_plan0)
    _slot = pcr.content_slots(
        _v, [{"code": "verify_voice_not_speech", "uid": "d02",
              "path": "voice_units"}],
        verification_record=_record("d02", "novel_narration", _t2))["slots"][0]
    _scope = pcr.build_editable_scope(_v, [_slot])
    assert _scope["allowed_reclassify_voices"][0]["uid"] == "d02"
    _cand, _p, _rk, _ans = _apply(_v, _slot, pcr.ACT_RECLASSIFY, _scope)
    assert _rk["kind"] == "applied", (_rk, _p)
    assert [u["text"] for u in _cand["dialogue_units"]] == [_t1]
    assert [u["text"] for u in _cand["narration_units"]] == [_t2]
    _plan2 = pc.plan_from_view(_cand)
    assert _plan2["dialogue_lines"] == [_t1]
    assert _plan2["narration_lines"][0]["text"] == _t2
    assert _plan2["shot_beats"][0].endswith("｜载体：台词1"), _plan2["shot_beats"]
    assert _plan2["shot_beats"][1].endswith("｜载体：旁白1"), _plan2["shot_beats"]


def test_mixed_fact_and_voice_problems_both_get_slots():
    _text = "甲说会走。"
    _plan0 = _plan(
        shot_beats=["节拍：甲站在门口｜载体：画面", "节拍：甲开口｜载体：台词1"],
        required_facts=["甲说会走｜原文证据：“" + _text + "”｜承载：B1/画面"],
        dialogue_lines=[_text])
    _rec = _record("d01", "novel_narration", _text)
    _rec["plan_digest"] = cv.plan_digest_of(_plan0)
    import types
    _ver = types.SimpleNamespace(records={_rec["plan_digest"]: _rec})
    _ok, _why, _ctx = o._content_slots_applicable(
        _plan0,
        [{"code": "verify_fact_unsupported", "uid": "f01", "path": "facts[f01]"},
         {"code": "verify_voice_not_speech", "uid": "d01", "path": "voice_units"}],
        verification=_ver)
    assert _ok, _why
    _kinds = sorted(s["kind"] for s in _ctx["found"]["slots"])
    assert _kinds == ["fact_visual", "voice_kind"], _ctx["found"]["slots"]


def test_a_non_target_fact_sharing_the_moved_voice_is_identity_swapped_only():
    _text = "甲留在门外。"
    _plan0 = _plan(
        shot_beats=["节拍：甲留在门外｜载体：台词1", "节拍：门｜载体：画面"],
        required_facts=[
            _text + "｜原文证据：“" + _text + "”｜承载：B1/台词1",
            "甲在等候｜原文证据：“" + _text + "”｜承载：B1/台词1",
            "门开着｜原文证据：“门开着。”｜承载：B2/画面"],
        dialogue_lines=[_text])
    _v = _view(_plan0)
    _slot = pcr.content_slots(_v, [_voice_problem("d01")],
                              verification_record=_record(
                                  "d01", "novel_narration", _text))["slots"][0]
    _scope = pcr.build_editable_scope(_v, [_slot])
    assert _scope["allowed_rebind_fact_uids"] == ["f01", "f02"], _scope
    assert _scope["allowed_fact_uids"] == [], _scope
    _cand, _p, _rk, _ans = _apply(_v, _slot, pcr.ACT_RECLASSIFY, _scope)
    assert _rk["kind"] == "applied", (_rk, _p)
    assert _cand["fact_bindings"][0]["carriers"] == [
        {"kind": "narration", "uid": "n01"}]
    assert _cand["fact_bindings"][1]["carriers"] == [
        {"kind": "narration", "uid": "n01"}]
    assert _cand["fact_bindings"][2]["carriers"] == [
        {"kind": "visual", "uid": "b02"}]
    # 主张/证据不变（身份替换不是改写）
    assert [(f["uid"], f["claim"], f["evidence"]) for f in _cand["frozen_facts"]] \
        == [(f["uid"], f["claim"], f["evidence"]) for f in _v["frozen_facts"]]


def test_a_failed_record_does_not_authorize_reclassification():
    _text = "甲留在门外。"
    _plan0 = _plan(
        shot_beats=["节拍：甲留在门外｜载体：台词1"],
        required_facts=[_text + "｜原文证据：“" + _text + "”｜承载：B1/台词1"],
        dialogue_lines=[_text])
    _v = _view(_plan0)
    _rec = _record("d01", "novel_narration", _text)
    _rec["status"] = "failed"
    _found = pcr.content_slots(_v, [_voice_problem("d01")],
                               verification_record=_rec)
    assert _found["slots"] == []
    assert any(str(x).startswith("verify_voice_not_speech")
               for x in _found["unexpressible"])


def test_a_record_keyed_to_another_candidate_does_not_authorize_a_move():
    _text = "甲留在门外。"
    _plan0 = _plan(
        shot_beats=["节拍：甲留在门外｜载体：台词1"],
        required_facts=[_text + "｜原文证据：“" + _text + "”｜承载：B1/台词1"],
        dialogue_lines=[_text])
    _rec = _record("d01", "novel_narration", _text)
    _rec["plan_digest"] = "0" * 64
    import types
    _ver = types.SimpleNamespace(records={"0" * 64: _rec})
    _ok, _why, _ctx = o._content_slots_applicable(
        _plan0, [_voice_problem("d01")], verification=_ver)
    assert not _ok and _ctx["state"] == pcr.STATE_UNSUPPORTED, (_why, _ctx)


def test_a_bad_voice_answer_is_rejected_before_any_candidate_verification(monkeypatch):
    _text = "甲留在门外。"
    _plan0 = _plan(
        shot_beats=["节拍：甲留在门外｜载体：台词1"],
        required_facts=[_text + "｜原文证据：“" + _text + "”｜承载：B1/台词1"],
        dialogue_lines=[_text])
    _rec = _record("d01", "novel_narration", _text)
    _rec["plan_digest"] = cv.plan_digest_of(_plan0)
    _ver = _FakeVerification(_rec)
    from tests.step01_script_gen.test_plan_content_slots import _Budget, _stub
    _calls = _stub(monkeypatch, {
        "schema": pcr.SLOT_ANSWER_SCHEMA,
        "answers": [{"slot_id": "voice_kind:d01",
                     "action": pcr.ACT_RECLASSIFY, "text": "改写"}]})
    _out = o.revise_plan_once(_plan0, [_voice_problem("d01")], _Budget(), None,
                              novel_text=NOVEL, verification=_ver)
    assert _out["evidence"].get("failure_stage") == "answer_contract", _out["evidence"]
    assert len(_calls) == 1, len(_calls)
    # 没有为候选申请核验记录：拒绝发生在内容核验之前
    assert len(_ver.records) == 1, _ver.records.keys()


# ---- 记录门槛与控制器接线 ----

def test_without_or_without_matching_record_there_is_no_reclass_slot():
    _text = "甲留在门外。"
    _plan0 = _plan(
        shot_beats=["节拍：甲留在门外｜载体：台词1"],
        required_facts=[_text + "｜原文证据：“" + _text + "”｜承载：B1/台词1"],
        dialogue_lines=[_text])
    _v = _view(_plan0)
    _none = pcr.content_slots(_v, [_voice_problem("d01")])
    assert _none["slots"] == []
    assert any(str(x).startswith("verify_voice_not_speech")
               for x in _none["unexpressible"])
    _aligned = pcr.content_slots(
        _v, [_voice_problem("d01")],
        verification_record=_record("d01", "novel_speech", _text))
    assert _aligned["slots"] == []
    assert any(str(x).startswith("verify_voice_not_speech")
               for x in _aligned["unexpressible"])


def test_the_controller_uses_the_current_record_for_slot_applicability():
    _text = "甲留在门外。"
    _plan0 = _plan(
        shot_beats=["节拍：甲留在门外｜载体：台词1"],
        required_facts=[_text + "｜原文证据：“" + _text + "”｜承载：B1/台词1"],
        dialogue_lines=[_text])
    _rec = _record("d01", "novel_narration", _text)
    _rec["plan_digest"] = cv.plan_digest_of(_plan0)
    import types
    _ver = types.SimpleNamespace(records={_rec["plan_digest"]: _rec})
    _ok, _why, _ctx = o._content_slots_applicable(
        _plan0, [_voice_problem("d01")], verification=_ver)
    assert _ok, _why
    assert [s["kind"] for s in _ctx["found"]["slots"]] == ["voice_kind"]
    _ok2, _why2, _ctx2 = o._content_slots_applicable(
        _plan0, [_voice_problem("d01")], verification=None)
    assert not _ok2 and _ctx2["state"] == pcr.STATE_UNSUPPORTED, (_why2, _ctx2)


class _FakeVerification:
    """只实现控制器被用到的两个面：`records`（槽位适用性）与 `record_for`（验收）."""

    novel_digest = "x" * 64
    max_verifications = 3
    require_coherence = False

    def __init__(self, base):
        self.base = base
        self.records = {base["plan_digest"]: base}

    def record_for(self, view, plan_digest=None, **kw):
        _d = str(plan_digest or "")
        if _d in self.records:
            return self.records[_d]
        _r = copy.deepcopy(self.base)
        _r["plan_digest"] = _d
        _r["voice_units"] = [
            {"unit_uid": pc.ref_uid(_u), "verdict": "supported",
             "source_kind": ("novel_narration" if _k == "narration_units"
                             else "novel_speech"),
             "speaker": "", "echo_text": str(_u.get("text") or ""), "reason": ""}
            for _k in ("dialogue_units", "narration_units")
            for _u in view.get(_k) or []]
        self.records[_d] = _r
        return _r


def test_the_revision_entry_routes_a_voice_kind_problem_to_the_voice_slot(monkeypatch):
    _text = "甲留在门外。"
    _plan0 = _plan(
        shot_beats=["节拍：甲留在门外｜载体：台词1"],
        required_facts=[_text + "｜原文证据：“" + _text + "”｜承载：B1/台词1"],
        dialogue_lines=[_text])
    _rec = _record("d01", "novel_narration", _text)
    _rec["plan_digest"] = cv.plan_digest_of(_plan0)
    _ver = _FakeVerification(_rec)
    from tests.step01_script_gen.test_plan_content_slots import _Budget, _stub
    _calls = _stub(monkeypatch, {
        "schema": pcr.SLOT_ANSWER_SCHEMA,
        "answers": [{"slot_id": "voice_kind:d01",
                     "action": pcr.ACT_RECLASSIFY}]})
    _out = o.revise_plan_once(_plan0, [_voice_problem("d01")], _Budget(), None,
                              novel_text=NOVEL, verification=_ver)
    _ev = _out["evidence"]
    assert _ev["route"] == "content_slots", _ev
    assert "voice_kind:d01" in (_ev.get("content_slots", {}).get("slots") or []), _ev
    assert len(_calls) == 1, len(_calls)
    assert _out["kind"] in ("applied", "illegal"), _out["kind"]
