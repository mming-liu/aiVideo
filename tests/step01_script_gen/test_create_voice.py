# -*- coding: utf-8 -*-
"""P2 create_voice：合法已有落点/新节拍、来源与保护守卫（离线）."""
import copy
import hashlib

import pytest

from shared import content_verification as cv
from shared import presentation_contract as pc
from step01_script_gen import outline as o
from step01_script_gen import plan_content_repair as pcr
from tests.shared.test_fact_split import _view

NOVEL = "甲留在门外。乙走进屋。"


def _view_for(existing=True):
    v = copy.deepcopy(_view())
    v["narration_units"] = []
    v["frozen_events"] = ["e01", "e02"]
    v["frozen_facts"] = [{"uid": "f01", "claim": "甲留在门外",
                          "evidence": "甲留在门外。"}]
    v["fact_bindings"] = [{"fact_uid": "f01", "carriers": []}]
    v["beats"] = [
        {"uid": "b01", "event_uids": ["e01"], "text": "无关画面", "purpose": "",
         "evidence": "无关", "source": {}, "voice_ref": {
             "kind": "visual", "beat_uid": "b01"}},
        {"uid": "b02", "event_uids": ["e02"],
         "text": "甲留在门外" if existing else "无关画面",
         "purpose": "", "evidence": "甲留在门外。" if existing else "无关",
         "source": {}, "voice_ref": {"kind": "visual", "beat_uid": "b02"}},
    ]
    return v


def _record(view):
    return {
        "schema": cv.SCHEMA_VERIFICATION, "status": "ok",
        "plan_digest": cv.plan_digest_of(view),
        "novel_digest": hashlib.sha256(NOVEL.encode()).hexdigest(),
        "rule_version": cv.DEFAULT_RULE_VERSION,
        "facts": [{"fact_uid": "f01", "verdict": "supported",
                   "support_spans": ["甲留在门外。"]}],
        "source_support": [{"fact_uid": "f01", "verdict": "supported",
                            "evidence_spans": ["甲留在门外。"], "reason": ""}],
        "voice_units": [],
    }


def _slot(view, record=None):
    _found = pcr.content_slots(
        view, [{"code": "verify_fact_unsupported", "uid": "f01",
                "kind": "verification", "path": "facts"}],
        verification_record=record or _record(view), novel_text=NOVEL,
        record_admitted=True)
    assert _found["slots"], _found
    return _found["slots"][0]


def _answer(slot, **kw):
    _a = {"slot_id": slot["slot_id"], "action": pcr.ACT_CREATE_VOICE,
          "text": "甲留在门外。", "voice_kind": "narration"}
    _a.update(kw)
    return {"schema": pcr.SLOT_ANSWER_SCHEMA, "answers": [_a]}


def _compile(view, slot, answer):
    _scope = pcr.build_editable_scope(view, [slot], answer=answer)
    _delta, _dp = pcr.compile_delta(view, [slot], answer, novel_text=NOVEL)
    return _delta, _dp, _scope


def test_create_narration_on_existing_beat_keeps_beat_count_and_fact():
    v = _view_for(existing=True)
    slot = _slot(v)
    assert pcr.ACT_CREATE_VOICE in slot["allowed_actions"]
    assert slot["voice_existing_beat_uids"] == ["b02"]
    assert slot["voice_new_allowed"] is False
    ans = _answer(slot, placement="existing", beat_uid="b02")
    assert pcr.answer_problems(ans, [slot]) == []
    d, dp, scope = _compile(v, slot, ans)
    assert d is not None and not dp, dp
    cand, cp, rk = pc.apply_delta_view(v, d, editable_scope=scope,
                                       accept=lambda _v: [])
    assert rk["kind"] == "applied", (rk, cp)
    assert [b["uid"] for b in cand["beats"]] == ["b01", "b02"]
    assert cand["beats"][1]["voice_ref"]["kind"] == "narration"
    _uid = cand["beats"][1]["voice_ref"]["uid"]
    assert cand["narration_units"][0]["uid"] == _uid
    assert cand["narration_units"][0]["text"] == "甲留在门外。"
    assert {"kind": "narration", "uid": _uid} in cand["fact_bindings"][0]["carriers"]
    assert cand["beats"][0]["voice_ref"]["kind"] == "visual"
    _plan = pc.plan_from_view(cand)
    assert _plan["narration_lines"][0]["text"] == "甲留在门外。"
    assert len(_plan["shot_beats"]) == 2


