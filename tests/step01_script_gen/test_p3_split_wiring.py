# -*- coding: utf-8 -*-
"""P3 前置离线可达性：D → M 桩 → apply → R 桩 → 投影/状态.

只验证控制流与守卫，不调用模型、不接生产拆分请求。
"""
from __future__ import annotations

import pytest

from shared import content_verification as cv
from shared import fact_split as fs
from shared import presentation_contract as pc
from tests.shared.test_fact_split import (
    NOVEL, _base_plan, _children, _ledger, _ops, _record_for, _scope, _view,
)
from step01_script_gen import outline as o
from step01_script_gen import split_judge as sj
from step01_script_gen import split_transaction as st

PARENT = "f02"


def _d() -> list:
    _entry = (_ledger().get("splits") or [])[0]
    _parent = next(f for f in _view()["frozen_facts"] if f["uid"] == PARENT)
    return fs.d_problems(_parent, _children(), _entry["parent_evidence_spans"])


def _apply(*, ledger=None, accept=None):
    return fs.apply_split_view(
        _view(), _ops(), scope=_scope(),
        ledger=_ledger() if ledger is None else ledger,
        record=_record_for(_base_plan()), accept=accept)


def _candidate_record(candidate):
    _plan = pc.plan_from_view(candidate)
    rec = _record_for(_plan, fact_uid="f01", verdict="supported")
    rec["obligation_basis"] = cv.obligation_basis_digest(candidate)
    return rec


def test_d_m_apply_r_projection_reaches_effective():
    assert _d() == []
    cand, probs, res = _apply(accept=lambda _v: [])
    assert probs == [] and res["kind"] == "applied", (probs, res)
    assert res["mapping"] == {"f02": ["f02.1", "f02.2"]}
    assert [f["uid"] for f in cand["frozen_facts"]] == [
        "f01", "f02.1", "f02.2", "f03"]
    _plan = pc.plan_from_view(cand)
    _back, _vp = pc.view_from_plan(_plan)
    assert _vp == [] and [f["uid"] for f in _back["frozen_facts"]] == [
        "f01", "f02.1", "f02.2", "f03"]
    _entry = (fs.context(cand).get("splits") or [])[0]
    rec = _candidate_record(cand)
    assert fs.split_state(cand, _entry, record=rec) == "effective"


def test_m_negative_is_not_applied():
    cand, probs, res = _apply(ledger=_ledger(verdict="contradicted"))
    assert cand is None
    assert res["kind"] == "illegal"
    assert "split_judgment_not_supported" in [p["code"] for p in probs]


def test_d_rejects_child_evidence_outside_parent_spans():
    _entry = (_ledger().get("splits") or [])[0]
    _kids = _children()
    _kids[0]["evidence"] = "不在父证据里的句子"
    _problems = fs.d_problems(
        next(f for f in _view()["frozen_facts"] if f["uid"] == PARENT),
        _kids, _entry["parent_evidence_spans"])
    assert [p["code"] for p in _problems] == ["split_evidence_not_derived"]


def test_record_basis_mismatch_is_detectable_before_apply():
    _plan = _base_plan()
    rec = _record_for(_plan)
    rec["obligation_basis"] = "0" * 64
    assert cv.record_basis_matches(rec, _plan) is False


def test_current_accept_hook_reports_residuals_but_does_not_gate_commit():
    # 现状证据：apply_split_view 的 accept 只是候选探针；生产 wrapper 必须把
    # 非空 residuals 转成提交拒绝（P3-C）。
    cand, probs, res = _apply(
        accept=lambda _v: [{"code": "r_child_unsupported"}])
    assert probs == []
    assert res["kind"] == "applied"
    assert res["residuals"] == [{"code": "r_child_unsupported"}]


def _m_supported(_prompt):
    return {"verdict": "supported", "missing": "", "reason": ""}


def _tx(**kw):
    base = _view()
    plan = _base_plan()
    ledger = _ledger()
    params = dict(
        base_view=base, base_plan=plan, base_record=_record_for(plan),
        split_ops=_ops(), scope=_scope(), ledger=ledger,
        novel_text=NOVEL, m_call=_m_supported, m_call_id="m-test",
        verify_candidate=lambda c: _record_for(
            pc.plan_from_view(c), fact_uid="f01", verdict="supported"),
        accept=lambda _c: [], acceptance=lambda _p, _n: [])
    params.update(kw)
    return st.run_split_transaction(**params)


