"""shared.key_scheduler - 多 API Key 冷却调度器

Agnes 免费档限制按 key 独立计算（图片约 20s/次，视频约 60s/次）。
N 个 key 做冷却轮询可把提交吞吐提升 N 倍：

- acquire():  阻塞直到某个 key 冷却结束，返回 key 下标并立即预占下一轮冷却
- penalize(): 429 时按服务端 retry-after 精确延长该 key 的冷却
- disable():  401/402（key 失效/欠费）时永久拉黑该 key

线程安全：provider 的请求在 asyncio.to_thread 工作线程中并发执行，
因此用 threading.Lock + time.sleep（锁外睡眠，醒后重查竞态）。
"""
import threading
import time


class KeyScheduler:
    """按 key 独立冷却的轮询调度器."""

    def __init__(self, n_keys: int, cooldown: float, name: str = "keys"):
        if n_keys < 1:
            raise ValueError("n_keys 必须 >= 1")
        self._n = n_keys
        self._cooldown = cooldown
        self._name = name
        self._next_free = [0.0] * n_keys  # 各 key 下次可用的 monotonic 时间戳
        self._disabled: set[int] = set()
        self._lock = threading.Lock()

    def acquire(self) -> int:
        """阻塞直到有 key 可用，返回 key 下标（返回时已预占本轮冷却）."""
        while True:
            with self._lock:
                now = time.monotonic()
                avail = [
                    (t, i) for i, t in enumerate(self._next_free)
                    if i not in self._disabled
                ]
                if not avail:
                    raise RuntimeError(
                        f"[{self._name}] 所有 API Key 均已禁用 (401/402)"
                    )
                t, i = min(avail)
                if t <= now:
                    self._next_free[i] = now + self._cooldown
                    return i
                wait = t - now
            # 锁外睡眠；封顶 5s，醒后重查（其他线程可能已抢走或惩罚了 key）
            time.sleep(min(wait, 5.0))

    def penalize(self, idx: int, seconds: float) -> None:
        """429 限流：把该 key 的冷却延长到 now + seconds（只延不缩）."""
        with self._lock:
            self._next_free[idx] = max(
                self._next_free[idx], time.monotonic() + seconds
            )

    def disable(self, idx: int) -> None:
        """401/402：永久拉黑该 key（本次进程内）."""
        with self._lock:
            if idx not in self._disabled:
                self._disabled.add(idx)
                print(f"  [{self._name}] Key {idx + 1} 已禁用，"
                      f"剩余可用 {self._n - len(self._disabled)} 个")

    def n_active(self) -> int:
        with self._lock:
            return self._n - len(self._disabled)
