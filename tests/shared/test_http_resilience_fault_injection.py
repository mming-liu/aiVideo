# -*- coding: utf-8 -*-
"""统一 HTTP 传输韧性层的故障注入（2026-11-28 O4）.

六类故障全部用**纯离线桩**注入，不发任何真实请求：
1. 429（带 `Retry-After`）→ 按头重试；
2. 502 连续 → 重试到上限后返回最后一次响应（不抛）；
3. `ssl.SSLError` → 连接池判脏、`close()` 后重建，再重试成功；
4. 读超时 → 按可重试传输错误退避后重试；
5. 400 参数错 → 0 重试立即返回；
6. 预算耗尽（`outbound_guard` 阶段上限 0）→ 发送前抛 `SendBlocked`，transport 一次不调。

另覆盖：指数退避序列精确、`Retry-After` 优先于退避、内容审核拒收识别、账目计数。
"""
from __future__ import annotations

import ssl
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import outbound_guard
from shared.http_resilience import (ErrorClass, ManagedSession, classify_exception,
                                    classify_status, is_content_rejection)


class _Resp:
    def __init__(self, status_code=200, headers=None, text=""):
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.text = text


class _Transport:
    """按脚本依次返回响应或抛异常，并记录调用次数."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, url, method="POST", **kw):
        self.calls.append((method, url))
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _session(script, **kw):
    transport = _Transport(script)
    kw.setdefault("jitter", 0.0)
    kw.setdefault("sleep", lambda _s: None)
    return ManagedSession(transport=transport, **kw), transport


# ---------------------------------------------------------------------------
# 1) 429 + Retry-After
# ---------------------------------------------------------------------------
def test_429_honours_retry_after_then_succeeds():
    sleeps = []
    transport = _Transport([_Resp(429, {"Retry-After": "7"}), _Resp(200)])
    session = ManagedSession(transport=transport, max_retries=2, jitter=0.0,
                             sleep=sleeps.append)
    resp = session.post("http://example.invalid")
    assert resp.status_code == 200
    assert len(transport.calls) == 2
    assert sleeps == [7.0], "Retry-After 必须优先于指数退避"
    assert session.attempts == 2 and session.retries == 1


# ---------------------------------------------------------------------------
# 2) 5xx 重试到上限
# ---------------------------------------------------------------------------
def test_502_exhausts_retries_and_returns_last_response():
    session, transport = _session([_Resp(502), _Resp(503), _Resp(504)], max_retries=2)
    resp = session.post("http://example.invalid")
    assert resp.status_code == 504
    assert session.attempts == 3 and session.retries == 2
    assert len(transport.calls) == 3


def test_backoff_sequence_is_exponential():
    sleeps = []
    transport = _Transport([_Resp(503), _Resp(503), _Resp(503), _Resp(503)])
    session = ManagedSession(transport=transport, max_retries=3, backoff_base=0.5,
                             backoff_max=100.0, jitter=0.0, sleep=sleeps.append)
    session.post("http://example.invalid")
    assert sleeps == [0.5, 1.0, 2.0]


# ---------------------------------------------------------------------------
# 3) SSL 断连 → 连接池重建
# ---------------------------------------------------------------------------
class _FakeSession:
    """可注入的会话：第一次 request 抛 SSL，之后成功；记录 close 次数."""

    def __init__(self, behaviour, log):
        self._behaviour = behaviour
        self._log = log
        self.closed = 0

    def request(self, method, url, **kw):
        self._log.append(("request", len(self._log)))
        item = self._behaviour.pop(0) if self._behaviour else _Resp(200)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self):
        self.closed += 1


def test_ssl_error_rebuilds_the_dirty_session():
    behaviour = [ssl.SSLError("bad handshake"), _Resp(200)]
    sessions = []

    def factory():
        sessions.append(_FakeSession(behaviour, []))
        return sessions[-1]

    sleeps = []
    session = ManagedSession(session_factory=factory, max_retries=2, jitter=0.0,
                             sleep=sleeps.append)
    resp = session.post("http://example.invalid")
    assert resp.status_code == 200
    assert session.attempts == 2
    assert session.rebuilds == 1, "SSL 后必须丢弃脏连接池并重建"
    assert sessions[0].closed == 1
    assert len(sessions) == 2


# ---------------------------------------------------------------------------
# 4) 读超时
# ---------------------------------------------------------------------------
def test_timeout_is_retryable_transport():
    session, transport = _session([TimeoutError("read timed out"), _Resp(200)],
                                  max_retries=1)
    resp = session.post("http://example.invalid")
    assert resp.status_code == 200
    assert session.attempts == 2 and session.retries == 1
    assert session.last_error_class is ErrorClass.OK


# ---------------------------------------------------------------------------
# 5) 不可重试的客户端错误
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_client_errors_are_never_retried(code):
    session, transport = _session([_Resp(code)], max_retries=3)
    resp = session.post("http://example.invalid")
    assert resp.status_code == code
    assert session.attempts == 1 and session.retries == 0
    assert len(transport.calls) == 1


# ---------------------------------------------------------------------------
# 6) 预算耗尽 → 发送前 fail-closed
# ---------------------------------------------------------------------------
def test_budget_exhausted_blocks_before_transport():
    session, transport = _session([_Resp(200)], phase="image_gen")
    guard = outbound_guard.OutboundGuard(phase_attempts={"image_gen": 0})
    outbound_guard.install(guard)
    try:
        with pytest.raises(outbound_guard.SendBlocked):
            session.post("http://example.invalid")
    finally:
        outbound_guard.install(None)
    assert transport.calls == []
    assert session.attempts == 0


def test_guard_records_each_attempt():
    session, _transport = _session([_Resp(503), _Resp(200)], phase="vision_qc",
                                   max_retries=1)
    guard = outbound_guard.OutboundGuard(phase_attempts={"vision_qc": 5})
    outbound_guard.install(guard)
    try:
        session.post("http://example.invalid")
    finally:
        outbound_guard.install(None)
    assert guard.snapshot()["http_attempts"] == {"vision_qc": 2}


# ---------------------------------------------------------------------------
# 分类与内容审核
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("status,expected", [
    (200, ErrorClass.OK), (204, ErrorClass.OK),
    (429, ErrorClass.RETRYABLE_HTTP), (500, ErrorClass.RETRYABLE_HTTP),
    (502, ErrorClass.RETRYABLE_HTTP), (503, ErrorClass.RETRYABLE_HTTP),
    (504, ErrorClass.RETRYABLE_HTTP),
    (400, ErrorClass.NON_RETRYABLE_CLIENT), (401, ErrorClass.NON_RETRYABLE_CLIENT),
    (403, ErrorClass.NON_RETRYABLE_CLIENT), (404, ErrorClass.NON_RETRYABLE_CLIENT),
    (418, ErrorClass.NON_RETRYABLE_CLIENT), (302, ErrorClass.OTHER),
    ("not-a-number", ErrorClass.OTHER),
])
def test_status_classification(status, expected):
    assert classify_status(status) is expected


@pytest.mark.parametrize("exc,expected", [
    (ssl.SSLError("x"), ErrorClass.RETRYABLE_TRANSPORT),
    (ConnectionResetError("reset"), ErrorClass.RETRYABLE_TRANSPORT),
    (TimeoutError("t"), ErrorClass.RETRYABLE_TRANSPORT),
    (ValueError("bad"), ErrorClass.OTHER),
])
def test_exception_classification(exc, expected):
    assert classify_exception(exc) is expected


def test_content_rejection_is_detected():
    assert is_content_rejection("OutputImageSensitiveContentDetected")
    assert is_content_rejection('{"code": "DataInspectionFailed"}')
    assert is_content_rejection("blocked by content_policy")
    assert not is_content_rejection('{"code": "InvalidParameter"}')
