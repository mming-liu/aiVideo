# -*- coding: utf-8 -*-
"""统一 HTTP 传输韧性层（2026-11-28 O4）.

## 为什么要有它

`_text_gen/agent_plan_provider.py`、`_image_gen/volcengine_provider.py`、
`_video_gen/volcengine_provider.py`、`_video_gen/agent_plan_provider.py`、
`_video_gen/minimax_provider.py` 各自维护重试循环、退避参数、SSL 处理与状态码分类：
口径不统一，同一类故障在不同通道得到不同结论，也无法集中测透。

本模块只做**三件确定的事**（不猜、不吞、不改变对外异常类型）：

1. **分类**：把异常/状态码判成 `RETRYABLE_TRANSPORT` / `RETRYABLE_HTTP` /
   `NON_RETRYABLE_CLIENT` 三档；内容审核拒收单独识别（0 重试，交调用方按业务处理）。
2. **脏连接池自愈**：`SSLError` / `ConnectionResetError` 先 `close()` 丢弃半开连接池，
   **下一次**尝试用新会话；退避 = 指数 + 抖动（可注入 sleep 便于测试）。
3. **记账**：每次底层尝试前调用 `shared.outbound_guard.charge_attempt(phase)`
   （未安装守卫时是 no-op，零行为变化），触顶由守卫抛 `SendBlocked`（BaseException）。

## 明确的边界

- 本模块**不**决定业务语义：返回的 `Response` 原样交给调用方，状态码分类只决定
  「要不要重试」，不把 4xx 变成异常。
- 本模块**不**自己维护第二套账本：重试次数只由 `max_retries` 控制，守卫只管预算。
- 接入是**渐进**的：未改动的调用方保持原有重试逻辑。
"""
from __future__ import annotations

import enum
import random
import time
from typing import Callable

#: 可重试的 HTTP 状态码（服务端瞬时故障 / 限流）
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
#: 不可重试的客户端错误（参数/鉴权/不存在）——重试只会浪费额度
NON_RETRYABLE_STATUS = frozenset({400, 401, 403, 404, 405, 409, 422})

#: 内容审核拒收的稳定标记（各供应商的错误码/文案；命中即 0 重试）
_CONTENT_REJECT_MARKERS = (
    "outputimagesensitivecontentdetected",
    "inputimagesensitivecontentdetected",
    "datainspectionfailed",
    "sensitivecontent",
    "content_policy",
    "content policy",
)


class ErrorClass(str, enum.Enum):
    """一次发送结果的分类（决定是否重试）."""

    RETRYABLE_TRANSPORT = "retryable_transport"
    RETRYABLE_HTTP = "retryable_http"
    NON_RETRYABLE_CLIENT = "non_retryable_client"
    CONTENT_REJECTED = "content_rejected"
    OK = "ok"
    OTHER = "other"


def is_content_rejection(body) -> bool:
    """响应体/错误文案是否命中内容审核拒收标记."""
    text = str(body or "").lower()
    return any(marker in text for marker in _CONTENT_REJECT_MARKERS)


def classify_status(status_code) -> ErrorClass:
    """HTTP 状态码 → 分类（未知状态码归 OTHER，交由调用方处理）."""
    try:
        code = int(status_code)
    except (TypeError, ValueError):
        return ErrorClass.OTHER
    if 200 <= code < 300:
        return ErrorClass.OK
    if code in RETRYABLE_STATUS:
        return ErrorClass.RETRYABLE_HTTP
    if code in NON_RETRYABLE_STATUS:
        return ErrorClass.NON_RETRYABLE_CLIENT
    if 400 <= code < 500:
        return ErrorClass.NON_RETRYABLE_CLIENT
    return ErrorClass.OTHER


def classify_exception(exc: BaseException) -> ErrorClass:
    """异常 → 分类（按异常类型名与文案双重判定，避免依赖具体 SDK 异常类）."""
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if "ssl" in name or "ssl" in text:
        return ErrorClass.RETRYABLE_TRANSPORT
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return ErrorClass.RETRYABLE_TRANSPORT
    if any(k in name for k in ("connection", "connect", "timeout", "timeoutproxy")):
        return ErrorClass.RETRYABLE_TRANSPORT
    if any(k in text for k in ("connection reset", "connection aborted",
                               "remote end closed", "max retries exceeded",
                               "temporary failure", "timed out")):
        return ErrorClass.RETRYABLE_TRANSPORT
    return ErrorClass.OTHER


