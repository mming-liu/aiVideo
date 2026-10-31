# -*- coding: utf-8 -*-
"""R03：超时之后的**线程退出、连接关闭与迟到隔离**（2026-10-30）.

被验的对象是生产里真正生效的组合：TransportGate.request（墙钟闸门 + 工作线程）
套住 AgentPlanTextService._send_sse（requests.post + 读完整个 SSE 流）。

四个问题**分开取证**，不许互相代替：

1. 调用方是否在**有上界**的时间内返回（不是等 socket 自己超时）；
2. 超时之后工作线程是否**真的退出**（不是靠进程结束回收）；
3. 客户端是否**关闭了连接**（服务端迟到的写入会失败）；
4. 迟到的正文是否**不被采纳**，且一次请求最多**一次 POST**。

同一进程连续跑：超时场景三次 + 正常场景一次——若资源真的泄漏，后一次正常请求会露馅。
"""
from __future__ import annotations

import json
import threading
import time
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from _text_gen import agent_plan_provider as ap
from _text_gen import deadline as dl

BOUND = 0.6


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    mode = "ok"
    lock = threading.Lock()
    hits: list = []
    closed: list = []

    def log_message(self, *a):
        pass

    def _rid(self) -> str:
        return str(getattr(self, "_rid_value", "") or "")

    def _mark_closed(self) -> None:
        rid = self._rid()
        with type(self).lock:
            if rid not in type(self).closed:
                type(self).closed.append(rid)

    def _peer_closed(self) -> bool:
        """对端是否已关闭：recv 返回空 = 正常关闭，异常 = 中止（两者都是"已断开"）."""
        try:
            self.connection.settimeout(0.2)
            return self.connection.recv(1) == b""
        except TimeoutError:
            return False
        except OSError:
            return True

    def _emit(self, text: str) -> bool:
        """写一块 chunked 数据；返回 False 表示客户端已断开（连接关闭的正面证据）."""
        data = text.encode("utf-8")
        try:
            self.wfile.write(b"%x\r\n" % len(data) + data + b"\r\n")
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            self._mark_closed()
            return False

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        # 请求身份：优先请求头；生产入口不额外加头，因此也可从请求体的测试字段读
        rid = str(self.headers.get("x-test-rid") or "")
        if not rid:
            try:
                rid = str(json.loads(raw.decode("utf-8")).get("x-test-rid") or "")
            except Exception:
                rid = ""
        self._rid_value = rid
        with type(self).lock:
            type(self).hits.append(rid)
        mode = type(self).mode
        try:
            if mode == "no_headers":
                time.sleep(2.0)
                # 迟到的响应头：客户端早已按墙钟返回并关闭连接
                self.wfile.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
                self.wfile.flush()
                return
            if mode == "error_body":
                body = json.dumps({"error": "stub failure"}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            if mode == "headers_silence":
                time.sleep(3.0)
                # 迟到的写入失败、或对端已 EOF ⇒ 客户端确实关闭了连接（两种都记）
                if not self._emit(": late\n\n") or self._peer_closed():
                    self._mark_closed()
                return
            if mode == "heartbeat":
                # 心跳 5 次后静默：这条只证明"静默期由 socket 读超时收口"
                for _ in range(5):
                    if not self._emit(": ping\n\n"):
                        return
                    time.sleep(0.1)
                time.sleep(5.0)
                self._emit(": late\n\n")
                return
            if mode == "heartbeat_long":
                # 2026-10-31 复核：心跳**持续跨过截止与收尾窗口**（间隔 0.1s « 读超时 0.6s），
                # 因此线程退出不可能是 socket 静默超时造成的，只能是取消信号生效。
                for _ in range(40):
                    if not self._emit(": ping\n\n"):
                        return
                    time.sleep(0.1)
                time.sleep(1.0)
                return
            if mode == "late":
                time.sleep(1.2)
                self._emit('data: {"choices":[{"index":0,"delta":{"content":"late"},"finish_reason":"stop"}]}\n\n')
                self._emit("data: [DONE]\n\n")
                return
            # 正常完成：正文 + 终态 + DONE
            self._emit(": hello\n\n")
            self._emit('data: {"choices":[{"index":0,"delta":{"content":"{\\"a\\":1}"},"finish_reason":null}]}\n\n')
            self._emit('data: {"choices":[{"index":0,"delta":{},"finish_reason":"stop"}],"usage":{"completion_tokens":2}}\n\n')
            self._emit("data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError, OSError):
            with type(self).lock:
                type(self).closed.append(rid)
        finally:
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except Exception:
                pass


class _RecordedThread(threading.Thread):
    """记录**闸门自己创建**的工作线程，并在目标函数返回/抛错后置位 done."""

    created: list = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.done = threading.Event()
        type(self).created.append(self)

    def run(self):
        try:
            super().run()
        finally:
            self.done.set()


@pytest.fixture()
def server():
    _Handler.hits = []
    _Handler.closed = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.daemon_threads = True
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture()
def worker_threads(monkeypatch):
    """把 deadline 模块里的 threading 换成**只替换 Thread** 的观测垫片."""
    _RecordedThread.created = []
    monkeypatch.setattr(dl, "threading",
                        types.SimpleNamespace(Thread=_RecordedThread))
    return _RecordedThread.created


class _Req:
    def __init__(self, timeout=None, deadline=None):
        self.timeout_sec = timeout
        self.deadline_monotonic = deadline


def _service(monkeypatch):
    svc = object.__new__(ap.AgentPlanTextService)
    svc._model = "deepseek-v4.1-flash"
    svc._base_url = "x"
    monkeypatch.setattr(ap.AgentPlanTextService, "_headers", {}, raising=False)
    monkeypatch.setattr(ap, "_verbose_write", lambda *a, **k: None)
    return svc


def _post(server, monkeypatch, mode, rid, *, timeout=BOUND):
    """一次生产口径的传输：闸门 + 读取，返回 (响应, 是否超时, 原因, 轨迹, 计时, 停止位)."""
    _Handler.mode = mode
    svc = _service(monkeypatch)
    url = "http://127.0.0.1:%d/chat/completions" % server.server_address[1]
    trace: dict = {}
    stop = threading.Event()
    gate = ap.TransportGate(_Req(deadline=None), default_timeout=timeout, where="r03")
    payload = {"stream": True, "messages": [], "x-test-rid": rid}

    def _send(t):
        # 请求身份随请求头下去：服务端据此把命中/断开记到**本次**请求上
        # 不加自定义请求头：rode 身份随请求体的测试字段下去，生产签名不变
        return svc._send_sse(url, payload, t, gate=gate, trace=trace, stop=stop)

    started = time.monotonic()
    try:
        resp, timed_out, why = gate.request(_send)
    except ap.requests.exceptions.RequestException as exc:
        # 生产入口（generate）同样按传输失败收口：socket 超时先于闸门收尾时抛到这里，
        # 结果一样是**失败、不采纳、不重发**——这里只是把两条出口归一，便于观测。
        resp, timed_out = None, True
        why = "%s: %s" % (type(exc).__name__, exc)
    elapsed = time.monotonic() - started
    if why:
        stop.set()   # 生产入口在闸门返回后同样置位（agent_plan_provider.generate）
    return resp, timed_out, why, trace, elapsed, stop


def test_timeout_returns_bounded_and_worker_exits(server, monkeypatch, worker_threads):
    """① 迟迟不返回响应头：调用方按墙钟返回，工作线程在有限时间内退出."""
    resp, timed_out, why, trace, elapsed, stop = _post(server, monkeypatch,
                                                      "no_headers", "r-no-headers")
    assert timed_out and resp is None and why, (timed_out, why)
    assert elapsed < BOUND + 1.5, elapsed
    assert worker_threads, "闸门必须真的开了工作线程"
    th = worker_threads[-1]
    assert th.done.wait(5.0), "工作线程必须在有限时间内退出（不靠进程结束回收）"
    assert not th.is_alive()
    assert _Handler.hits == ["r-no-headers"], _Handler.hits


def test_headers_without_body_is_bounded_and_closes(server, monkeypatch, worker_threads):
    """② 响应头到了、正文不来：读取有上界，连接最终被关闭（服务端迟到写入失败）."""
    resp, timed_out, why, trace, elapsed, stop = _post(server, monkeypatch,
                                                      "headers_silence", "r-silence")
    assert timed_out and resp is None, (timed_out, why)
    assert elapsed < BOUND + 1.5, elapsed
    th = worker_threads[-1]
    assert th.done.wait(6.0), "工作线程必须在有限时间内退出"
    assert trace.get("headers_at") is not None, trace
    assert _Handler.hits == ["r-silence"], _Handler.hits
    # 服务端在客户端返回后还要睡 3s 才写；写失败即"客户端已关闭连接"的正面证据
    deadline_at = time.monotonic() + 5.0
    while time.monotonic() < deadline_at and "r-silence" not in _Handler.closed:
        time.sleep(0.1)
    assert "r-silence" in _Handler.closed, "客户端必须关闭连接，不能留在服务端挂着"


def test_heartbeat_without_terminal_stops_on_cancel(server, monkeypatch, worker_threads):
    """③ 持续心跳但无业务终态：超时后读取中止，迟到数据不采纳，线程退出."""
    resp, timed_out, why, trace, elapsed, stop = _post(server, monkeypatch,
                                                      "heartbeat", "r-heartbeat")
    assert timed_out and resp is None, (timed_out, why)
    assert elapsed < BOUND + 1.5, elapsed
    th = worker_threads[-1]
    assert th.done.wait(5.0), "心跳场景下线程也必须退出"
    assert trace.get("events", 0) == 0, trace
    assert _Handler.hits == ["r-heartbeat"], _Handler.hits


def test_late_complete_body_is_not_adopted(server, monkeypatch, worker_threads):
    """④ 超时后到达完整正文：不采纳、不重发，线程退出."""
    resp, timed_out, why, trace, elapsed, stop = _post(server, monkeypatch,
                                                      "late", "r-late")
    assert timed_out and resp is None, (timed_out, why)
    assert elapsed < BOUND + 1.5, elapsed
    th = worker_threads[-1]
    assert th.done.wait(5.0), "迟到正文到达后线程应退出"
    assert trace.get("events", 0) == 0, trace
    assert _Handler.hits == ["r-late"], _Handler.hits


def test_same_process_still_serves_a_normal_request(server, monkeypatch, worker_threads):
    """⑤ 同一进程内：三次超时之后，正常请求仍然成功（资源没有被耗尽）."""
    for i, mode in enumerate(("no_headers", "headers_silence", "heartbeat")):
        _resp, timed_out, _why, _trace, _elapsed, _stop = _post(
            server, monkeypatch, mode, "r-loop-%d" % i)
        assert timed_out
    for th in worker_threads:
        assert th.done.wait(6.0), "所有超时工作线程都必须退出"
    resp, timed_out, why, trace, elapsed, _stop = _post(server, monkeypatch, "ok",
                                                        "r-normal", timeout=10.0)
    assert not timed_out and not why, why
    assert resp is not None
    assert resp.json()["choices"][0]["message"]["content"] == '{"a":1}', resp.json()
    assert resp.json()["usage"]["completion_tokens"] == 2
    assert elapsed < 5.0, elapsed
    assert _Handler.hits.count("r-normal") == 1


def test_error_body_is_read_once_and_closed(server, monkeypatch):
    """⑥ 错误响应体：可读、可记录，且只发一次（不当成"没发送"）."""
    resp, timed_out, why, trace, elapsed, stop = _post(server, monkeypatch,
                                                      "error_body", "r-error", timeout=5.0)
    assert not timed_out and not why
    assert resp is not None and resp.status_code == 500
    assert "stub failure" in resp.json()["error"]
    assert _Handler.hits == ["r-error"]


def test_continuous_heartbeat_across_the_deadline_still_stops(server, monkeypatch,
                                                              worker_threads):
    """③b 心跳**持续跨过截止**（间隔 0.1s « 读超时 0.6s）：退出只能来自取消信号."""
    resp, timed_out, why, trace, elapsed, stop = _post(server, monkeypatch,
                                                      "heartbeat_long", "r-hb-long")
    assert timed_out and resp is None, (timed_out, why)
    assert elapsed < BOUND + 1.5, elapsed
    th = worker_threads[-1]
    stopped_at = time.monotonic()
    assert th.done.wait(5.0), "持续心跳下线程仍必须退出（不能靠 socket 静默超时）"
    exit_latency = time.monotonic() - stopped_at
    print("[r03] gate_return=%.3fs worker_exit<=%.3fs" % (elapsed, exit_latency))
    # 心跳间隔 0.1s ⇒ 取消后应在**一个心跳周期量级**内退出，远早于任何读超时窗口
    assert exit_latency < 1.0, exit_latency
    assert trace.get("events", 0) == 0, trace
    assert _Handler.hits == ["r-hb-long"], _Handler.hits
    # 客户端确实关闭：服务端后续心跳写失败
    deadline_at = time.monotonic() + 5.0
    while time.monotonic() < deadline_at and "r-hb-long" not in _Handler.closed:
        time.sleep(0.1)
    assert "r-hb-long" in _Handler.closed, "客户端必须关闭连接"
