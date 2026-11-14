# -*- coding: utf-8 -*-
"""Step B：opencode 视觉通道 429/5xx/SSL 受控退避重试的离线单测（零网络）.

复核/计划要求：只对 429/502/503/504 与瞬态 SSLError/ConnectionError 退避；
400/401/403/404 等客户端/鉴权错误立即失败；优先遵循 `Retry-After`，否则 2s → 5s。
"""
from __future__ import annotations

import pytest

from shared import image_qc


class _Resp:
    def __init__(self, status, payload=None, text="", headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._payload


def _ok(content: str = "ok") -> _Resp:
    return _Resp(200, {"choices": [{"message": {"content": content}}]})


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AGENT_PLAN_TEXT_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("AGENT_PLAN_API_KEY", "test-key")
    monkeypatch.setenv("QC_VISION_HTTP_RETRIES", "2")
    # 不读真实图片：传输层只关心 data URL 已构造
    monkeypatch.setattr(image_qc, "_ark_image_data_url",
                        lambda path: "data:image/png;base64,AAAA")
    sleeps: list[float] = []
    monkeypatch.setattr(image_qc.time, "sleep", lambda sec: sleeps.append(sec))
    return sleeps


def _patch_posts(monkeypatch, responses):
    calls: list[dict] = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs)
        item = responses[len(calls) - 1]
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(image_qc.requests, "post", fake_post)
    return calls


def test_429_then_200_is_retried_with_default_backoff(monkeypatch, _env):
    calls = _patch_posts(monkeypatch,
                         [_Resp(429, text="rate limited"), _ok("done")])
    assert image_qc._opencode_vision_chat("x.png", "p") == "done"
    assert len(calls) == 2, "429 后必须重试一次"
    assert _env == [2.0], "无 Retry-After 时按 2s 退避"


def test_retry_after_header_wins(monkeypatch, _env):
    calls = _patch_posts(monkeypatch, [
        _Resp(503, text="busy", headers={"Retry-After": "7"}), _ok()])
    assert image_qc._opencode_vision_chat("x.png", "p") == "ok"
    assert len(calls) == 2
    assert _env == [7.0], "Retry-After 优先于默认退避"


def test_client_error_is_not_retried(monkeypatch, _env):
    calls = _patch_posts(monkeypatch, [_Resp(401, text="unauthorized")])
    with pytest.raises(RuntimeError, match="401"):
        image_qc._opencode_vision_chat("x.png", "p")
    assert len(calls) == 1, "鉴权错误不得重试"
    assert _env == []


def test_transient_ssl_error_is_retried(monkeypatch, _env):
    err = image_qc.requests.exceptions.SSLError("ssl eof")
    calls = _patch_posts(monkeypatch, [err, _ok()])
    assert image_qc._opencode_vision_chat("x.png", "p") == "ok"
    assert len(calls) == 2
    assert _env == [2.0]


def test_retries_exhausted_raises_after_three_attempts(monkeypatch, _env):
    calls = _patch_posts(monkeypatch, [
        _Resp(429, text="a"), _Resp(429, text="b"), _Resp(429, text="c")])
    with pytest.raises(RuntimeError, match="429"):
        image_qc._opencode_vision_chat("x.png", "p")
    assert len(calls) == 3, "默认 2 次重试 = 最多 3 次尝试"
    assert _env == [2.0, 5.0]


def test_retries_env_zero_disables_retry(monkeypatch, _env):
    monkeypatch.setenv("QC_VISION_HTTP_RETRIES", "0")
    calls = _patch_posts(monkeypatch, [_Resp(429, text="a")])
    with pytest.raises(RuntimeError):
        image_qc._opencode_vision_chat("x.png", "p")
    assert len(calls) == 1
    assert _env == []
