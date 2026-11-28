# -*- coding: utf-8 -*-
"""v6.152（A1）：审计的**整次截止时间**受本跑剩余总预算约束.

2026-09-29 复核两轮裁决：上限 180 → 300（对齐通道自身的
`AGENT_PLAN_TEXT_TIMEOUT_SEC`），但必须受剩余总预算约束——实测单跑已到 950–995s，
只抬上限等于把「审计超时 BLOCK」换成「总预算耗尽 BLOCK」。

第二轮复核指出两处未闭合，本文件即其回归：

1. 剩余预算低于收尾预留时，旧实现仍返回一个 **5 秒**的请求超时——发出去注定超时，
   还照样挤占收尾余量。现在**不发请求**，直接记 `budget_exhausted`。
2. 超时只在进入审计前算一次，**重采样沿用同一个值**：首调耗掉时间后第二次仍可能拿到
   完整上限 ⇒「给提交收尾留 60s」形同虚设。现在截止时刻覆盖**整次审计**，
   每次实际发起前重算单次超时。

实据：五跑 21 次审计调用里 2 次硬失败都是 180s 超时（183.7s / 182.9s），
成功的单次调用区间约 74–144s。
"""
from __future__ import annotations

import sys
import time as _time
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


@pytest.fixture
def m(monkeypatch):
    import step01_script_gen.main as _m
    monkeypatch.setenv("STEP01_TOTAL_BUDGET_SEC", "900")
    monkeypatch.delenv("AUDIT_TIMEOUT_SEC", raising=False)
    monkeypatch.delenv("AUDIT_MIN_USEFUL_SEC", raising=False)
    monkeypatch.setattr(_m, "_STEP_TIMING_T0", None)
    return _m


class _Clock:
    """可控时钟（`audit.py` 里是函数内 `import time as _time`，打模块属性即可生效）."""

    def __init__(self, t0: float = 1000.0):
        self.t = t0

    def __call__(self) -> float:
        return self.t

    def advance(self, sec: float) -> None:
        self.t += sec


class _Svc:
    """按脚本返回结果，并把时钟推进指定秒数（模拟一次调用的真实耗时）."""

    def __init__(self, clock, results, *, advance_per_call=0.0):
        self.clock = clock
        self._results = list(results)
        self.advance_per_call = advance_per_call
        self.calls: list = []

    def generate(self, req):
        self.calls.append(req)
        self.clock.advance(self.advance_per_call)
        return self._results[min(len(self.calls) - 1, len(self._results) - 1)]


def _res(*, success=True, text='{"issues": []}', truncated=False):
    from _text_gen import models
    r = models.TextGenerationResult(success=success, text=text, error="",
                                    truncated=truncated)
    r.usage = {"output_tokens": 10, "reasoning_tokens": 5}
    return r


def _run_audit(monkeypatch, svc, *, timeout_sec=300, deadline_ts=None,
               min_useful_sec=None):
    import step01_script_gen.audit as au
    monkeypatch.setattr(au, "create_service", lambda *a, **k: svc)
    return au.run_same_model_audit_with_status(
        "原文", {"beats": [], "dialogue_lines": []},
        {"shots": [], "characters": [], "hook": None}, model="m",
        timeout_sec=timeout_sec, deadline_ts=deadline_ts,
        min_useful_sec=min_useful_sec)


# ---------------------------------------------------------------------------
# 允许额度（main 侧）
# ---------------------------------------------------------------------------
def test_no_budget_constraint_means_cap_and_no_deadline(m):
    allow, deadline, info = m._audit_budget_allowance()
    assert allow == m.AUDIT_TIMEOUT_CAP_SEC == 300
    assert deadline is None, "无预算约束不得凭空设截止时刻（旧行为不变）"
    assert "无总预算约束" in info["why"]
    assert m._budget_left_sec() is None


def test_ample_budget_uses_the_raised_cap(m, monkeypatch):
    monkeypatch.setattr(m, "_STEP_TIMING_T0", _time.time())
    allow, deadline, info = m._audit_budget_allowance()
    assert allow == 300
    assert deadline is not None and deadline > _time.time()
    assert "余量充足" in info["why"]


def test_allowance_is_bounded_by_remaining_budget(m, monkeypatch):
    monkeypatch.setattr(m, "_STEP_TIMING_T0", _time.time() - 800)   # 剩 ~100s
    allow, _deadline, info = m._audit_budget_allowance()
    assert allow == int(max(0, info["left"] - m.AUDIT_COMMIT_RESERVE_SEC))
    assert allow < 180, allow
    assert "受剩余总预算约束" in info["why"]


