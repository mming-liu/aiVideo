# -*- coding: utf-8 -*-
"""发送边界预算（Step01 固定输入运行器 / 冻结恢复共用）.

**兼容外壳**：实现已统一到 `shared/outbound_guard.OutboundGuard`（M2.1）。
本模块只保留既有名字与构造签名，避免运行器/恢复入口/单测同时改动：

* `CallCounter(transport, cap=…, log_dir=…, hook_cap=…)` —— 旧的"只数尝试"视图；
* `SendBudgetExceeded` —— 与 `SendBlocked` 是**同一个类**（`except` 照旧命中）；
* `is_hook_request` —— 钩子请求分类（提示词标记，不靠 URL/顺序猜）。

新代码请直接用 `shared.outbound_guard.OutboundGuard`：它同时提供
`logical_calls` / `http_attempts` / `responses_received` 三指标，并在
`begin()` 处就拦住逻辑调用层。
"""
from __future__ import annotations

from pathlib import Path

from shared.outbound_guard import (  # noqa: F401  (re-export)
    OutboundGuard, SendBlocked, is_hook_request, phase_of_schema,
)

#: 旧名字 = 同一个异常类（不是子类）：`except SendBudgetExceeded` 必须能接住
#: `OutboundGuard` 抛出的阻断，否则预算耗尽会被当成普通异常继续重试。
SendBudgetExceeded = SendBlocked


class CallCounter:
    """**旧视图**：`attempts` / `responses_received` / 钩子分类计数.

    委托给 `OutboundGuard`：`cap` 是**总尝试上限**（含 provider 内部重试），
    `hook_cap` 是钩子分类的尝试上限。逻辑调用层请改用 `OutboundGuard.begin()`。
    """

    def __init__(self, transport, *, cap, log_dir, hook_cap=None):
        self.transport = transport
        self.cap = int(cap)
        self.hook_cap = None if hook_cap is None else int(hook_cap)
        self.log_dir = Path(log_dir)
        self._guard = OutboundGuard(
            transport=transport, total_attempts=self.cap,
            phase_attempts=({"hook": self.hook_cap} if self.hook_cap is not None else None),
            log_dir=self.log_dir)

    # ---- 发送边界 --------------------------------------------------------
    def __call__(self, url, **kw):
        return self._guard(url, **kw)

    # ---- 旧读数（对外契约保持不变） --------------------------------------
    @property
    def attempts(self) -> int:
        return sum(int(v or 0) for v in self._guard.http_attempts.values())

    @property
    def responses_received(self) -> int:
        return sum(int(v or 0) for v in self._guard.responses_received.values())

    @property
    def hook_attempts(self) -> int:
        return int(self._guard.http_attempts.get("hook") or 0)

    @property
    def hook_responses(self) -> int:
        return int(self._guard.responses_received.get("hook") or 0)

    @property
    def blocked(self) -> int:
        return int(self._guard.blocked)

    @property
    def hook_blocked(self) -> int:
        return int(self._guard.blocked_by_phase.get("hook") or 0)

    @property
    def calls(self) -> list:
        return self._guard.calls

    # ---- 新视图（三指标 + 逻辑层） ---------------------------------------
    @property
    def guard(self) -> OutboundGuard:
        """底层守卫（供运行器 `install()`，让逻辑调用层真正生效）."""
        return self._guard

    def snapshot(self) -> dict:
        return self._guard.snapshot()

    def begin(self, phase: str = "other") -> str:
        return self._guard.begin(phase)

    def remaining_logical(self, phase=None):
        return self._guard.remaining_logical(phase)

    def remaining_attempts(self):
        return self._guard.remaining_attempts()


__all__ = ["CallCounter", "OutboundGuard", "SendBlocked", "SendBudgetExceeded",
           "is_hook_request", "phase_of_schema"]
