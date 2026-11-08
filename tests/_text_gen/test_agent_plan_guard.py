"""agent_plan 发送守卫的位置与计量边界（P2，2026-10-29 复核）.

要点：守卫必须在 **TransportGate 真正调用发送闭包** 的那一刻执行。
闸门因截止/余量而未调用闭包时，必须零 HTTP、零 attempt、零 claim——
否则会出现「授权已被消费，却一次请求都没发」的账。
"""
from __future__ import annotations

import types

import pytest

from _text_gen import agent_plan_provider as ap


class _Guard:
    def __init__(self) -> None:
        self.before = []
        self.outcomes = []

    def before_send(self, *, provider, model, endpoint):
        self.before.append((provider, model, endpoint))
        return "att_test"

    def record_send_outcome(self, attempt_id, outcome, metadata=None):
        self.outcomes.append((attempt_id, outcome))
        return ""


class _Gate:
    """受控闸门桩：`call_closure=False` 模拟「截止时刻已到，闭包根本不会被调用」."""

    def __init__(self, *, call_closure: bool = True) -> None:
        self.call_closure = call_closure

    def wait_budget(self):
        return None

    def limit(self):
        return (30.0, "")

    def expiry_reason(self):
        return "截止时刻已到（stub）"

    def wait(self, seconds):
        return True

    def request(self, fn):
        if not self.call_closure:
            return None, True, "截止时刻已到（stub）"
        return fn(30.0), False, ""


class _Resp:
    status_code = 200
    text = "{}"


def _service() -> ap.AgentPlanTextService:
    svc = object.__new__(ap.AgentPlanTextService)
    svc._model = "deepseek-v4.1-flash"
    svc._base_url = "https://opencode.ai/zen/go/v1"
    svc._key_scheduler = types.SimpleNamespace(acquire=lambda timeout=None: None)
    return svc


def _req(guard):
    return types.SimpleNamespace(send_guard=guard, reasoning_effort=None,
                                 json_schema={}, user_prompt="probe",
                                 allow_truncated=False, timeout_sec=None)


@pytest.fixture()
def patched(monkeypatch):
    monkeypatch.setattr(ap, "_verbose_write", lambda *a, **k: None)
    monkeypatch.setattr(ap.AgentPlanTextService, "_build_payload",
                        lambda self, req: {"model": "deepseek-v4.1-flash", "messages": [],
                                           "temperature": 0, "top_p": 1, "seed": 0})
    monkeypatch.setattr(ap.AgentPlanTextService, "_parse_response",
                        lambda self, resp, model, mx, allow_truncated=False, trace=None:
                        types.SimpleNamespace(success=True, text="ok", error=""))
    return monkeypatch


def test_guard_is_inside_closure_and_before_http(patched):
    order = []
    guard = _Guard()
    guard_before = guard.before_send

    def _before(**kw):
        order.append("guard")
        return guard_before(**kw)

    guard.before_send = _before
    patched.setattr(ap, "TransportGate", lambda *a, **k: _Gate(call_closure=True))
    patched.setattr(ap.AgentPlanTextService, "_send_once",
                    lambda self, url, payload, timeout, trace, body=None:
                    (order.append("http"), _Resp())[1])

    res = _service().generate(_req(guard))

    assert res.success, res
    assert order == ["guard", "http"], "守卫必须紧贴 HTTP 之前、且在闭包内"
    assert guard.before == [("agent_plan", "deepseek-v4.1-flash", "chat.completions")]
    assert guard.outcomes == [("att_test", "returned")]


def test_expired_deadline_sends_nothing_and_claims_nothing(patched):
    """真闸门 + 已到期截止时刻：零 HTTP、零 attempt（守卫一次都不调）."""
    import time as _time

    sends = []
    guard = _Guard()
    patched.setattr(ap.AgentPlanTextService, "_send_once",
                    lambda *a, **k: sends.append(a) or _Resp())
    req = _req(guard)
    req.deadline_monotonic = _time.monotonic() - 1.0

    res = _service().generate(req)

    assert not res.success
    assert sends == [], "截止时刻已到不得发送"
    assert guard.before == [], "截止时刻已到不得占用授权/落 attempt"
    assert guard.outcomes == []


def test_gate_denial_calls_no_guard_and_no_http(patched):
    sends = []
    guard = _Guard()
    patched.setattr(ap, "TransportGate", lambda *a, **k: _Gate(call_closure=False))
    patched.setattr(ap.AgentPlanTextService, "_send_once",
                    lambda self, url, payload, timeout, trace: sends.append(url) or _Resp())

    res = _service().generate(_req(guard))

    assert not res.success
    assert sends == [], "闸门未调用闭包时不得发 HTTP"
    assert guard.before == [], "闸门未调用闭包时不得占用授权/落 attempt"
    assert guard.outcomes == []