def test_create_dialogue_on_new_beat_when_no_legal_existing_landing():
    v = _view_for(existing=False)
    slot = _slot(v)
    assert slot["voice_existing_beat_uids"] == []
    assert slot["voice_new_allowed"] is True
    ans = _answer(slot, voice_kind="dialogue", placement="new",
                  insert_after_uid="b01", event_index=2,
                  beat_text="甲说：我留在门外。")
    assert pcr.answer_problems(ans, [slot]) == []
    d, dp, scope = _compile(v, slot, ans)
    assert d is not None and not dp, dp
    cand, cp, rk = pc.apply_delta_view(v, d, editable_scope=scope,
                                       accept=lambda _v: [])
    assert rk["kind"] == "applied", (rk, cp)
    assert len(cand["beats"]) == 3
    _nb = cand["beats"][1]
    assert _nb["text"] == "甲说：我留在门外。"
    assert _nb["voice_ref"]["kind"] == "dialogue"
    _uid = _nb["voice_ref"]["uid"]
    assert cand["dialogue_units"][0]["uid"] == _uid
    assert cand["dialogue_units"][0]["text"] == "甲留在门外。"
    assert {"kind": "dialogue", "uid": _uid} in cand["fact_bindings"][0]["carriers"]
    _plan = pc.plan_from_view(cand)
    assert _plan["dialogue_lines"] == ["甲留在门外。"]
    assert len(_plan["shot_beats"]) == 3


def test_existing_landing_available_rejects_new_beat_before_accept():
    v = _view_for(existing=True)
    slot = _slot(v)
    ans = _answer(slot, placement="new", insert_after_uid="b01", event_index=1,
                  beat_text="甲留在门外。")
    _probs = pcr.answer_problems(ans, [slot])
    assert [p["code"] for p in _probs] == ["repair_create_voice_existing_available"]


def test_non_verbatim_voice_text_is_rejected_before_accept():
    v = _view_for(existing=True)
    slot = _slot(v)
    ans = _answer(slot, text="甲留在门外等待。", placement="existing", beat_uid="b02")
    _probs = pcr.answer_problems(ans, [slot])
    assert [p["code"] for p in _probs] == ["repair_create_voice_not_in_source"]


def test_existing_beat_keeps_other_visual_fact_and_body():
    v = _view_for(existing=True)
    v["frozen_facts"].append({"uid": "f02", "claim": "门外无人",
                              "evidence": "无关画面"})
    v["fact_bindings"].append({"fact_uid": "f02", "carriers": [
        {"kind": "visual", "beat_uid": "b02"}]})
    _old_b02 = copy.deepcopy(v["beats"][1])
    _old_f02 = copy.deepcopy(v["fact_bindings"][1]["carriers"])
    slot = _slot(v)
    ans = _answer(slot, placement="existing", beat_uid="b02")
    d, dp, scope = _compile(v, slot, ans)
    assert d is not None and not dp, dp
    cand, cp, rk = pc.apply_delta_view(v, d, editable_scope=scope,
                                       accept=lambda _v: [])
    assert rk["kind"] == "applied", (rk, cp)
    assert cand["beats"][1]["text"] == _old_b02["text"]
    assert cand["beats"][1]["evidence"] == _old_b02["evidence"]
    assert [{"kind": c.get("kind"), "uid": c.get("uid") or c.get("beat_uid")}
            for c in cand["fact_bindings"][1]["carriers"]] == [
        {"kind": "visual", "uid": "b02"}]


def test_hook_bound_fact_does_not_open_create_voice():
    v = _view_for(existing=True)
    v["fact_bindings"][0]["carriers"] = [{"kind": "visual", "beat_uid": "b02"}]
    v["hook_bindings"] = {"spoken_uids": [], "carriers": [
        {"kind": "visual", "uid": "b02"}]}
    slot = _slot(v)
    assert pcr.ACT_CREATE_VOICE not in slot["allowed_actions"]


def test_create_voice_target_resolves_temp_mapping():
    _slot0 = {"slot_id": "slot:fact:f01", "kind": "fact_visual",
              "allowed_actions": [pcr.ACT_CREATE_VOICE]}
    _tmp = pcr._create_voice_temp_uid(_slot0)
    _targets, _unresolved = o._voice_targets_from_slots([_slot0], {_tmp: "n01"})
    assert _targets == {"n01"} and _unresolved is False
    _out = o._slots_revision_outcome(
        [{"code": "verify_voice_unsupported", "uid": "n01"}],
        target_voice_uids=_targets)
    assert _out["content_repaired"] is False, _out