def test_transaction_d_m_apply_r_commits_only_after_r_and_acceptance():
    _m_calls = []
    _r_calls = []

    def _m(prompt):
        _m_calls.append(prompt)
        return _m_supported(prompt)

    def _r(candidate):
        _r_calls.append(candidate)
        return _record_for(pc.plan_from_view(candidate), fact_uid="f01",
                           verdict="supported")

    out = _tx(m_call=_m, verify_candidate=_r)
    assert out["ok"] is True and out["stage"] == "committed", out
    assert out["m_calls"] == 1 and len(_m_calls) == 1 and len(_r_calls) == 1
    assert [f["uid"] for f in out["candidate"]["frozen_facts"]] == [
        "f01", "f02.1", "f02.2", "f03"]
    assert out["state"] == "effective"


def test_transaction_m_negative_stops_before_apply():
    _r_calls = []
    out = _tx(m_call=lambda _p: {"verdict": "contradicted", "missing": "缺条件",
                                  "reason": "子主张遗漏条件"},
              verify_candidate=lambda c: _r_calls.append(c) or {})
    assert out["ok"] is False and out["stage"] == "m", out
    assert out["candidate"] is None and out["m_calls"] == 1
    assert _r_calls == []


def test_transaction_authorization_failure_never_calls_m():
    _m_calls = []
    plan = _base_plan()
    bad = _record_for(plan)
    bad["obligation_basis"] = "0" * 64
    out = _tx(base_record=bad, m_call=lambda p: _m_calls.append(p) or
              _m_supported(p))
    assert out["ok"] is False and out["stage"] == "authorization", out
    assert _m_calls == [] and out["m_calls"] == 0


def _record_with_parent(field, value):
    rec = _record_for(_base_plan())
    for _r in rec[field]:
        if _r.get("fact_uid") == "f02":
            _r["verdict"] = value
            if field == "facts" and value == "supported":
                _r["missing"] = ""
                for _o in _r.get("obligations") or []:
                    _o["verdict"] = "supported"
            return rec
    raise AssertionError("parent missing")


def test_authorization_requires_source_supported_and_display_partial():
    _m_calls = []
    _bad_source = _record_with_parent("source_support", "contradicted")
    out = _tx(base_record=_bad_source, m_call=lambda p: _m_calls.append(p) or
              _m_supported(p))
    assert out["ok"] is False and out["stage"] == "authorization", out
    assert _m_calls == [] and out["m_calls"] == 0

    _bad_display = _record_with_parent("facts", "supported")
    _m_calls.clear()
    out = _tx(base_record=_bad_display, m_call=lambda p: _m_calls.append(p) or
              _m_supported(p))
    assert out["ok"] is False and out["stage"] == "authorization", out
    assert _m_calls == [] and out["m_calls"] == 0


def test_judge_split_stamps_call_evidence_and_rejects_unknown_fields():
    _prompts = []
    out = sj.judge_split(
        "甲说话并离开", _children(),
        call=lambda p: _prompts.append(p) or {
            "verdict": "supported", "missing": "", "reason": ""},
        call_id="m-1")
    assert out["ok"] is True
    j = out["judgment"]
    assert j["mode"] == "model" and j["call_id"] == "m-1"
    assert j["inputs_digest"] == fs.inputs_digest("甲说话并离开", _children())
    assert len(j["prompt_digest"]) == 64 and len(j["response_digest"]) == 64
    assert "甲说话并离开" in _prompts[0] and "甲说话" in _prompts[0]

    bad = sj.judge_split(
        "甲说话并离开", _children(),
        call=lambda _p: {"verdict": "supported", "missing": "", "reason": "",
                          "extra": 1}, call_id="m-2")
    assert bad["ok"] is False and bad["judgment"] is None
    assert bad["problems"][0]["code"] == "split_m_unknown_field"


def test_split_acceptance_is_wired_into_plan_records():
    cand, _, _ = _apply()
    plan = pc.plan_from_view(cand)
    assert [p["code"] for p in o._split_acceptance_records(plan)] == [
        "split_pending"]