class ManagedSession:
    """带分类重试与脏连接池重建的会话包装（离线可测：`transport` 可注入）.

    Args:
        transport: `(url, **kw) -> response` 的发送函数；None 时用内部
            `requests.Session().post`（保持连接池复用）。
        phase: 记账阶段名（`image_gen` / `video_gen` / `vision_qc` / `tts` / `other`）。
        max_retries: 首次之外的最大重试次数（0 = 只发一次）。
        backoff_base / backoff_max: 指数退避的基数与上限（秒）。
        jitter: 抖动比例（0 = 无抖动，测试用）。
        sleep: 休眠函数（测试注入，默认 `time.sleep`）。
        session_factory: 建会话的工厂（默认 `requests.Session`；测试注入计数用）。
    """

    def __init__(self, *, transport: Callable | None = None, phase: str = "other",
                 max_retries: int = 2, backoff_base: float = 0.5,
                 backoff_max: float = 8.0, jitter: float = 0.25,
                 sleep: Callable[[float], None] = time.sleep,
                 session_factory: Callable | None = None):
        self._transport = transport
        self.phase = str(phase or "other")
        self.max_retries = max(0, int(max_retries))
        self.backoff_base = float(backoff_base)
        self.backoff_max = float(backoff_max)
        self.jitter = max(0.0, float(jitter))
        self._sleep = sleep
        self._session_factory = session_factory
        self._session = None
        self._session_dirty = False
        #: 观测指标（测试与报表消费）
        self.attempts = 0
        self.rebuilds = 0
        self.retries = 0
        self.last_error_class: ErrorClass = ErrorClass.OK

    # ---- 会话 ------------------------------------------------------------
    def _get_session(self):
        if self._session_factory is None:
            import requests
            self._session_factory = requests.Session
        if self._session is None or self._session_dirty:
            if self._session is not None:
                try:
                    self._session.close()
                except Exception:
                    pass
                self.rebuilds += 1
            self._session = self._session_factory()
            self._session_dirty = False
        return self._session

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            finally:
                self._session = None
                self._session_dirty = False

    def _mark_dirty(self) -> None:
        self._session_dirty = True

    # ---- 退避 ------------------------------------------------------------
    def backoff_delay(self, attempt_index: int, retry_after=None) -> float:
        """第 `attempt_index` 次重试前的等待（优先 `Retry-After`，否则指数+抖动）."""
        if retry_after is not None:
            try:
                return max(0.0, float(retry_after))
            except (TypeError, ValueError):
                pass
        delay = self.backoff_base * (2 ** max(0, attempt_index))
        delay = min(delay, self.backoff_max)
        if self.jitter:
            delay += delay * self.jitter * random.random()
        return delay

    # ---- 发送 ------------------------------------------------------------
    def post(self, url: str, **kw):
        return self.request("POST", url, **kw)

    def request(self, method: str, url: str, **kw):
        from shared import outbound_guard

        last_exc: BaseException | None = None
        for attempt in range(self.max_retries + 1):
            # 每层尝试都在**发出字节之前**记账（未安装守卫 = no-op；触顶抛 SendBlocked）
            outbound_guard.charge_attempt(self.phase)
            self.attempts += 1
            try:
                if self._transport is not None:
                    resp = self._transport(url, method=method, **kw)
                else:
                    session = self._get_session()
                    resp = session.request(method, url, **kw)
            except BaseException as exc:                     # noqa: BLE001 - 分类后原样抛出
                last_exc = exc
                cls = classify_exception(exc)
                self.last_error_class = cls
                if cls is ErrorClass.RETRYABLE_TRANSPORT:
                    self._mark_dirty()          # 半开连接池不再复用
                    if attempt < self.max_retries:
                        self.retries += 1
                        self._sleep(self.backoff_delay(attempt))
                        continue
                raise
            cls = classify_status(getattr(resp, "status_code", None))
            self.last_error_class = cls
            if cls is ErrorClass.RETRYABLE_HTTP and attempt < self.max_retries:
                self.retries += 1
                self._sleep(self.backoff_delay(attempt, self._retry_after(resp)))
                continue
            return resp
        raise last_exc if last_exc is not None else RuntimeError("unreachable")

    @staticmethod
    def _retry_after(resp):
        headers = getattr(resp, "headers", None) or {}
        raw = headers.get("Retry-After") or headers.get("retry-after")
        if raw is None:
            return None
        try:
            return max(0.0, float(str(raw).strip()))
        except (TypeError, ValueError):
            return None


__all__ = ["ErrorClass", "ManagedSession", "NON_RETRYABLE_STATUS",
           "RETRYABLE_STATUS", "classify_exception", "classify_status",
           "is_content_rejection"]
