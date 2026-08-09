"""shared.rate_limit - 429 限流统一决策（收敛各 provider 的重复实现）.

审计（2026-08-09 P0-provider-1）：429 处理此前在 4 个 agnes/bailian provider +
submit_poll 各自实现——每处都写「parse_retry_after → 读不到回退 → penalize/等待」，
且视频 bailian/dashscope 完全没有 429 处理（限流下必然失败）。

本模块收敛「读 retry-after 并决策等待秒数」这一重复点：
- compute_429_wait(err, fallback_seconds)：优先服务端 retry-after，读不到回退默认
- rate_limit_budget(window_seconds, backoff)：RPM→冷却窗口 + 缓冲的确定性计算

各 provider 保留自己的调度架构（KeyScheduler penalize vs 局部等待是设计选择），
但「读 retry-after 决策秒数」统一走本模块，消除每处手写 parse_retry_after + 兜底。
"""

from __future__ import annotations

from shared.error_fmt import parse_retry_after


def compute_429_wait(err, fallback_seconds: float, backoff_seconds: float = 0.5) -> float:
    """计算 429 后应等待的秒数.

    Args:
        err: 429 错误（响应体文本 / 异常对象 / Retry-After 头值）
        fallback_seconds: 读不到 retry-after 时的默认冷却窗口（如 RPM 推导值）
        backoff_seconds: 窗口末尾额外缓冲（网络抖动/时钟偏差）

    Returns:
        至少 1s + backoff 的等待秒数（避免瞬时限流下 0 等待狂发）。
    """
    wait = parse_retry_after(err)
    if wait is None:
        wait = float(fallback_seconds or 0)
    return max(wait, 1.0) + backoff_seconds


def rate_limit_window(rpm: float | None, min_seconds: float = 5.0) -> float:
    """由 RPM 推导 429 冷却窗口（秒）.

    Args:
        rpm: 每分钟请求数；None/<=0 时回退 60s（保守 RPM=1）
        min_seconds: 窗口下限（高并发下避免瞬时限流 0 等待）

    Returns:
        冷却窗口秒数（>= min_seconds）
    """
    if not rpm or rpm <= 0:
        return 60.0
    return max(60.0 / rpm, min_seconds)