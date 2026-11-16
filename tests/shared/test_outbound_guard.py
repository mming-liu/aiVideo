# -*- coding: utf-8 -*-
"""M2.1：统一出站守卫的双层账本、三指标与连接池自愈（2026-11-16）.

要证明的：
  1. **逻辑调用层**在构造请求之前就拦住（`begin()` 超限即 fail-closed）；
  2. **网络尝试层**每次真实 POST 都记账（含 provider 内部重试），分类与全局上限
     都在**发出字节之前**判定；
  3. 三指标固定为 `logical_calls` / `http_attempts` / `responses_received`，
     不再有 `transmitted`；
  4. 瞬态 SSL/连接错误把连接池标记为脏，下一次发送前重建（不在此处重试）；
  5. 未安装守卫时 `begin_for_schema()` 是 no-op（零行为变化）。
"""
from __future__ import annotations

import pytest

from shared import outbound_guard as og


class _Resp:
    status_code = 200


def _body(messages):
    import json
    return {"data": json.dumps({"messages": messages}, ensure_ascii=False)}


def _hook_kw():
    return _body([{"role": "system", "content": "你是短视频脚本大师。"},
                  {"role": "user", "content": "生成钩子分镜"}])


def _other_kw():
    return _body([{"role": "system", "content": "你是编剧。"},
                  {"role": "user", "content": "生成分镜脚本"}])


# ---- 第 1 层：逻辑调用 ---------------------------------------------------
def test_logical_cap_blocks_before_any_transport(tmp_path):
    sent = []
    g = og.OutboundGuard(transport=lambda url, **kw: sent.append(url) or _Resp(),
                         phase_logical={"hook": 1}, log_dir=tmp_path)
    g.begin("hook")
    with pytest.raises(og.SendBlocked):
        g.begin("hook")
    assert sent == [], "逻辑层超限必须在构造/发送之前阻断"
    assert g.logical_calls["hook"] == 1
    assert g.blocked_by_phase["hook"] == 1


def test_total_logical_cap_spans_phases():
    g = og.OutboundGuard(total_logical=2)
    g.begin("audit")
    g.begin("repair")
    with pytest.raises(og.SendBlocked):
        g.begin("generate")


# ---- 第 2 层：网络尝试 ---------------------------------------------------
def test_attempts_count_every_post_including_retries(tmp_path):
    seq = [200, "ssl", "ssl", "ssl", "ssl", 200]
    state = {"i": 0}

    def transport(url, **kw):
        kind = seq[state["i"]]
        state["i"] += 1
        if kind == "ssl":
            raise ConnectionError("SSLError: EOF")
        return _Resp()

    g = og.OutboundGuard(transport=transport, log_dir=tmp_path)
    for _ in seq:
        try:
            g("u", **_other_kw())
        except ConnectionError:
            pass
    snap = g.snapshot()
    assert snap["http_attempts_total"] == 6
    assert snap["responses_total"] == 2
    assert len(g.calls) == 6
    assert sum(1 for c in g.calls if not c["response_received"]) == 4


def test_phase_attempt_cap_blocks_before_send(tmp_path):
    sent = {"n": 0}

    def transport(url, **kw):
        sent["n"] += 1
        return _Resp()

    g = og.OutboundGuard(transport=transport, phase_attempts={"hook": 2}, log_dir=tmp_path)
    g("u", **_hook_kw())
    g("u", **_hook_kw())
    with pytest.raises(og.SendBlocked):
        g("u", **_hook_kw())
    assert sent["n"] == 2, "超限请求不得再发出"
    assert g.snapshot()["http_attempts"]["hook"] == 2


def test_total_attempt_cap_blocks_before_send(tmp_path):
    sent = {"n": 0}

    def transport(url, **kw):
        sent["n"] += 1
        return _Resp()

    g = og.OutboundGuard(transport=transport, total_attempts=1, log_dir=tmp_path)
    g("u", **_other_kw())
    with pytest.raises(og.SendBlocked):
        g("u", **_other_kw())
    assert sent["n"] == 1


def test_explicit_phase_wins_over_prompt_sniffing():
    seen = []

    def transport(url, **kw):
        seen.append(kw)
        return _Resp()

    g = og.OutboundGuard(transport=transport)
    g("u", phase="vision_qc", **_other_kw())
    assert g.snapshot()["http_attempts"] == {"vision_qc": 1}


# ---- 三指标 --------------------------------------------------------------
def test_three_metrics_have_no_transmitted_key():
    g = og.OutboundGuard(transport=lambda url, **kw: _Resp())
    g.begin("audit")
    g("u", **_other_kw())
    snap = g.snapshot()
    assert set(snap) == {"logical_calls", "logical_total", "http_attempts",
                         "http_attempts_total", "responses_received",
                         "responses_total", "blocked"}
    assert "transmitted" not in snap


# ---- 连接池自愈 ----------------------------------------------------------
def test_transient_error_marks_pool_dirty(monkeypatch):
    import requests

    made = []

    class _FakeSession:
        def __init__(self):
            made.append(self)
            self.closed = False

        def close(self):
            self.closed = True

        def post(self, url, **kw):
            raise ConnectionError("SSL: UNEXPECTED_EOF_WHILE_READING")

    monkeypatch.setattr(requests, "Session", _FakeSession)
    g = og.OutboundGuard()
    with pytest.raises(ConnectionError):
        g("u", **_other_kw())
    assert g._session_dirty is True
    first = g._session
    g._get_session()                      # 下一次发送前重建
    assert g._session is not first and first.closed is True
    assert g._session_dirty is False


def test_non_transient_error_keeps_pool(monkeypatch):
    import requests

    class _FakeSession:
        def __init__(self):
            pass

        def close(self):
            pass

        def post(self, url, **kw):
            raise ValueError("bad payload")

    monkeypatch.setattr(requests, "Session", _FakeSession)
    g = og.OutboundGuard()
    with pytest.raises(ValueError):
        g("u", **_other_kw())
    assert g._session_dirty is False


# ---- 安装点 --------------------------------------------------------------
def test_begin_for_schema_is_noop_without_guard(monkeypatch):
    monkeypatch.setattr(og, "_CURRENT", None)
    assert og.begin_for_schema("script_audit") == ""
    assert og.snapshot() == {}


def test_install_roundtrip_and_schema_phase():
    old = og.install(None)
    try:
        g = og.OutboundGuard()
        og.install(g)
        og.begin_for_schema("script_audit")
        og.begin_for_schema("hook_shot")
        assert g.logical_calls == {"audit": 1, "hook": 1}
        assert og.snapshot()["logical_total"] == 2
    finally:
        og.install(old)
    assert og.current() is None


def test_remaining_logical_reports_headroom():
    g = og.OutboundGuard(total_logical=3)
    g.begin("audit")
    assert g.remaining_logical() == 2
    g2 = og.OutboundGuard(phase_logical={"hook": 2})
    g2.begin("hook")
    assert g2.remaining_logical("hook") == 1
