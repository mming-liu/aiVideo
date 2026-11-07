# -*- coding: utf-8 -*-
"""S02：线上字节编码与失败计量的回归（2026-11-07）."""
import json

import pytest

from _text_gen import agent_plan_provider as ap
from _text_gen.wire import WireEncodingError, encode_json_body


def test_encode_roundtrips_and_shrinks_chinese():
    payload = {"model": "m", "messages": [{"role": "user", "content": "中文" * 50}]}
    body = encode_json_body(payload)
    assert json.loads(body) == payload
    # 与旧 requests(json=) 的默认序列化对比：中文不再被转义
    assert body == json.dumps(payload, ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")
    assert b"\\u4e2d" not in body


def test_encode_rejects_nan_instead_of_writing_illegal_json():
    with pytest.raises(WireEncodingError):
        encode_json_body({"x": float("nan")})


def test_post_sends_exact_encoded_bytes(monkeypatch):
    sent = {}

    class _Resp:
        status_code = 200
        headers = {}
        text = "{}"

    monkeypatch.setattr(ap.AgentPlanTextService, "_headers",
                        {"Content-Type": "application/json"})
    monkeypatch.setattr(ap.requests, "post",
                        lambda url, **kw: (sent.update(kw), _Resp())[1])
    svc = object.__new__(ap.AgentPlanTextService)
    payload = {"messages": [{"role": "user", "content": "中文"}]}
    _resp, body = ap.AgentPlanTextService._post(svc, "https://x", payload, 5)
    assert "json" not in sent
    assert sent["data"] == body == encode_json_body(payload)
    assert sent["headers"]["Content-Type"] == "application/json"


def test_send_once_records_bytes_before_the_request_fails(monkeypatch):
    """连接异常时 trace 也必须带"本来要发多大"——否则失败请求无从计量."""
    def _boom(*a, **kw):
        raise ConnectionError("connection reset")

    monkeypatch.setattr(ap.AgentPlanTextService, "_headers", {"Authorization": "x"})
    monkeypatch.setattr(ap.requests, "post", _boom)
    svc = object.__new__(ap.AgentPlanTextService)
    trace = {}
    with pytest.raises(ConnectionError):
        svc._send_once("https://x", {"messages": [{"role": "user", "content": "中文"}]},
                       5, trace)
    assert trace["request_bytes"] > 0
    assert trace["request_sha256"]
    assert trace["logical_sha256"]
    assert trace["post_error"].startswith("ConnectionError")