def test_direct_slot_without_admitted_record_has_no_create_voice():
    v = _view_for(existing=True)
    _found = pcr.content_slots(
        v, [{"code": "verify_fact_unsupported", "uid": "f01"}],
        verification_record=_record(v), novel_text=NOVEL)   # 不传 record_admitted
    _slot0 = _found["slots"][0]
    assert pcr.ACT_CREATE_VOICE not in _slot0["allowed_actions"]
    assert _slot0["voice_source_spans"] == []


def test_production_entry_without_admitted_record_has_no_create_voice():
    v = _view_for(existing=True)
    plan = pc.plan_from_view(v)
    _ok, _why, _ctx = o._content_slots_applicable(
        plan, [{"code": "verify_fact_unsupported", "uid": "f01"}],
        verification=None, novel_text=NOVEL)
    _slots = (_ctx or {}).get("found", {}).get("slots") or []
    assert _slots
    assert pcr.ACT_CREATE_VOICE not in _slots[0]["allowed_actions"]


def test_beat_text_match_is_not_a_landing_when_evidence_is_unrelated():
    v = _view_for(existing=True)
    v["beats"][1]["text"] = "甲留在门外"
    v["beats"][1]["evidence"] = "无关画面"
    slot = _slot(v)
    assert slot["voice_existing_beat_uids"] == []
    assert slot["voice_new_allowed"] is True


def test_duplicate_source_text_keeps_all_positions():
    v = _view_for(existing=True)
    _novel = "甲留在门外。乙走进屋。甲留在门外。"
    _rec = _record(v)
    _found = pcr.content_slots(
        v, [{"code": "verify_fact_unsupported", "uid": "f01"}],
        verification_record=_rec, novel_text=_novel, record_admitted=True)
    _land = _found["slots"][0]["voice_landings"][0]
    assert _land["text"] == "甲留在门外。"
    assert len(_land["positions"]) == 2
    assert _land["beat_uids"] == ["b02"]


def test_prompt_renders_only_the_converged_create_voice_action():
    v = _view_for(existing=True)
    slot = copy.deepcopy(_slot(v))
    slot["allowed_actions"] = [pcr.ACT_CREATE_VOICE]
    req = pcr.slot_request(v, [slot])
    prompt = pcr.slot_prompt(req, novel_text=NOVEL)
    assert "create_voice" in prompt
    assert "rewrite_visual" not in prompt
    assert "画面正文须完整表达" not in prompt
    assert "每个槽位只消费自己的权限" in prompt
    assert "slot:fact:f01" in prompt
    assert "target_uids" not in req["slots"][0]


@pytest.mark.v2_request
def test_limited_action_cannot_fall_back_when_slots_are_unexpressible(monkeypatch):
    from tests.step01_script_gen.test_plan_content_slots import _Budget, _stub

    v = _view_for(existing=True)
    plan = pc.plan_from_view(v)
    _calls = _stub(monkeypatch, {"schema": pcr.SLOT_ANSWER_SCHEMA, "answers": []})
    _out = o.revise_plan_once(
        plan, [{"code": "verify_voice_speaker_missing", "uid": "f01"}],
        _Budget(), None, novel_text=NOVEL,
        allowed_repair_actions={"create_voice"})
    assert _out["kind"] == "invalid_target", _out
    assert _calls == [], len(_calls)


