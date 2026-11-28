# -*- coding: utf-8 -*-
"""N6：文本调用的失败分类与重试预算（**单一来源**）+ 上层不得叠加重试.

2026-09-28 复核决定的背景：
  - 四家 provider 各写各的重试（agent_plan 1 次退避 / agnes 走自己的常量 /
    deepseek 显式钉 0 / dashscope 没有），**判定也散在各自的 except 分支里**
    → 换 provider 语义就反转，上层无从判断该不该再叠一层；
  - run 08 实测：provider 重试已耗尽，修复轮仍按失败终局——这条**不能**变成
    「上层无条件再加一次重试」。

## 本文件锁住

1. 分类表：可重试（连接类、5xx）与不可重试（读超时、截断、解析、鉴权类）分明；
2. 读超时**不重试**（旧请求仍在模型侧跑，重发 = 双份长任务）；
3. 次数、退避、时间预算从**一处**取（环境变量），预算耗尽即停；
4. 审计层只对 `parse_failed` 重采样一次，**传输类失败一次都不重试**。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_classification_separates_timeout_from_connect():
    import requests

    from _text_gen import retry_policy as rp

    assert rp.classify_exception(
        requests.exceptions.ConnectionError("connection aborted")) \
        is rp.FailureClass.TRANSPORT_CONNECT
    assert rp.classify_exception(
        requests.exceptions.ReadTimeout("read timeout")) \
        is rp.FailureClass.TRANSPORT_TIMEOUT
    # 连接超时按保守侧处置（不重发长任务）——与既有 agent_plan 行为一致
    assert rp.classify_exception(
        requests.exceptions.ConnectTimeout("connect timeout")) \
        is rp.FailureClass.TRANSPORT_TIMEOUT


def test_status_classification_table():
    from _text_gen import retry_policy as rp

    assert rp.classify_status(500) is rp.FailureClass.SERVER_ERROR
    assert rp.classify_status(503) is rp.FailureClass.SERVER_ERROR
    assert rp.classify_status(429) is rp.FailureClass.RATE_LIMIT
    for code in (400, 401, 402, 403, 422):
        assert rp.classify_status(code) is rp.FailureClass.FATAL, code


def test_retryable_and_not_retryable_sets_are_disjoint_and_explicit():
    from _text_gen import retry_policy as rp

    assert rp.RETRYABLE == {rp.FailureClass.TRANSPORT_CONNECT,
                            rp.FailureClass.SERVER_ERROR}
    assert not (rp.RETRYABLE & rp.NOT_RETRYABLE)
    # 「重发有副作用」与「重发必然同样失败」两类都必须在不可重试一侧
    for fc in (rp.FailureClass.TRANSPORT_TIMEOUT, rp.FailureClass.TRUNCATION,
               rp.FailureClass.PARSE, rp.FailureClass.FATAL):
        assert rp.should_retry(fc, attempts_used=1, elapsed_sec=0.1) is False


def test_attempts_backoff_and_budget_come_from_one_place(monkeypatch):
    from _text_gen import retry_policy as rp

    monkeypatch.delenv(rp.ENV_RETRIES, raising=False)
    monkeypatch.delenv(rp.ENV_BACKOFF, raising=False)
    monkeypatch.delenv(rp.ENV_BUDGET, raising=False)
    assert rp.submit_attempts() == 2          # 初次 + 1 次重试（既有行为）
    assert rp.submit_backoff_sec(1) == 2.0
    assert rp.submit_backoff_sec(3) == 8.0    # 指数退避
    assert rp.submit_backoff_sec(9) == 30.0   # 封顶
    assert rp.submit_budget_sec() == 30.0

    monkeypatch.setenv(rp.ENV_RETRIES, "0")   # 关掉重试 = 只提交一次
    assert rp.submit_attempts() == 1
    monkeypatch.setenv(rp.ENV_RETRIES, "abc")  # 非法值回退默认，不静默关保护
    assert rp.submit_attempts() == 2


def test_budget_stops_retries_even_when_attempts_remain(monkeypatch):
    from _text_gen import retry_policy as rp

    monkeypatch.delenv(rp.ENV_BUDGET, raising=False)
    assert rp.should_retry(rp.FailureClass.TRANSPORT_CONNECT,
                           attempts_used=1, elapsed_sec=1.0) is True
    assert rp.should_retry(rp.FailureClass.TRANSPORT_CONNECT,
                           attempts_used=1, elapsed_sec=30.0) is False
    # 次数上限也要生效——连接类 v6.149 起是**共 3 次**（只这一类 +1），
    # 到顶即停；其它类别仍是共 2 次。
    assert rp.should_retry(rp.FailureClass.TRANSPORT_CONNECT,
                           attempts_used=3, elapsed_sec=1.0) is False
    assert rp.should_retry(rp.FailureClass.SERVER_ERROR,
                           attempts_used=2, elapsed_sec=1.0) is False


# --------------------------------------------------------------- 上层不叠加
class _FakeService:
    """按脚本返回：每次调用记数，返回预设结果."""

    def __init__(self, results, calls):
        self._results = list(results)
        self._calls = calls

    def generate(self, req):
        self._calls.append(req)
        return self._results[min(len(self._calls) - 1, len(self._results) - 1)]


class _Res:
    def __init__(self, *, success, text="", error="", usage=None):
        self.success = success
        self.text = text
        self.error = error
        self.usage = usage


def _audit(monkeypatch, results, calls):
    import step01_script_gen.audit as audit

    monkeypatch.setattr(audit, "create_service",
                        lambda *a, **k: _FakeService(results, calls))
    return audit.run_same_model_audit_with_status(
        "原文", {"beats": [], "dialogue_lines": []},
        {"shots": [], "characters": [], "hook": None}, model="m")


def test_audit_does_not_retry_transport_failure(monkeypatch):
    """传输类：provider 层已尽力 → 审计**一次都不重试**（run 08 的形态）."""
    calls: list = []
    res = _audit(monkeypatch,
                 [_Res(success=False, error="Connection error.")], calls)
    assert res.status == "request_failed"
    assert len(calls) == 1, "传输失败不得在审计层叠加重试，实际调用 %d 次" % len(calls)


def test_audit_resamples_on_truncation(monkeypatch):
    """截断（含思维链吃光配额、正文为空）与解析失败同性质：换一次采样就可能不截断.

    2026-09-28 实测 run 02：审计 `finish_reason=length`、正文为空 → 旧实现记
    `request_failed`（不可重试）→ 直接 BLOCK。截断必须走**同一个**有界预算。
    """
    monkeypatch.delenv("AUDIT_PARSE_RETRIES", raising=False)
    calls: list = []
    trunc = _Res(success=False, text="", error="输出被截断且正文为空（finish_reason=length）")
    trunc.truncated = True
    ok = _Res(success=True, text='{"issues": []}')
    res = _audit(monkeypatch, [trunc, ok], calls)
    assert len(calls) == 2, "截断应重采样一次"
    assert res.status == "ok"


def test_audit_truncation_after_budget_is_fail_closed(monkeypatch):
    monkeypatch.setenv("AUDIT_PARSE_RETRIES", "1")
    calls: list = []
    trunc = _Res(success=False, text="", error="输出被截断且正文为空（finish_reason=length）")
    trunc.truncated = True
    res = _audit(monkeypatch, [trunc], calls)      # 每次都截断
    assert res.status == "truncated", res.status
    assert len(calls) == 2, "预算用满（初次 + 1 次重采样）"
    assert res.failed_attempts and res.failed_attempts[0]["failure"] == "truncated"


def test_audit_retries_parse_failure_once(monkeypatch):
    """解析类：换采样有意义，允许 1 次（`AUDIT_PARSE_RETRIES`），且只对解析类."""
    monkeypatch.delenv("AUDIT_PARSE_RETRIES", raising=False)
    calls: list = []
    good = '{"issues": []}'
    res = _audit(monkeypatch,
                 [_Res(success=True, text="这不是 JSON"), _Res(success=True, text=good)],
                 calls)
    assert len(calls) == 2, "解析失败应重采样 1 次"
    assert res.status == "ok"
    assert res.attempts == 2


def test_parse_retry_count_is_bounded(monkeypatch):
    monkeypatch.setenv("AUDIT_PARSE_RETRIES", "0")
    calls: list = []
    res = _audit(monkeypatch, [_Res(success=True, text="仍不是 JSON")], calls)
    assert res.status == "parse_failed"
    assert len(calls) == 1, "AUDIT_PARSE_RETRIES=0 时连解析类也不重试"


# ---------------------------------------------------------------------------
# v6.149：连接重置**故障注入**（复核要求：先验证次数/退避/预算，再改默认值）
# ---------------------------------------------------------------------------


def test_connect_reset_fault_injection_attempts_backoff_budget(monkeypatch):
    """注入连续连接重置 → 次数 / 指数退避 / 30s 预算三条行为同时钉住.

    依据（2026-09-29 实测）：一天 8 次 `transport_connect`、其中 2 次直接判死整跑，
    而失败时预算只用了 **12.0s / 30s**——先耗尽的是**次数**不是预算。故只给
    `TRANSPORT_CONNECT` 多一次（共 3 次），其它类别不放宽。
    """
    from _text_gen import retry_policy as rp
    for k in (rp.ENV_RETRIES, rp.ENV_RETRIES_CONNECT, rp.ENV_BACKOFF, rp.ENV_BUDGET):
        monkeypatch.delenv(k, raising=False)

    fc = rp.FailureClass.TRANSPORT_CONNECT
    assert rp.submit_attempts(fc) == 3                                  # 连接类共 3 次
    assert rp.submit_attempts(rp.FailureClass.SERVER_ERROR) == 2        # 其它仍是 2 次
    assert rp.submit_backoff_sec(1) == 2 and rp.submit_backoff_sec(2) == 4

    # 模拟提交循环：每次提交耗 4s（实测 4~12s）且都连接重置
    sleeps, elapsed, attempt = [], 0.0, 1
    while True:
        elapsed += 4.0
        if not rp.should_retry(fc, attempts_used=attempt, elapsed_sec=elapsed):
            break
        _w = rp.submit_backoff_sec(attempt)
        sleeps.append(_w)
        elapsed += _w
        attempt += 1
    assert sleeps == [2, 4]              # 指数退避；第 3 次失败后不再退避
    assert attempt == 3                  # 共提交 3 次
    assert elapsed < rp.submit_budget_sec()

    # 预算先耗尽 → 立刻停（不再看次数）
    assert rp.should_retry(fc, attempts_used=1, elapsed_sec=29.0) is True
    assert rp.should_retry(fc, attempts_used=1, elapsed_sec=30.0) is False

    # **只放宽连接类**：其余类别即便次数与预算都有余量也不重试
    for other in (rp.FailureClass.TRANSPORT_TIMEOUT, rp.FailureClass.TRUNCATION,
                  rp.FailureClass.PARSE, rp.FailureClass.FATAL):
        assert rp.should_retry(other, attempts_used=1, elapsed_sec=1.0) is False


def test_connect_extra_attempts_env_override(monkeypatch):
    """连接类多出来的一次可单独覆盖（0 = 回落到共 2 次）."""
    from _text_gen import retry_policy as rp
    fc = rp.FailureClass.TRANSPORT_CONNECT
    monkeypatch.setenv(rp.ENV_RETRIES_CONNECT, "0")
    assert rp.submit_attempts(fc) == 2
    monkeypatch.setenv(rp.ENV_RETRIES_CONNECT, "2")
    assert rp.submit_attempts(fc) == 4
    monkeypatch.delenv(rp.ENV_RETRIES_CONNECT, raising=False)
    monkeypatch.setenv(rp.ENV_RETRIES, "3")
    assert rp.submit_attempts(fc) == 5          # 全局 3 + 连接类额外 1
    assert rp.submit_attempts(rp.FailureClass.SERVER_ERROR) == 4


# ---------------------------------------------------------------------------
# v6.151：审计截断的**重采样要换思考档位**（同请求重采等于同一枚硬币再抛）
# ---------------------------------------------------------------------------


def test_audit_truncation_retry_lowers_reasoning_effort(monkeypatch):
    """注入两次截断：断言 ①第 2 次调用换了更省的思考档位 ②尝试次数仍有界."""
    import step01_script_gen.audit as au
    from _text_gen import models as tmodels

    seen = []

    class _Svc:
        def generate(self, req):
            seen.append((req.reasoning_effort, req.max_tokens))
            return tmodels.TextGenerationResult(
                success=False, text="", error="截断（finish_reason=length）",
                truncated=True)

    monkeypatch.setattr(au, "create_service", lambda *a, **k: _Svc())
    monkeypatch.setenv("AUDIT_REASONING_EFFORT", "low")
    monkeypatch.setenv("AUDIT_PARSE_RETRIES", "1")
    monkeypatch.setenv("AUDIT_MAX_OUTPUT_TOKENS", "32000")
    monkeypatch.setenv("AUDIT_TRUNCATION_RETRY_MAX_TOKENS", "64000")

    res = au.run_same_model_audit_with_status(
        "原文", {}, {"shots": [], "characters": []},
        model="deepseek-v4.1-flash")
    assert res.status == "truncated"                 # fail-closed 不变
    assert [mt for _, mt in seen] == [32000, 64000]   # 配了才抬上限（默认不抬）
    assert len(seen) == 2                             # 有界：初次 + 1 次重采样
    # 只抬**重采样**那一次：首跑上限不动（避免每次审计都被拉长撞超时）


def test_truncation_retry_default_does_not_bump_the_cap(monkeypatch):
    """默认**不抬上限**：首跑已按实测放到 64000，重采样就是同请求再采一次."""
    import step01_script_gen.audit as au
    from _text_gen import models as tmodels

    seen = []

    class _Svc:
        def generate(self, req):
            seen.append(req.max_tokens)
            return tmodels.TextGenerationResult(
                success=False, text="", error="截断", truncated=True)

    monkeypatch.setattr(au, "create_service", lambda *a, **k: _Svc())
    monkeypatch.setenv("AUDIT_MAX_OUTPUT_TOKENS", "64000")
    monkeypatch.setenv("AUDIT_PARSE_RETRIES", "1")
    monkeypatch.delenv("AUDIT_TRUNCATION_RETRY_MAX_TOKENS", raising=False)

    res = au.run_same_model_audit_with_status(
        "原文", {}, {"shots": [], "characters": []}, model="deepseek-v4.1-flash")
    assert res.status == "truncated"
    assert seen == [64000, 64000]


# ---------------------------------------------------------------------------
# v6.152（D1）：审计计量行**成功也要打**
# ---------------------------------------------------------------------------


def test_audit_prints_metrics_on_success_too(monkeypatch, capsys):
    """成功路径也要有耗时/上限/输出/思维链——此前只有失败分支打，成功审计查不到.

    现场（2026-09-29）：run 01 的审计耗时 144s 只能靠 submit/response 两个时间戳
    反推，因为计量行写在 `if not result.success:` 里。
    """
    calls: list = []
    ok = _Res(success=True, text='{"issues": []}',
              usage={"output_tokens": 1234, "reasoning_tokens": 567})
    res = _audit(monkeypatch, [ok], calls)
    assert res.status == "ok"

    out = capsys.readouterr().out
    assert "[AUDIT] 第 1 次返回" in out, out
    assert "耗时" in out
    assert "output=1234" in out, out
    assert "思维链 567" in out, out
    assert "success=True" in out, out


def test_audit_still_prints_metrics_on_failure(monkeypatch, capsys):
    """失败路径的计量行不得因为上移而丢（它同时是重采样判据的输入）."""
    calls: list = []
    trunc = _Res(success=False, text="", error="输出被截断且正文为空",
                 usage={"output_tokens": 32000, "reasoning_tokens": 32000})
    trunc.truncated = True
    res = _audit(monkeypatch, [trunc, _Res(success=True, text='{"issues": []}')], calls)
    assert res.status == "ok"

    out = capsys.readouterr().out
    assert "truncated=True" in out, out
    assert "success=False" in out, out
    assert "output=32000" in out, out
