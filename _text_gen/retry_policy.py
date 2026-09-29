# -*- coding: utf-8 -*-
"""文本调用的**失败分类与重试预算**（单一来源，2026-09-28 复核决定 N6）.

## 为什么需要

此前四家 provider 各写各的重试：agent_plan 1 次退避、agnes 走
`AGNES_TEXT_SUBMIT_RETRIES`、deepseek 显式钉 0（v6.118：SDK 默认 2 会让一次抖动
变成三次提交）、dashscope 完全没有；**判定（哪些错可重试）也散在各自的
`except` 分支里**。后果有两类：

1. 同一份上层代码换 provider 后语义反转（「传输抖动能否自愈」随 provider 变）；
2. 上层无法判断「这个失败该不该由我再试一次」——`request_failed` 不重试、
   `parse_failed` 重试 1 次（审计层）这两条既有策略只能靠注释维持。

## 分类（本模块是唯一判定处）

| 类别 | 含义 | 谁重试 |
|---|---|---|
| `TRANSPORT_CONNECT` | 连接层失败（reset/refused/DNS）——请求**大概率没跑到**模型侧 | 统一层（provider 内） |
| `TRANSPORT_TIMEOUT` | **读超时**——旧请求可能仍在模型侧跑 | **不重试**（重发 = 双份长任务） |
| `SERVER_ERROR` | 5xx | 统一层（provider 内） |
| `RATE_LIMIT` | 429 | provider 按 `retry-after` 等待（等待语义特殊，仍由 provider 处理） |
| `TRUNCATION` | `finish_reason=length` / max_output_tokens | **不重试**：同请求同样会截断，交上层决策 |
| `PARSE` | 输出不符结构化契约 | **不重试**：由调用点决定（审计层有 1 次重采样） |
| `FATAL` | 401/403/400、额度耗尽 | **不重试**（重试无意义） |

## 预算（共享）

`TEXT_TRANSPORT_RETRIES`（默认 1 = 初次 + 1 次重试 = 共 2 次提交，与 agent_plan
既有行为一致）、`TEXT_TRANSPORT_BACKOFF_SEC`（默认 2，指数退避）、
`TEXT_TRANSPORT_BUDGET_SEC`（默认 30：**整次提交含重试**的总时间预算，
耗尽即停并报最后一次错误）。

## 上层不得叠加

统一层已耗尽重试仍失败 = 该类失败**已经尽力**。上层（修复轮 / 审计 / GAP）
只允许对 **`PARSE`** 这类「非传输」失败做自己的有界重采样（审计
`AUDIT_PARSE_RETRIES`），**不得**对 `TRANSPORT_*` 再叠一层重试
（2026-09-28 run 08 实测：provider 重试已耗尽，修复轮直接按失败终局）。
"""
from __future__ import annotations

import os
from enum import Enum

ENV_RETRIES = "TEXT_TRANSPORT_RETRIES"
#: 只作用于 `TRANSPORT_CONNECT` 的**额外**尝试次数（默认 1 → 共 3 次提交）
ENV_RETRIES_CONNECT = "TEXT_TRANSPORT_CONNECT_RETRIES"
_CONNECT_EXTRA_ATTEMPTS = 1
ENV_BACKOFF = "TEXT_TRANSPORT_BACKOFF_SEC"
ENV_BUDGET = "TEXT_TRANSPORT_BUDGET_SEC"

#: 可重试的连接类 HTTP 状态（5xx 见 `classify_status`）
_RETRYABLE_STATUS = (500, 502, 503, 504, 520, 522, 524)
#: 鉴权/参数类：重试无意义
_FATAL_STATUS = (400, 401, 402, 403, 404, 422)


class FailureClass(str, Enum):
    TRANSPORT_CONNECT = "transport_connect"
    TRANSPORT_TIMEOUT = "transport_timeout"
    SERVER_ERROR = "server_error"
    RATE_LIMIT = "rate_limit"
    TRUNCATION = "truncation"
    PARSE = "parse"
    FATAL = "fatal"
    UNKNOWN = "unknown"


