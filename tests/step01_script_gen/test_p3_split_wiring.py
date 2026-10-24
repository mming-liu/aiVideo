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
    _base_plan, _children, _ledger, _ops, _record_for, _scope, _view,
)

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