@pytest.mark.v2_request
def test_action_convergence_narrows_actions_and_empty_intersection_makes_zero_calls(
        monkeypatch):
    from tests.step01_script_gen.test_plan_content_slots import _Budget, _stub

    v = _view_for(existing=True)
    plan = pc.plan_from_view(v)
    base = _record(v)
    digest = cv.plan_digest_of(plan)
    monkeypatch.setattr(cv, "record_protocol_problems", lambda *a, **k: [])

    class _Ctl:
        novel_digest = base["novel_digest"]
        max_verifications = 3
        require_coherence = False

        def __init__(self):
            self.records = {digest: base}

        def record_for(self, view, plan_digest=None, **kw):
            return self.records.get(str(plan_digest or ""), base)

    _ok, _why, _ctx = o._content_slots_applicable(
        plan, [{"code": "verify_fact_unsupported", "uid": "f01"}],
        verification=_Ctl(), novel_text=NOVEL,
        allowed_repair_actions={"create_voice"})
    assert _ok, _why
    _slot0 = _ctx["found"]["slots"][0]
    assert _slot0["allowed_actions"] == [pcr.ACT_CREATE_VOICE]

    _calls = _stub(monkeypatch, {
        "schema": pcr.SLOT_ANSWER_SCHEMA,
        "answers": [{"slot_id": _slot0["slot_id"],
                     "action": pcr.ACT_CREATE_VOICE,
                     "text": "甲留在门外。", "voice_kind": "narration",
                     "placement": "existing", "beat_uid": "b02"}]})
    _out_bad = o.revise_plan_once(
        plan, [{"code": "verify_fact_unsupported", "uid": "f01"}], _Budget(),
        None, novel_text=NOVEL, verification=_Ctl(),
        allowed_repair_actions={"reclassify_voice"})
    assert _out_bad["kind"] == "invalid_target", _out_bad
    assert _calls == [], len(_calls)


@pytest.mark.v2_request
def test_create_voice_reaches_the_revision_entry(monkeypatch):
    """生产入口：槽位 → create_voice → delta → 应用/重验 → 候选投影."""
    from tests.step01_script_gen.test_plan_content_slots import _Budget, _stub
    from step01_script_gen import outline as o

    v = _view_for(existing=True)
    v["frozen_events"] = ["e01", "e02", "e03"]
    v["beats"].append({
        "uid": "b03", "event_uids": ["e03"], "text": "乙走进屋",
        "purpose": "", "evidence": "乙走进屋", "source": {},
        "voice_ref": {"kind": "dialogue", "uid": "d01"}})
    v["dialogue_units"] = [{"uid": "d01", "text": "乙走进屋。", "source": {}}]
    plan0 = pc.plan_from_view(v)
    plan0["main_events"] = ["甲留在门外", "乙进屋", "甲继续等待"]
    base = _record(v)
    digest = cv.plan_digest_of(plan0)

    class _Ctl:
        novel_digest = base["novel_digest"]
        max_verifications = 3
        require_coherence = False

        def __init__(self):
            self.records = {digest: base}

        def record_for(self, view, plan_digest=None, **kw):
            _d = str(plan_digest or "")
            if _d in self.records:
                return self.records[_d]
            _u = []
            for _kind in ("dialogue", "narration"):
                for _x in view.get(_kind + "_units") or []:
                    _u.append({
                        "unit_uid": str(_x.get("uid") or ""),
                        "verdict": "supported",
                        "source_kind": ("novel_speech" if _kind == "dialogue"
                                        else "novel_narration"),
                        "speaker": "", "echo_text": str(_x.get("text") or ""),
                        "reason": ""})
            _r = copy.deepcopy(self.base)
            _r["plan_digest"] = _d
            _r["voice_units"] = _u
            return _r

    _ctl = _Ctl()
    _ctl.base = base
    monkeypatch.setattr(cv, "record_protocol_problems", lambda *a, **k: [])
    _calls = _stub(monkeypatch, {
        "schema": pcr.SLOT_ANSWER_SCHEMA,
        "answers": [{"slot_id": "slot:fact:f01",
                     "action": pcr.ACT_CREATE_VOICE,
                     "text": "甲留在门外。", "voice_kind": "narration",
                     "placement": "existing", "beat_uid": "b02"}]})
    _out = o.revise_plan_once(
        plan0, [{"code": "verify_fact_unsupported", "uid": "f01",
                 "kind": "verification", "path": "facts"}],
        _Budget(), None, novel_text=NOVEL, verification=_ctl)
    assert _out["evidence"]["route"] == "content_slots", _out["evidence"]
    assert len(_calls) == 1, len(_calls)
    _cand = _out.get("plan") or {}
    _narr = _cand.get("narration_lines") or []
    assert _narr and _narr[0].get("text") == "甲留在门外。"
    assert len(_cand.get("shot_beats") or []) == len(plan0.get("shot_beats") or [])
    _rv = _out["evidence"]["revision_outcome"]
    assert _rv["final_accepted"] is True, (
        _rv, (_cand.get("plan_revision") or {}).get("residuals"))

