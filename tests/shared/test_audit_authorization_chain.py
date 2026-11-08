# -*- coding: utf-8 -*-
"""R05/F5：授权链的**逐条身份关联**（2026-10-30）.

被修的行为：旧判据是"attempt 数 == outcome 数、attempt_id 不重复"——数量完全相同、
把结果挂到另一条 attempt 上，或把整条链换成另一次运行，计数一模一样，照样判完整。

这里钉的是 validate_authorization_chain 的判别力：每个反例只改**身份**，不改数量。
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.audit_authorization import (  # noqa: E402
    MAX_SENDS, describe_state, validate_authorization_chain)

EXPECTED = {"authorization_id": "auth-1", "binding_digest": "bd-1", "run_id": "run-1",
            "provider": "agent_plan", "model": "deepseek-v4.1-flash",
            "call_ids": ["call-1"], "ledger_checked": True}


def _attempt(n=1, **over):
    row = {"attempt_id": "att_%d" % n, "attempt_index": n,
           "authorization_id": "auth-1", "binding_digest": "bd-1", "run_id": "run-1",
           "call_id": "call-%d" % n, "provider": "agent_plan",
           "model": "deepseek-v4.1-flash", "endpoint": "chat.completions"}
    row.update(over)
    return row


def _state(*, claim=True, attempts=None, outcomes=None):
    return {
        "claim": ({"authorization_id": "auth-1", "binding_digest": "bd-1",
                   "run_id": "run-1", "call_id": "call-1"} if claim else None),
        "attempts": list(attempts if attempts is not None else [_attempt(1)]),
        "outcomes": list(outcomes if outcomes is not None else [{"attempt_id": "att_1",
                                                                 "outcome": "returned"}]),
        "result": None,
    }


def _codes(problems) -> list:
    return [p["code"] for p in problems]


def test_a_legal_chain_passes():
    assert validate_authorization_chain(_state(), expected=EXPECTED) == []


def test_not_sent_has_no_chain_to_check():
    state = {"claim": None, "attempts": [], "outcomes": [], "result": None}
    assert validate_authorization_chain(state, expected=EXPECTED) == []
    assert describe_state(state)["send_state"] == "not_sent"


def test_same_counts_with_swapped_attempt_ids_are_rejected():
    """两条 attempt、两条 outcome，数量与唯一性都成立——只是结果挂错了对象."""
    state = _state(
        attempts=[_attempt(1, call_id="call-a"), _attempt(2, call_id="call-b")],
        outcomes=[{"attempt_id": "att_1", "outcome": "returned"},
                  {"attempt_id": "att_1", "outcome": "failed"}])
    exp = dict(EXPECTED, call_ids=["call-a", "call-b"])
    codes = _codes(validate_authorization_chain(state, expected=exp))
    assert "outcome_duplicated" in codes, codes
    assert "outcome_missing" in codes, codes
    # 计数层面"看起来"仍然是 2 == 2
    assert len(state["attempts"]) == len(state["outcomes"]) == 2


def test_cross_run_attempt_is_rejected_even_with_matching_counts():
    state = _state(attempts=[_attempt(1, run_id="run-other")])
    codes = _codes(validate_authorization_chain(state, expected=EXPECTED))
    assert "run_id_mismatch" in codes, codes


def test_binding_mismatch_is_rejected():
    state = _state(attempts=[_attempt(1, binding_digest="bd-other")])
    codes = _codes(validate_authorization_chain(state, expected=EXPECTED))
    assert "binding_digest_mismatch" in codes, codes


def test_attempt_must_belong_to_the_claim():
    state = _state(attempts=[_attempt(1, authorization_id="auth-2")])
    codes = _codes(validate_authorization_chain(state, expected=EXPECTED))
    assert "authorization_id_mismatch" in codes, codes


def test_endpoint_drift_inside_one_authorization_is_rejected():
    state = _state(
        attempts=[_attempt(1, call_id="call-a"),
                  _attempt(2, endpoint="responses.create", call_id="call-b")],
        outcomes=[{"attempt_id": "att_1", "outcome": "returned"},
                  {"attempt_id": "att_2", "outcome": "failed"}])
    exp = dict(EXPECTED, call_ids=["call-a", "call-b"])
    codes = _codes(validate_authorization_chain(state, expected=exp))
    assert "attempt_endpoint_drifted" in codes, codes
    assert "attempt_endpoint_mismatch" in codes, codes


def test_provider_model_are_part_of_identity():
    state = _state(attempts=[_attempt(1, provider="deepseek", model="deepseek-flash")])
    codes = _codes(validate_authorization_chain(state, expected=EXPECTED))
    assert "attempt_provider_mismatch" in codes, codes
    assert "attempt_model_mismatch" in codes, codes


def test_orphan_and_missing_outcomes_are_reported_separately():
    orphan = _state(outcomes=[{"attempt_id": "att_9", "outcome": "returned"}])
    codes = _codes(validate_authorization_chain(orphan, expected=EXPECTED))
    assert "outcome_orphaned" in codes, codes

    missing = _state(outcomes=[])
    codes = _codes(validate_authorization_chain(missing, expected=EXPECTED))
    assert "outcome_missing" in codes, codes


def test_attempt_must_appear_in_the_call_ledger():
    state = _state(attempts=[_attempt(1, call_id="call-absent")])
    exp = dict(EXPECTED, call_ids=["call-1"])
    codes = _codes(validate_authorization_chain(state, expected=exp))
    assert "attempt_call_missing_from_ledger" in codes, codes


def test_more_attempts_than_the_single_send_cap_is_rejected():
    state = _state(
        attempts=[_attempt(1), _attempt(2, call_id="call-2", attempt_index=2)],
        outcomes=[{"attempt_id": "att_1", "outcome": "returned"},
                  {"attempt_id": "att_2", "outcome": "returned"}])
    exp = dict(EXPECTED, call_ids=["call-1", "call-2"])
    codes = _codes(validate_authorization_chain(state, expected=exp))
    assert "attempt_over_single_send_cap" in codes, codes
    assert MAX_SENDS == 1


def test_claims_without_attempts_is_reserved_not_a_chain_failure():
    state = {"claim": {"authorization_id": "auth-1", "binding_digest": "bd-1",
                       "run_id": "run-1"}, "attempts": [], "outcomes": [], "result": None}
    assert describe_state(state)["send_state"] == "reserved"
    # 抢占但没进发送边界：这不是"身份不符"，链上没有问题记录
    assert validate_authorization_chain(state, expected=EXPECTED) == []


def test_problems_carry_record_identity_and_path():
    state = _state(attempts=[_attempt(1, run_id="run-other")])
    rec = [p for p in validate_authorization_chain(state, expected=EXPECTED)
           if p["code"] == "run_id_mismatch"][0]
    assert rec["ref"] == "att_1"
    assert rec["path"] == "attempts[0].run_id"
    assert rec["expected"] == "run-1" and rec["actual"] == "run-other"


def test_the_claim_must_point_at_the_same_call():
    """占用凭据的 call_id 必须是本次调用——否则"占用了"与"发送了"可能是两件事."""
    state = _state()
    state["claim"]["call_id"] = "call-other"
    codes = _codes(validate_authorization_chain(state, expected=EXPECTED))
    assert "claim_call_mismatch" in codes, codes
    assert "attempt_claim_call_mismatch" in codes, codes


def test_a_claim_without_call_id_is_reported():
    state = _state()
    del state["claim"]["call_id"]
    codes = _codes(validate_authorization_chain(state, expected=EXPECTED))
    assert "claim_call_id_missing" in codes, codes


def test_an_illegal_outcome_status_is_reported():
    """outcome 只允许 returned/failed/unknown；任意字符串不能算"结果合法"."""
    state = _state(outcomes=[{"attempt_id": "att_1", "outcome": "INVALID"}])
    problems = validate_authorization_chain(state, expected=EXPECTED)
    codes = _codes(problems)
    assert "outcome_status_invalid" in codes, codes
    rec = [p for p in problems if p["code"] == "outcome_status_invalid"][0]
    assert rec["path"] == "outcomes[0].outcome", rec
    assert rec["expected"] == ["returned", "failed", "unknown"], rec
    assert rec["actual"] == "INVALID", rec
