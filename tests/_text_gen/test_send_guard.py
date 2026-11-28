"""发送守卫接线的定向测试（M07/S06-S08，2026-10-29）.

验收的是**顺序**：守卫必须在 client.responses.create() / chat.completions.create()
之前执行；守卫失败时 SDK 调用次数必须为 0；守卫只在受控作用域内生效，
普通入口（无守卫）行为不变。
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import pytest

from _text_gen import deepseek_provider as dp
from _text_gen.models import TextGenerationRequest, TextGenerationResult
from _text_gen.send_guard import BoundSendGuard, bind_send_guard
from _text_gen.service import TextGenerationService, _LedgeredService


class _Recorder:
    def __init__(self, *, fail=False):
        self.events = []
        self.fail = fail

    def before_send(self, *, provider, model, endpoint, call_id):
        self.events.append(("before_send", provider, model, endpoint, call_id))
        if self.fail:
            raise RuntimeError("guard denied before send")
        return "att_test_1"

    def record_send_outcome(self, attempt_id, outcome, metadata=None):
        self.events.append(("outcome", attempt_id, outcome))
        return ""


def _response(text=json.dumps({"issues": []})):
    return NS(status="completed",
              output=[NS(type="message",
                         content=[NS(type="output_text", text=text)])],
              usage=None, error=None, incomplete_details=None)


def _install_client(monkeypatch, *, create=None, stream=False):
    calls = []

    def _create(**kwargs):
        calls.append(kwargs)
        if create is not None:
            return create(**kwargs)
        return _response()

    client = NS(responses=NS(create=_create),
                chat=NS(completions=NS(create=_create)))
    monkeypatch.setattr(__import__("openai"), "OpenAI", lambda **kw: client)
    monkeypatch.setattr(dp.config, "DEEPSEEK_MODEL", "deepseek-flash")
    monkeypatch.setattr(dp.config, "DEEPSEEK_STREAM", stream)
    monkeypatch.setattr(dp.config, "validate", lambda: None)
    return calls


def _req(guard):
    req = TextGenerationRequest("system", "user")
    req.send_guard = BoundSendGuard(guard, "call_abc")
    return req


def test_guard_runs_before_responses_create_and_records_returned(monkeypatch):
    calls = _install_client(monkeypatch)
    guard = _Recorder()
    result = dp.DeepSeekTextService().generate(_req(guard))
    assert result.success is True
    assert calls, "SDK 必须被调用"
    kinds = [e[0] for e in guard.events]
    assert kinds == ["before_send", "outcome"], guard.events
    assert guard.events[-1][1:] == ("att_test_1", "returned")
    assert guard.events[0][3] == "responses.create"
    assert guard.events[0][4] == "call_abc", "attempt 必须绑定逻辑调用的 call_id"
    assert "send_guard" not in calls[0] and "guard" not in calls[0]


def test_guard_covers_streaming_branch(monkeypatch):
    class Stream:
        def __init__(self):
            self._events = [NS(type="response.completed", response=_response())]
            self.closed = False

        def __iter__(self):
            return iter(self._events)

        def close(self):
            self.closed = True

    calls = _install_client(monkeypatch, create=lambda **kw: Stream(), stream=True)
    guard = _Recorder()
    result = dp.DeepSeekTextService().generate(_req(guard))
    assert result.success is True
    assert len(calls) == 1 and calls[0]["stream"] is True
    assert [e[0] for e in guard.events] == ["before_send", "outcome"]
    assert guard.events[-1][2] == "returned"


def test_sdk_failure_records_failed(monkeypatch):
    def _boom(**kwargs):
        raise ConnectionError("reset")

    _install_client(monkeypatch, create=_boom)
    guard = _Recorder()
    result = dp.DeepSeekTextService().generate(_req(guard))
    assert result.success is False
    assert [e[0] for e in guard.events] == ["before_send", "outcome"]
    assert guard.events[-1][2] == "failed"


def test_guard_denial_means_zero_sdk_calls(monkeypatch):
    calls = _install_client(monkeypatch)
    guard = _Recorder(fail=True)
    result = dp.DeepSeekTextService().generate(_req(guard))
    assert result.success is False
    assert "guard denied" in (result.error or "")
    assert calls == [], "守卫拒收时绝不能进入 SDK"
    assert [e[0] for e in guard.events] == ["before_send"]


def test_chat_fallback_is_also_guarded(monkeypatch):
    calls = []

    def _create(**kwargs):
        calls.append(kwargs)
        message = NS(content=json.dumps({"issues": []}), reasoning_content=None)
        return NS(choices=[NS(message=message, finish_reason="stop")], usage=None)

    client = NS(responses=NS(create=_create),
                chat=NS(completions=NS(create=_create)))
    monkeypatch.setattr(__import__("openai"), "OpenAI", lambda **kw: client)
    monkeypatch.setattr(dp.config, "DEEPSEEK_MODEL", "deepseek-reasoner")
    monkeypatch.setattr(dp.config, "validate", lambda: None)
    guard = _Recorder()
    result = dp.DeepSeekTextService().generate(_req(guard))
    assert result.success is True
    assert guard.events[0][3] == "chat.completions.create"
    assert [e[0] for e in guard.events] == ["before_send", "outcome"]
    assert guard.events[-1][2] == "returned"


def test_without_guard_behaviour_is_unchanged(monkeypatch):
    calls = _install_client(monkeypatch)
    result = dp.DeepSeekTextService().generate(TextGenerationRequest("system", "user"))
    assert result.success is True and len(calls) == 1


def test_ledgered_service_binds_guard_with_call_id(monkeypatch):
    seen = {}

    class Service(TextGenerationService):
        def generate(self, req):
            seen["guard"] = getattr(req, "send_guard", None)
            return TextGenerationResult(success=True, text="ok")

    monkeypatch.setattr("shared.call_ledger.record_call", lambda *a, **kw: None)
    guard = _Recorder()
    with bind_send_guard(guard):
        result = _LedgeredService(Service(), "stub").generate(
            TextGenerationRequest("s", "u"))
    assert seen["guard"] is not None
    assert seen["guard"].call_id == result.call_id
    assert seen["guard"].before_send(provider="p", model="m", endpoint="e") == "att_test_1"
    assert guard.events[0][-1] == result.call_id


def test_ledgered_service_without_guard_passes_request_through(monkeypatch):
    seen = {}

    class Service(TextGenerationService):
        def generate(self, req):
            seen["guard"] = getattr(req, "send_guard", None)
            return TextGenerationResult(success=True, text="ok")

    monkeypatch.setattr("shared.call_ledger.record_call", lambda *a, **kw: None)
    _LedgeredService(Service(), "stub").generate(TextGenerationRequest("s", "u"))
    assert seen["guard"] is None
