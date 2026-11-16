# -*- coding: utf-8 -*-
"""M2.2b：成对额度预检**两层都要够**（2026-11-16，2026-11-17 补双层）.

用户复核指出的缺口：旧实现只看逻辑调用余额，而 `--max-sends 1` 配的是 **HTTP
尝试**上限 ⇒ 预检放行，直到修复请求撞到发送守卫才被拦。那证明的是"发送上限有效"，
不是"成对准入有效"。

本文件钉住：
  1. 未安装守卫 ⇒ 全部 None、`short=False`（零行为变化）；
  2. 逻辑层不足（阶段 / 全局）⇒ short；
  3. **HTTP 层不足（剩余 0 / 1 / 2 三种对照）** ⇒ 0/1 不足、2 刚好够；
  4. `short_on` 明确列出不足项，便于提交摘要与事后核对。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_guard():
    from shared import outbound_guard as og

    old = og.install(None)
    yield
    og.install(old)


def _install(**kw):
    from shared import outbound_guard as og

    g = og.OutboundGuard(**kw)
    og.install(g)
    return g


def test_no_guard_means_no_gate():
    import step01_script_gen.main as m

    rec = m._paired_repair_budget()
    assert rec["short"] is False and rec["short_on"] == []
    for key in ("remaining_logical_repair", "remaining_logical_audit",
                "remaining_logical_total", "remaining_attempts_repair",
                "remaining_attempts_audit", "remaining_attempts_total"):
        assert rec[key] is None


def test_logical_repair_exhausted():
    import step01_script_gen.main as m

    _install(phase_logical={"repair": 0, "audit": 5})
    rec = m._paired_repair_budget()
    assert rec["short"] is True
    assert "logical_repair" in rec["short_on"]


def test_logical_audit_exhausted():
    import step01_script_gen.main as m

    g = _install(phase_logical={"repair": 2, "audit": 1})
    g.begin("audit")
    rec = m._paired_repair_budget()
    assert rec["short"] is True and "logical_audit" in rec["short_on"]


def test_logical_global_short_of_two():
    import step01_script_gen.main as m

    g = _install(total_logical=3)
    g.begin("generate")
    rec = m._paired_repair_budget()          # 全局只剩 2 → 刚好够，不算 short
    assert rec["short"] is False
    g.begin("verify")                        # 再花 1 → 只剩 1
    rec = m._paired_repair_budget()
    assert rec["short"] is True and "logical_total" in rec["short_on"]


@pytest.mark.parametrize("remaining,expect_short", [(0, True), (1, True), (2, False)])
def test_attempts_total_three_point_contrast(remaining, expect_short):
    """**剩余 HTTP 尝试 0 / 1 / 2** 的离线对照（用户点名要求的最小覆盖）."""
    import step01_script_gen.main as m

    class _Resp:
        status_code = 200

    g = _install(total_attempts=2, transport=lambda url, **kw: _Resp())
    for _ in range(2 - remaining):           # 消耗掉差额，制造指定剩余量
        g("u", json={"messages": [{"role": "user", "content": "x"}]})
    rec = m._paired_repair_budget()
    assert rec["remaining_attempts_total"] == remaining
    assert rec["short"] is expect_short
    if expect_short:
        assert "attempts_total" in rec["short_on"]


def test_phase_attempts_exhausted():
    import step01_script_gen.main as m

    _install(phase_attempts={"repair": 0, "audit": 5})
    rec = m._paired_repair_budget()
    assert rec["short"] is True and "attempts_repair" in rec["short_on"]


def test_both_layers_available_is_go():
    import step01_script_gen.main as m

    _install(total_logical=4, total_attempts=4,
             phase_logical={"repair": 2, "audit": 2},
             phase_attempts={"repair": 2, "audit": 2})
    rec = m._paired_repair_budget()
    assert rec["short"] is False and rec["short_on"] == []
    assert rec["required"] == {"repair": 1, "audit": 1}
