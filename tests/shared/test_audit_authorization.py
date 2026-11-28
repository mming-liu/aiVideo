"""一次性授权模块的定向单测（M09/M07，2026-10-29）.

只测 shared.audit_authorization 的契约：身份校验、稳定路径、排他占用、发送尝试、
结果状态机、故障（不可读/写失败/改绑）。不碰真实网络与真实模型。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from shared.audit_authorization import (
    AuditAuthorization,
    AuthorizationBindingMismatch,
    AuthorizationClaimed,
    AuthorizationStateError,
    SendNotPermitted,
    authorization_binding_digest,
    describe_state,
    read_state,
)
from shared.paths import step01_audit_authorization_dir


BINDING = {"candidate_sha256": "a" * 64, "revision": "r" * 64, "max_sends": 1,
           "provider": "deepseek", "model": "deepseek-flash"}


def _auth(state_root: Path, *, auth_id="test-authorization-01", run_id="run-1",
          binding=None) -> AuditAuthorization:
    return AuditAuthorization(authorization_id=auth_id, binding=binding or BINDING,
                              state_dir=Path(state_root) / ("auth_" + auth_id[-8:]),
                              run_id=run_id)


@pytest.fixture()
def state_root(tmp_path):
    return tmp_path / "registry"


# ---------------------------------------------------------------------------
# S03/S04：身份与稳定路径
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["", "short", "has space here", "../escape-path",
                                 "a" * 65, "-leadingdash", "trailing\\slash"])
def test_invalid_authorization_id_rejected(bad):
    with pytest.raises(ValueError):
        step01_audit_authorization_dir(bad)


def test_state_dir_is_stable_and_root_independent(monkeypatch, tmp_path):
    first = step01_audit_authorization_dir("stable-auth-0001")
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path / "other-output"))
    monkeypatch.setenv("STEP01_RUN_ID", "another-run")
    assert step01_audit_authorization_dir("stable-auth-0001") == first
    # 隔离方式：替换**模块事实源**（测试进程内），不是运行期环境开关。
    import shared.paths as paths_mod
    monkeypatch.setattr(paths_mod, "AUDIT_AUTHORIZATION_ROOT", tmp_path / "registry")
    second = step01_audit_authorization_dir("stable-auth-0001")
    assert second != first
    assert second.parent == tmp_path / "registry" / "audit_authorizations"


def test_env_var_is_inert_for_registry_root(monkeypatch, tmp_path):
    """P1：STEP01_AUDIT_STATE_ROOT 不得改变登记位置（生产解析不读环境）."""
    baseline = step01_audit_authorization_dir("stable-auth-0001")
    monkeypatch.setenv("STEP01_AUDIT_STATE_ROOT", str(tmp_path / "evil"))
    assert step01_audit_authorization_dir("stable-auth-0001") == baseline
    assert not (tmp_path / "evil").exists()


def test_binding_digest_is_canonical():
    assert authorization_binding_digest({"b": 1, "a": 2}) == \
        authorization_binding_digest({"a": 2, "b": 1})
    assert len(authorization_binding_digest(BINDING)) == 32


# ---------------------------------------------------------------------------
# S05：排他占用
# ---------------------------------------------------------------------------
def test_claim_is_exclusive_across_instances(state_root):
    first = _auth(state_root, run_id="run-a")
    second = _auth(state_root, run_id="run-b")
    attempt = first.before_send(provider="deepseek", model="deepseek-flash",
                                endpoint="responses.create", call_id="c1")
    assert attempt.startswith("att_")
    with pytest.raises(AuthorizationClaimed):
        second.before_send(provider="deepseek", model="deepseek-flash",
                           endpoint="responses.create", call_id="c2")
    claim = json.loads((first.state_dir / "claim.json").read_text(encoding="utf-8"))
    assert claim["run_id"] == "run-a" and claim["call_id"] == "c1"
    assert claim["binding_digest"] == first.binding_digest
    assert "claimed_at" in claim and "+" in claim["claimed_at"], "授权时间必须带时区"


def test_claim_rejects_rebinding_same_id(state_root):
    _auth(state_root).claim(call_id="c0")
    changed = dict(BINDING)
    changed["model"] = "deepseek-pro"
    with pytest.raises(AuthorizationBindingMismatch):
        _auth(state_root, binding=changed).claim(call_id="c1")


def test_concurrent_claim_only_one_wins(state_root):
    """并发占用：至多一次发送；败者必须是**结构化拒收**（已占用或状态不明），
    不允许未处理异常，也不得覆盖胜者终局（复核 2026-10-30）。"""
    winners, refused, unexpected = [], [], []

    def _try(i):
        guard = _auth(state_root, run_id="run-%d" % i)
        try:
            winners.append(guard.before_send(
                provider="deepseek", model="deepseek-flash",
                endpoint="responses.create", call_id="c%d" % i))
        except (AuthorizationClaimed, AuthorizationStateError) as exc:
            refused.append(type(exc).__name__)
        except BaseException as exc:                     # noqa: BLE001
            unexpected.append(repr(exc))

    threads = [threading.Thread(target=_try, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(winners) == 1 and len(refused) == 7, (winners, refused, unexpected)
    assert unexpected == [], unexpected
    state = describe_state(read_state(_auth(state_root).state_dir))
    assert state["attempts"] == 1
    assert state["send_state"] == "unknown", "只有 attempt、没有 outcome ⇒ 不明"


def test_second_send_in_same_guard_is_refused(state_root):
    guard = _auth(state_root)
    guard.before_send(provider="deepseek", model="deepseek-flash",
                      endpoint="responses.create", call_id="c1")
    with pytest.raises(SendNotPermitted):
        guard.before_send(provider="deepseek", model="deepseek-flash",
                          endpoint="responses.create", call_id="c1")
    rows = (guard.state_dir / "attempts.jsonl").read_text(encoding="utf-8").splitlines()
    assert len([r for r in rows if r.strip()]) == 1, "被拒的第二次不得再记 attempt"


def test_before_send_requires_call_id(state_root):
    with pytest.raises(AuthorizationStateError):
        _auth(state_root).before_send(provider="deepseek", model="m",
                                      endpoint="e", call_id="")


# ---------------------------------------------------------------------------
# S02/S11：结果状态机
# ---------------------------------------------------------------------------
def test_send_state_machine(state_root):
    guard = _auth(state_root)
    assert describe_state(read_state(guard.state_dir))["send_state"] == "not_sent"

    guard.claim(call_id="c1")
    assert describe_state(read_state(guard.state_dir))["send_state"] == "reserved"

    attempt = guard.before_send(provider="deepseek", model="m",
                                endpoint="responses.create", call_id="c1")
    assert describe_state(read_state(guard.state_dir))["send_state"] == "unknown"
    assert guard.record_send_outcome(attempt, "returned", {"stream": False}) == ""
    assert describe_state(read_state(guard.state_dir))["send_state"] == "returned"

def test_failed_outcome_is_distinct_from_returned(state_root):
    guard = _auth(state_root)
    attempt = guard.before_send(provider="deepseek", model="m",
                                endpoint="responses.create", call_id="c1")
    guard.record_send_outcome(attempt, "failed", {"error": "ConnectionError"})
    state = describe_state(read_state(guard.state_dir))
    assert state["send_state"] == "failed"
    assert state["outcomes"] == 1


def test_invalid_outcome_is_not_written(state_root):
    guard = _auth(state_root)
    attempt = guard.before_send(provider="deepseek", model="m",
                                endpoint="responses.create", call_id="c1")
    err = guard.record_send_outcome(attempt, "maybe", {})
    assert err and guard.write_errors
    assert not (guard.state_dir / "outcomes.jsonl").exists()


def test_result_record_is_atomic_and_visible(state_root):
    guard = _auth(state_root)
    guard.before_send(provider="deepseek", model="m", endpoint="e", call_id="c1")
    assert guard.write_result({"status": "BLOCK", "revision": "r"}) == ""
    state = describe_state(read_state(guard.state_dir))
    assert state["finished"] is True and state["result_status"] == "BLOCK"


# ---------------------------------------------------------------------------
# S11/S17：故障现场
# ---------------------------------------------------------------------------
def test_truncated_claim_is_refused_not_rebuilt(state_root):
    guard = _auth(state_root)
    guard.state_dir.mkdir(parents=True)
    claim = guard.state_dir / "claim.json"
    claim.write_text('{"authorization_id": "broken', encoding="utf-8")
    with pytest.raises(AuthorizationStateError):
        read_state(guard.state_dir)
    with pytest.raises(AuthorizationStateError):
        guard.before_send(provider="deepseek", model="m", endpoint="e", call_id="c1")
    assert claim.read_text(encoding="utf-8") == '{"authorization_id": "broken'
    assert not (guard.state_dir / "attempts.jsonl").exists()


def test_truncated_attempts_is_refused(state_root):
    guard = _auth(state_root)
    guard.claim(call_id="c1")
    (guard.state_dir / "attempts.jsonl").write_text('{"attempt_id": "att_1"',
                                                    encoding="utf-8")
    with pytest.raises(AuthorizationStateError):
        read_state(guard.state_dir)


def test_claim_write_failure_means_no_attempt(tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    guard = AuditAuthorization(authorization_id="write-failure-01", binding=BINDING,
                               state_dir=blocker / "auth_x", run_id="r")
    with pytest.raises(AuthorizationStateError):
        guard.before_send(provider="deepseek", model="m", endpoint="e", call_id="c1")


def test_attempts_without_claim_is_unknown(state_root):
    guard = _auth(state_root)
    guard.state_dir.mkdir(parents=True)
    (guard.state_dir / "attempts.jsonl").write_text(
        json.dumps({"attempt_id": "att_1", "call_id": "c1"}) + "\n", encoding="utf-8")
    assert describe_state(read_state(guard.state_dir))["send_state"] == "unknown"


def test_empty_claim_file_is_state_unknown(state_root):
    """胜者「已创建、未写入」的窗口：败者必须报状态不明，而不是 JSON 解析崩溃。"""
    guard = _auth(state_root)
    guard.state_dir.mkdir(parents=True, exist_ok=True)
    (guard.state_dir / "claim.json").write_text("", encoding="utf-8")
    with pytest.raises(AuthorizationStateError):
        guard.before_claim_probe() if hasattr(guard, "before_claim_probe") else guard.claim(call_id="c1")