#: 由**统一层**（provider 内）重试的类别——顺序即优先级
RETRYABLE = frozenset({FailureClass.TRANSPORT_CONNECT, FailureClass.SERVER_ERROR})
#: 明确不可重试的类别（含「重试必然同样失败」与「重发有副作用」两类）
NOT_RETRYABLE = frozenset({FailureClass.TRANSPORT_TIMEOUT, FailureClass.TRUNCATION,
                           FailureClass.PARSE, FailureClass.FATAL})


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        v = float((os.getenv(name) or "").strip() or default)
    except (TypeError, ValueError):
        v = default
    return max(minimum, v)


def submit_attempts(failure=None) -> int:
    """初次提交 + 重试的总次数（≥1）。默认 2 = 与 agent_plan 既有行为一致.

    v6.149（复核建议 + 故障注入验证）：**只给 `TRANSPORT_CONNECT` 多一次**（默认
    共 3 次）。依据：2026-09-29 一天内 8 次连接重置、其中 2 次直接判死整跑，而失败
    时预算只用了 12.0s / 30s——**先耗尽的是次数，不是预算**。连接层失败「请求大概率
    没跑到模型侧」，重发代价低；读超时/截断/解析**不放宽**（重发要么双份长任务、
    要么同样结果）。可用 `TEXT_TRANSPORT_CONNECT_RETRIES` 单独覆盖（0 = 回落共 2 次）。
    """
    try:
        n = int(float((os.getenv(ENV_RETRIES) or "").strip() or 1))
    except (TypeError, ValueError):
        n = 1
    caps = max(0, n) + 1
    if failure == FailureClass.TRANSPORT_CONNECT:
        try:
            extra = int(float((os.getenv(ENV_RETRIES_CONNECT) or "").strip()
                              or _CONNECT_EXTRA_ATTEMPTS))
        except (TypeError, ValueError):
            extra = _CONNECT_EXTRA_ATTEMPTS
        caps += max(0, extra)
    return caps


def submit_backoff_sec(attempt: int) -> float:
    """第 `attempt` 次重试前的等待（1-based 重试序号）；指数退避，上限 30s."""
    base = _env_float(ENV_BACKOFF, 2.0)
    return min(30.0, base * (2 ** max(0, attempt - 1)))


def submit_budget_sec() -> float:
    """整次提交（含全部重试）的时间预算；0 = 不限（不推荐，长尾会拖住整轮）."""
    return _env_float(ENV_BUDGET, 30.0, minimum=0.0)


def classify_status(status_code: int) -> FailureClass:
    if status_code == 429:
        return FailureClass.RATE_LIMIT
    if status_code in _RETRYABLE_STATUS:
        return FailureClass.SERVER_ERROR
    if status_code in _FATAL_STATUS:
        return FailureClass.FATAL
    if 500 <= int(status_code) < 600:
        return FailureClass.SERVER_ERROR
    return FailureClass.UNKNOWN


def classify_exception(exc: BaseException) -> FailureClass:
    """按异常类型分类——**读超时与连接失败必须分开**（前者不许重发）."""
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "timeout" in name or "timed out" in text or "read timeout" in text:
        return FailureClass.TRANSPORT_TIMEOUT
    if any(k in name for k in ("connection", "connect")) or any(
            k in text for k in ("connection reset", "connection aborted",
                                "connection refused", "remote end closed",
                                "max retries exceeded", "temporary failure in name",
                                "name or service not known")):
        return FailureClass.TRANSPORT_CONNECT
    return FailureClass.UNKNOWN


def should_retry(failure: FailureClass, *, attempts_used: int,
                 elapsed_sec: float) -> bool:
    """统一层的唯一重试判据：类别可重试 **且** 次数与时间预算都还有余量."""
    if failure not in RETRYABLE:
        return False
    if attempts_used >= submit_attempts(failure):
        return False
    budget = submit_budget_sec()
    if budget > 0 and elapsed_sec >= budget:
        return False
    return True
