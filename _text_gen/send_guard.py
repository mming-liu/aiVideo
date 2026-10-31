# -*- coding: utf-8 -*-
"""**发送守卫**的唯一绑定点（M07，2026-10-29）.

守卫属于**本地运行控制**：它决定「这次逻辑调用允不允许进入外部 SDK」，
绝不能混进发给模型的 prompt 或 API 参数。所以它只走请求对象上的一个本地运行字段
（`TextGenerationRequest.send_guard`），provider 的发送闭包显式持有它。

为什么要有这一层而不是让 provider 直接 import 授权模块：

  · provider 是 provider-neutral 的（四家共用）；授权状态存在哪、怎么记是审计入口的
    事，provider 只认「发送前问一句、返回后记一句」这两个动作。
  · **不依赖 ContextVar 自动跨线程传播**：`TransportGate` 会在工作线程里真正发送，
    ContextVar 在线程池/新线程里不保证按预期可见。因此由
    `_LedgeredService.generate()` 在**当前线程**取到守卫，连同 `call_id` 一起显式
    绑到请求上，再由 provider 在发送闭包里消费。

未绑定守卫时（普通生成入口），`current_send_guard()` 返回 `None`，provider 直接发送，
行为与改造前逐字一致。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

#: 当前作用域绑定的守卫；默认 `None` = 旧行为（不拦截、不记账）.
_ACTIVE_GUARD: ContextVar[object | None] = ContextVar("text_send_guard", default=None)


class BoundSendGuard:
    """把**守卫**与这一次逻辑调用的 `call_id` 绑在一起（随请求进入发送线程）.

    一个逻辑调用一个实例：同一个请求对象被复用/重试时，attempt 仍能归到正确的 call。
    """

    __slots__ = ("_guard", "call_id")

    def __init__(self, guard, call_id: str) -> None:
        self._guard = guard
        self.call_id = str(call_id or "")

    def before_send(self, *, provider: str, model: str, endpoint: str) -> str:
        """发送前调用；返回 `attempt_id`，抛错 ⇒ **不许发送**."""
        return self._guard.before_send(provider=provider, model=model,
                                       endpoint=endpoint, call_id=self.call_id)

    def record_send_outcome(self, attempt_id: str, outcome: str,
                            metadata: "dict | None" = None) -> str:
        """发送后记录结果；实现方不得因为它失败而重发."""
        return self._guard.record_send_outcome(attempt_id, outcome, metadata)


@contextmanager
def bind_send_guard(guard):
    """在**当前作用域**绑定守卫（冻结候选入口在调用提交函数期间使用）."""
    token = _ACTIVE_GUARD.set(guard)
    try:
        yield guard
    finally:
        _ACTIVE_GUARD.reset(token)


def current_send_guard():
    """当前作用域的守卫；`None` ⇒ 不拦截（普通生成入口）."""
    return _ACTIVE_GUARD.get()


def bind_request(req, call_id: str):
    """把当前守卫**显式**绑到请求上；没有守卫时原样返回（零行为变化）.

    用 `dataclasses.replace` 生成一份浅拷贝，避免把守卫写到调用方共享的请求对象上
    （同一请求被并发/复用时会串号）。
    """
    guard = _ACTIVE_GUARD.get()
    if guard is None:
        return req
    from dataclasses import replace
    try:
        return replace(req, send_guard=BoundSendGuard(guard, call_id))
    except TypeError:
        # 非 dataclass 请求（测试替身等）：退回属性赋值，保持可用但不改协议
        try:
            req.send_guard = BoundSendGuard(guard, call_id)          # type: ignore[attr-defined]
        except Exception:                                            # noqa: BLE001
            pass
        return req


__all__ = ["BoundSendGuard", "bind_request", "bind_send_guard", "current_send_guard"]