def test_budget_below_reserve_yields_zero_not_a_doomed_5s_request(m, monkeypatch):
    """复核第二轮的现场：总预算只剩 ~2 秒 → 旧实现返回 **5 秒**；现在必须是 0."""
    monkeypatch.setattr(m, "_STEP_TIMING_T0", _time.time() - 898)
    allow, deadline, info = m._audit_budget_allowance()
    assert allow == 0, (allow, info)
    assert deadline is not None and deadline <= _time.time() + 1


def test_env_cap_still_bounded_by_budget(m, monkeypatch):
    monkeypatch.setenv("AUDIT_TIMEOUT_SEC", "600")
    monkeypatch.setattr(m, "_STEP_TIMING_T0", _time.time() - 800)
    allow, _d, info = m._audit_budget_allowance()
    assert info["cap"] == 600
    assert allow == int(max(0, info["left"] - m.AUDIT_COMMIT_RESERVE_SEC))
    assert allow < 600


def test_why_string_is_gbk_safe(m, monkeypatch):
    """诊断文案必须能进 GBK 控制台（日志是文件+控制台双通道）."""
    monkeypatch.setattr(m, "_STEP_TIMING_T0", _time.time() - 800)
    _a, _d, info = m._audit_budget_allowance()
    info["why"].encode("gbk")


# ---------------------------------------------------------------------------
# 整次审计的截止时间（audit 侧）
# ---------------------------------------------------------------------------
def test_audit_sends_nothing_when_budget_is_insufficient(monkeypatch):
    """剩 ~2 秒 → **不发请求**，直接 `budget_exhausted`（BLOCK 由提交点兜）."""
    clock = _Clock()
    monkeypatch.setattr(_time, "time", clock)
    svc = _Svc(clock, [_res()])
    res = _run_audit(monkeypatch, svc, deadline_ts=clock.t + 2.0)

    assert res.status == "budget_exhausted", res.status
    assert svc.calls == [], "预算不足时不得发出请求（旧实现会发一个 5 秒的）"
    assert res.attempts == 0
    assert res.failed_attempts[0]["failure"] == "budget_exhausted"
    assert "总预算不足" in res.error


def test_resample_gets_only_the_time_left_on_the_deadline(monkeypatch):
    """首调耗掉 150s（额度 200s）→ 重采样只能拿到剩余 50s，**不是**完整上限."""
    clock = _Clock()
    monkeypatch.setattr(_time, "time", clock)
    trunc = _res(success=False, text="", truncated=True)
    trunc.error = "截断"
    svc = _Svc(clock, [trunc, _res()], advance_per_call=150.0)
    res = _run_audit(monkeypatch, svc, timeout_sec=300,
                     deadline_ts=clock.t + 200.0, min_useful_sec=10)

    assert len(svc.calls) == 2, "截断应重采样一次"
    assert svc.calls[0].timeout_sec == 200, svc.calls[0].timeout_sec
    assert svc.calls[1].timeout_sec == 50, (
        "重采样必须被整次审计的截止时刻限住，否则收尾预留形同虚设：%s"
        % svc.calls[1].timeout_sec)
    assert res.status == "ok"


def test_no_second_request_when_deadline_passed(monkeypatch):
    """首调就把额度耗到低于最小可用 → **不再发第二次**，记 `budget_exhausted`."""
    clock = _Clock()
    monkeypatch.setattr(_time, "time", clock)
    trunc = _res(success=False, text="", truncated=True)
    trunc.error = "截断"
    svc = _Svc(clock, [trunc], advance_per_call=150.0)
    res = _run_audit(monkeypatch, svc, deadline_ts=clock.t + 200.0)

    assert len(svc.calls) == 1, "剩余 50s < 最小可用 60s ⇒ 不得再发"
    assert res.status == "budget_exhausted", res.status
    assert res.attempts == 1


def test_no_deadline_means_old_behaviour(monkeypatch):
    """`deadline_ts=None`（无预算约束的调用方）→ 行为与旧版一致: 每次都拿满上限."""
    clock = _Clock()
    monkeypatch.setattr(_time, "time", clock)
    trunc = _res(success=False, text="", truncated=True)
    trunc.error = "截断"
    svc = _Svc(clock, [trunc, _res()], advance_per_call=250.0)
    res = _run_audit(monkeypatch, svc, timeout_sec=300, deadline_ts=None)

    assert [c.timeout_sec for c in svc.calls] == [300, 300]
    assert res.status == "ok"