def test_transaction_r_probe_residual_blocks_commit():
    out = _tx(accept=lambda _c: [{"code": "r_child_unsupported"}])
    assert out["ok"] is False and out["stage"] == "probe", out
    assert out["candidate"] is not None
    assert out["problems"][0]["code"] == "r_child_unsupported"


def test_transaction_r_child_not_supported_blocks_commit():
    def _r(c):
        rec = _record_for(pc.plan_from_view(c), fact_uid="f01",
                          verdict="supported")
        for _f in rec["facts"]:
            if _f.get("fact_uid") == "f02.1":
                _f["verdict"] = "partial"
                _f["support_spans"] = []
                _f["missing"] = "未展示"
                for _o in _f.get("obligations") or []:
                    _o["verdict"] = "partial"
                    _o["evidence"] = []
                    _o["missing"] = "未展示"
        return rec

    out = _tx(verify_candidate=_r)
    assert out["ok"] is False and out["stage"] == "r_state", out
    assert out["candidate"] is not None
    assert out["problems"][0]["code"] == "split_pending"


def test_split_acceptance_marks_pending_and_invalid():
    cand, _, _ = _apply()
    entry = (fs.context(cand).get("splits") or [])[0]
    assert fs.split_acceptance_problems(cand, None) == [
        {"code": "split_pending", "kind": "split", "uid": "f02", "ref": "f02",
         "path": "", "reason": "拆分尚未得到候选完整核验，不能采纳", "fix": ""}]


def test_create_voice_then_split_authorizes_on_v0_and_verifies_only_v2():
    v0 = _view()
    r0 = _record_for(_base_plan())
    m_calls = []
    r_calls = []

    def _create_voice(view):
        out = __import__("copy").deepcopy(view)
        out["narration_units"].append({"uid": "n02", "text": "甲离开。",
                                       "source": {"text": "甲离开。"}})
        out["beats"].append({"uid": "b04", "text": "甲离开。",
                             "event_uids": ["事件1"], "purpose": "",
                             "evidence": "甲离开。", "source": {},
                             "voice_ref": {"kind": "narration", "uid": "n02"}})
        return out

    def _verify(candidate):
        r_calls.append(candidate)
        return _record_for(pc.plan_from_view(candidate), fact_uid="f01",
                           verdict="supported")

    out = st.run_create_voice_then_split(
        v0=v0, r0=r0, create_voice=_create_voice,
        split_args={"split_ops": _ops(), "scope": _scope(),
                    "ledger": _ledger(), "novel_text": NOVEL,
                    "acceptance": lambda _p, _n: []},
        verify_candidate=_verify, m_call=lambda p: m_calls.append(p) or
        _m_supported(p), m_call_id="m-combo")
    assert out["ok"] is True and out["stage"] == "committed", out
    assert len(m_calls) == 1 and len(r_calls) == 1
    assert out["v2"] is not None and out["v1"] is not None
    assert out["baseline"] is v0
    assert cv.plan_digest_of(pc.plan_from_view(out["v1"])) != \
        cv.plan_digest_of(pc.plan_from_view(v0))
    assert r_calls[0] is out["v2"]


def test_create_voice_split_failure_rolls_back_to_v0():
    v0 = _view()
    r0 = _record_for(_base_plan())

    def _create_voice(view):
        out = __import__("copy").deepcopy(view)
        out["narration_units"].append({"uid": "n02", "text": "甲离开。",
                                       "source": {"text": "甲离开。"}})
        out["beats"].append({"uid": "b04", "text": "甲离开。",
                             "event_uids": ["事件1"], "purpose": "",
                             "evidence": "甲离开。", "source": {},
                             "voice_ref": {"kind": "narration", "uid": "n02"}})
        return out

    out = st.run_create_voice_then_split(
        v0=v0, r0=r0, create_voice=_create_voice,
        split_args={"split_ops": _ops(), "scope": _scope(),
                    "ledger": _ledger(), "novel_text": NOVEL,
                    "acceptance": lambda _p, _n: []},
        verify_candidate=lambda c: _record_for(
            pc.plan_from_view(c), fact_uid="f01", verdict="supported"),
        m_call=lambda _p: {"verdict": "contradicted", "missing": "",
                           "reason": ""}, m_call_id="m-fail")
    assert out["ok"] is False and out["rollback"] is True, out
    assert out["candidate"] is None and out["baseline"] is v0
    assert out["v1"] is not None
