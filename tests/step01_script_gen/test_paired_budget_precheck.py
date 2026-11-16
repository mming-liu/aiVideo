# -*- coding: utf-8 -*-
"""M2.2b：成对额度预检（2026-11-16）.

要证明的：`_paired_repair_budget()` 只在**装上了 OutboundGuard** 时给读数；
剩余额度不足以覆盖「1 次修复 + 1 次修后复审」时 `short=True`，提交循环据此
保存快照并报 `budget_exhausted`，**不发起注定无法闭环的孤儿修复**。
未安装守卫（测试/其它阶段）时一律 `short=False`，行为逐字不变。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_guard():
    from shared import outbound_guard as og

    old = og.install(None)
    yield
    og.install(old)


def test_no_guard_means_no_gate():
    import step01_script_gen.main as m

    rec = m._paired_repair_budget()
    assert rec["short"] is False
    assert rec["remaining_repair"] is None and rec["remaining_audit"] is None


def test_short_when_repair_exhausted():
    from shared import outbound_guard as og
    import step01_script_gen.main as m

    og.install(og.OutboundGuard(phase_logical={"repair": 0, "audit": 5}))
    rec = m._paired_repair_budget()
    assert rec["short"] is True
    assert rec["remaining_repair"] == 0


def test_short_when_audit_exhausted():
    from shared import outbound_guard as og
    import step01_script_gen.main as m

    g = og.OutboundGuard(phase_logical={"repair": 2, "audit": 1})
    g.begin("audit")                        # 用掉唯一一次复审
    og.install(g)
    rec = m._paired_repair_budget()
    assert rec["short"] is True
    assert rec["remaining_audit"] == 0


def test_not_short_when_both_available():
    from shared import outbound_guard as og
    import step01_script_gen.main as m

    og.install(og.OutboundGuard(phase_logical={"repair": 1, "audit": 1}))
    rec = m._paired_repair_budget()
    assert rec["short"] is False
    assert rec["required"] == {"repair": 1, "audit": 1}


def test_global_cap_counts_toward_pair():
    from shared import outbound_guard as og
    import step01_script_gen.main as m

    g = og.OutboundGuard(total_logical=2)
    g.begin("generate")
    og.install(g)
    assert m._paired_repair_budget()["short"] is True
