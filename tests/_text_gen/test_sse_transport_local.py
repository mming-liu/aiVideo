"""SSE 传输层的**可区分性**验收（本地 HTTP 服务，无外网）.

对应工作包 M03/M05：先证明客户端能看见什么，再把「没观测到」与「没有数据」分开；
同时验证超时退出、连接关闭与「不重发」。
"""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from _text_gen import agent_plan_provider as ap


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    mode = "complete"

    def log_message(self, *a):        # 静音
        pass

    hits = 0

    def do_POST(self):
        type(self).hits += 1
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        mode = type(self).mode
        if mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://example.invalid/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if mode == "zero_body":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def emit(text: str):
            data = text.encode("utf-8")
            self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
            self.wfile.flush()

        try:
            if mode == "heartbeat_only":
                for _ in range(5):
                    emit(": ping\n\n")
                    time.sleep(0.1)
                time.sleep(5)          # 之后挂住，等客户端超时
            elif mode == "incomplete_event":
                emit('data: {"choices":[{"index":0,"delta":{"content":"x"}}]')  # 无空行
            elif mode == "complete":
                emit(': hello\n\n')
                emit('data: {"choices":[{"index":0,"delta":{"content":"{\\"a\\":1}"},"finish_reason":null}]}\n\n')
                emit('data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],'
                     '"usage":{"prompt_tokens":5,"completion_tokens":2,"total_tokens":7}}\n\n')
                emit("data: [DONE]\n\n")
            elif mode == "late":
                time.sleep(1.2)            # 客户端先超时，再放数据（迟到结果）
                emit('data: {"choices":[{"index":0,"delta":{"content":"late"},"finish_reason":"stop"}]}\n\n')
                emit("data: [DONE]\n\n")
            elif mode == "complete_keep_open":
                emit('data: {"choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":null}]}\n\n')
                emit('data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n')
                emit("data: [DONE]\n\n")
                emit("data: {\"choices\":[{\"index\":0,\"delta\":{\"content\":\"TAIL\"}}]}\n\n")
                time.sleep(5)          # 不关闭连接：客户端必须在 DONE 后自行结束
            elif mode == "same_chunk_tail":
                emit('data: {"choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":null}]}\n\n'
                     'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
                     'data: [DONE]\n\n'
                     'data: {"choices":[{"index":0,"delta":{"content":"TAIL"}}]}\n\n')
                time.sleep(2)
            elif mode == "same_chunk_partial_tail":
                emit('data: {"choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":null}]}\n\n'
                     'data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
                     'data: [DONE]\n\n'
                     'data: {"choices":[{"index":0,"delta":{"con')
                time.sleep(2)
            elif mode == "delay_then_complete":
                time.sleep(1.0)
                emit('data: {"choices":[{"index":0,"delta":{"content":"ok"},"finish_reason":"stop"}]}\n\n')
                emit("data: [DONE]\n\n")
                time.sleep(1)
            elif mode == "done_without_finish":
                emit('data: {"choices":[{"index":0,"delta":{"content":"x"}}]}\n\n')
                emit("data: [DONE]\n\n")
            elif mode == "multi_line":
                emit('data: {"choices":[{"index":0,\ndata: "delta":{"content":"ok"}}]}\n\n')
                emit('data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n')
                emit("data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError):
            return
        finally:
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except Exception:
                pass


@pytest.fixture()
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.daemon_threads = True
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


def _service(monkeypatch):
    svc = object.__new__(ap.AgentPlanTextService)
    svc._model = "deepseek-v4.1-flash"
    svc._base_url = "x"
    monkeypatch.setattr(ap.AgentPlanTextService, "_headers", {}, raising=False)
    monkeypatch.setattr(ap, "_verbose_write", lambda *a, **k: None)
    return svc


def _call(server, monkeypatch, mode, *, timeout=2.0):
    _Handler.mode = mode
    svc = _service(monkeypatch)
    trace: dict = {}
    url = "http://127.0.0.1:%d/chat/completions" % server.server_address[1]
    return svc, trace, url


def test_complete_event_stream_is_aggregated(server, monkeypatch):
    svc, trace, url = _call(server, monkeypatch, "complete")
    comp = svc._send_sse(url, {"stream": True}, 2.0, gate=None, trace=trace).json()
    assert comp["choices"][0]["message"]["content"] == '{"a":1}'
    assert comp["choices"][0]["finish_reason"] == "stop"
    assert comp["usage"]["completion_tokens"] == 2
    assert trace["bytes"] > 0 and trace["events"] >= 2, trace
    assert trace["first_byte_at"] is not None and trace["first_event_at"] is not None
    # 统一时间起点：首字节/首事件与响应头同一原点，可直接比较
    assert trace["first_byte_at"] >= trace["headers_at"]
    assert trace["first_event_at"] >= trace["first_byte_at"]


def test_multi_line_data_and_heartbeat(server, monkeypatch):
    svc, trace, url = _call(server, monkeypatch, "multi_line")
    comp = svc._send_sse(url, {"stream": True}, 2.0, gate=None, trace=trace).json()
    assert comp["choices"][0]["message"]["content"] == "ok"
    assert trace["heartbeats"] >= 0 and trace["events"] >= 1


def test_heartbeat_only_is_not_zero_bytes(server, monkeypatch):
    """"0 字节"必须由测量得出：心跳场景下 bytes>0、events==0."""
    svc, trace, url = _call(server, monkeypatch, "heartbeat_only")
    started = time.monotonic()
    with pytest.raises(Exception):
        svc._send_sse(url, {"stream": True}, 0.6, gate=None, trace=trace)
    elapsed = time.monotonic() - started
    assert elapsed < 3.0, "墙钟必须收口"
    assert trace["bytes"] > 0, trace
    assert trace["events"] == 0 and trace["heartbeats"] > 0, trace


def test_zero_body_is_reported_as_missing_terminal(server, monkeypatch):
    svc, trace, url = _call(server, monkeypatch, "zero_body")
    with pytest.raises(ap._SseIncomplete) as exc:
        svc._send_sse(url, {"stream": True}, 2.0, gate=None, trace=trace)
    assert "[DONE]" in str(exc.value), str(exc.value)
    assert trace["bytes"] == 0


def test_unterminated_event_is_rejected(server, monkeypatch):
    svc, trace, url = _call(server, monkeypatch, "incomplete_event")
    with pytest.raises(ap._SseIncomplete) as exc:
        svc._send_sse(url, {"stream": True}, 2.0, gate=None, trace=trace)
    assert "未完成事件" in str(exc.value) or "[DONE]" in str(exc.value), str(exc.value)


def test_late_result_after_timeout_is_not_adopted(server, monkeypatch):
    """超时后服务端才放数据：不采纳、不重发（只有一次 POST）."""
    svc, trace, url = _call(server, monkeypatch, "late")
    hits = []
    real_post = ap.requests.post

    def _counting_post(*a, **k):
        hits.append(1)
        return real_post(*a, **k)

    monkeypatch.setattr(ap.requests, "post", _counting_post)
    started = time.monotonic()
    with pytest.raises(Exception):
        svc._send_sse(url, {"stream": True}, 0.3, gate=None, trace=trace)
    elapsed = time.monotonic() - started
    assert elapsed < 2.0, "客户端必须按自身超时返回"
    assert len(hits) == 1, "超时不得触发重发"
    assert trace.get("events", 0) == 0, trace


def test_done_without_finish_reason_is_rejected(server, monkeypatch):
    svc, trace, url = _call(server, monkeypatch, "done_without_finish")
    with pytest.raises(ap._SseIncomplete) as exc:
        svc._send_sse(url, {"stream": True}, 2.0, gate=None, trace=trace)
    assert "finish_reason" in str(exc.value), str(exc.value)


def test_non_200_response_is_closed(monkeypatch):
    """非 200 也必须关闭响应（复核 2026-10-30：旧版提前 return 跳过 close）."""
    class _R:
        status_code = 500
        headers = {}
        text = '{"error": "boom"}'

        def __init__(self):
            self.closed = False

        def iter_content(self, chunk_size=0):
            return iter([b'{"error": "boom"}'])

        def close(self):
            self.closed = True

    fake = _R()
    monkeypatch.setattr(ap.requests, "post", lambda *a, **k: fake)
    svc = _service(monkeypatch)
    out = svc._send_sse("http://127.0.0.1:1/x", {"stream": True}, 1.0, gate=None, trace={})
    assert out.status_code == 500
    assert fake.closed, "非 200 分支必须关闭响应"


def test_done_ends_consumption_without_server_close(server, monkeypatch):
    """F1：收到 [DONE] 即结束消费，不等服务端关闭，也不拼 DONE 之后的正文。"""
    svc, trace, url = _call(server, monkeypatch, "complete_keep_open")
    started = time.monotonic()
    comp = svc._send_sse(url, {"stream": True}, 10.0, gate=None, trace=trace).json()
    elapsed = time.monotonic() - started
    assert comp["choices"][0]["message"]["content"] == "ok", comp
    assert comp["choices"][0]["finish_reason"] == "stop"
    assert elapsed < 3.0, "DONE 后不得继续等待服务端断开"
    assert trace.get("done_early") is True, trace


def test_abort_stops_reading(server, monkeypatch):
    """F2：调用方置位停止标志后，读取器立即中止（迟到数据不采纳）。"""
    import threading as _t
    svc, trace, url = _call(server, monkeypatch, "heartbeat_only")
    stop = _t.Event()
    _t.Timer(0.3, stop.set).start()
    started = time.monotonic()
    with pytest.raises(ap._SseAborted):
        svc._send_sse(url, {"stream": True}, 30.0, gate=None, trace=trace, stop=stop)
    assert time.monotonic() - started < 3.0


def test_done_in_same_chunk_ignores_trailing_content(server, monkeypatch):
    """F1 反例 1：正文 + DONE + 额外正文在同一块 ⇒ 不得拼成 okTAIL."""
    svc, trace, url = _call(server, monkeypatch, "same_chunk_tail")
    comp = svc._send_sse(url, {"stream": True}, 10.0, gate=None, trace=trace).json()
    assert comp["choices"][0]["message"]["content"] == "ok", comp


def test_done_with_partial_tail_does_not_fail(server, monkeypatch):
    """F1 反例 2：DONE 后跟半个事件 ⇒ 完整结果不能被判"未完成事件"."""
    svc, trace, url = _call(server, monkeypatch, "same_chunk_partial_tail")
    comp = svc._send_sse(url, {"stream": True}, 10.0, gate=None, trace=trace).json()
    assert comp["choices"][0]["message"]["content"] == "ok", comp


def test_cancel_after_chunk_arrives_is_honoured(server, monkeypatch):
    """F2 反例：读取期间置位，随后到达完整终态块 ⇒ 不得解析、不得改轨迹."""
    import threading as _t
    svc, trace, url = _call(server, monkeypatch, "delay_then_complete")
    stop = _t.Event()
    _t.Timer(0.3, stop.set).start()
    with pytest.raises(ap._SseAborted):
        svc._send_sse(url, {"stream": True}, 10.0, gate=None, trace=trace, stop=stop)
    assert trace.get("events", 0) == 0, trace


def test_redirect_is_not_followed(server, monkeypatch):
    """F3 补充：受控通道不跟随重定向（跨 host 302 会让核验端点与实际落点不一致）."""
    _Handler.hits = 0
    svc, trace, url = _call(server, monkeypatch, "redirect")
    out = svc._send_sse(url, {"stream": True}, 2.0, gate=None, trace={})
    assert out.status_code == 302
    assert _Handler.hits == 1, "不得跟随重定向发出第二次请求"
