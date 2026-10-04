# -*- coding: utf-8 -*-
"""**绝对截止时刻**（monotonic）在整条文本通道上的单一表示（工作包 §4 B）.

为什么要有它（2026-10-01 工作包 B）：此前规划闭环只有"单次请求超时"这一个时间偏好，
而**排队、退避、每次 POST、Retry-After 等待、候选返回后的重验**各花各的时间——一次
规划调用可以既吃满单次超时、又吃满退避、再吃满重试预算，整次 Step 01 的剩余预算到了
哪里就没有人再看。工作包要求：**整次 Step 01 的剩余预算换算成一个绝对截止时刻**，
此后所有环节都按它重算余量。

规矩（每条都对应工作包 B 的一句要求）：

1. **只认 monotonic**。`Deadline` 存的是"到点"的 monotonic 时刻，不是秒数——秒数会在
   每一层被重新解释，绝对时刻不会。调用方用 `planning_deadline_at()` 换算**一次**。
2. **余量耗尽 ⇒ 直接失败**，不回退到 provider 默认超时（agent_plan 600s），也不因为
   "取整成 0" 而被当成"没配置"（`MIN_SLICE_SEC` 以下一律拒绝发送）。
3. **请求级 `timeout_sec` 仍是偏好**：真正生效的超时 = `min(偏好, 余量)`（`TransportGate.limit`）。
4. **等待有界**：`TransportGate.request()` 用**墙钟**等一次传输——socket 的 read timeout
   只在两次收包之间计时，**不等于**整请求墙钟上限；超时只说明"没等到"，**不宣称远端已取消**
   （迟到的响应没有采纳、写盘或重发的权限）。
5. **没有 deadline 的旧请求保持原行为**（`deadline_monotonic=None` ⇒ 与改造前逐字一致）。
"""
from __future__ import annotations

import threading
import time
from typing import Optional

#: 余量小于它就算耗尽——避免把 1e-9 秒当成"还有时间"再发一次请求
MIN_SLICE_SEC = 0.001
#: 退避/Retry-After 的实际等待下限（比它更短的等待没有意义，直接判拒绝）
MIN_WAIT_SEC = 0.001
#: 真发生墙钟等待时给线程收尾留的余量（毫秒级，避免刚好卡在边界上抖动）
_JOIN_SLACK_SEC = 0.02


def planning_deadline_at(left_sec, cap_sec=None, *, clock=time.monotonic):
    """把**剩余预算**换算成一个绝对截止时刻（monotonic）——换算只做一次.

    `deadline = min(整次截止, 现在 + 本阶段上限)`（工作包 B）：`left_sec` 是整次 Step 01
    的剩余时间（`None` = 不限），`cap_sec` 是本阶段自己的上限（如修订的 600s）。
    两者都没有 ⇒ `None`（不设截止，行为同改造前）。
    """
    _now = float(clock())
    _cands = []
    if left_sec is not None:
        _cands.append(_now + max(0.0, float(left_sec)))
    if cap_sec is not None:
        _cands.append(_now + max(0.0, float(cap_sec)))
    return min(_cands) if _cands else None


class Deadline:
    """一个**绝对**截止时刻（monotonic）；`None` = 不设截止（旧行为）."""

    __slots__ = ("_at", "_clock", "why")

    def __init__(self, at=None, *, clock=time.monotonic, why: str = ""):
        self._at = None if at is None else float(at)
        self._clock = clock
        self.why = str(why or "")

    @classmethod
    def from_remaining(cls, seconds, *, clock=time.monotonic, why: str = ""):
        if seconds is None:
            return cls(None, clock=clock, why=why)
        return cls(float(clock()) + max(0.0, float(seconds)), clock=clock, why=why)

    def at(self) -> Optional[float]:
        return self._at

    def armed(self) -> bool:
        return self._at is not None

    def remaining(self) -> Optional[float]:
        if self._at is None:
            return None
        return max(0.0, self._at - float(self._clock()))

    def expired(self) -> bool:
        _r = self.remaining()
        return _r is not None and _r <= MIN_SLICE_SEC

    def limit(self, want) -> Optional[float]:
        """真正生效的上限：`min(偏好, 余量)`；未设截止 ⇒ 偏好原样（可能也是 `None`）."""
        _r = self.remaining()
        if _r is None:
            return None if want is None else float(want)
        if want is None:
            return _r
        return min(float(want), _r)

    def expiry_reason(self, where: str, *, want=None) -> str:
        _r = self.remaining()
        _left = "未知" if _r is None else "%.3fs" % _r
        _w = "" if want is None else "（本次原定上限 %gs）" % float(want)
        return ("[%s] 规划截止时刻已到（剩余 %s）%s⇒ **不发请求**（不退回默认超时）"
                % (where, _left, _w))

    def timeout_reason(self, where: str, *, limit: float) -> str:
        """**有界等待超时**的文案：只说"没等到"，不说"远端已取消"."""
        return ("[%s] 墙钟上限 %.3fs 内未返回 ⇒ 按失败处置；"
                "**未宣称远端已取消**，迟到的响应不会被采纳、写盘或重发"
                % (where, float(limit)))


def request_deadline(req, *, clock=time.monotonic) -> Deadline:
    """从请求对象取截止时刻（缺失/`None` ⇒ 不设截止，行为同改造前）."""
    return Deadline(getattr(req, "deadline_monotonic", None), clock=clock,
                    why="deadline_monotonic")


class TransportGate:
    """**一次请求**在传输层的时间闸门：排队前、退避前、每次 POST 前都重算余量.

    provider 的用法（四个通道一致）：

        _gate = TransportGate(req, default_timeout=..., where="agent_plan")
        ...
        _resp, _timed_out, _deny = _gate.request(lambda t: requests.post(url, timeout=t, ...))
        if _deny:            # 余量耗尽：**没有发送**
            return failure(_deny)
        if _timed_out:       # 发出去了但没在墙钟内回来：不重发、不采纳
            return failure(_gate.timeout_reason(limit=_limit))
        if not _gate.wait(backoff):   # 退避不许睡过截止时刻
            return failure(_gate.expiry_reason())
    """

    def __init__(self, req, *, default_timeout, where: str, clock=time.monotonic):
        self.deadline = request_deadline(req, clock=clock)
        self.where = str(where or "text")
        #: 请求级偏好（`timeout_sec`）；没有就取该通道自己的默认。
        #: `default_timeout=None` = 这个通道**本来就不传超时**（如 deepseek 走 SDK 默认）
        #: ——保持"没带截止时刻的旧请求行为不变"：不设截止时就把 `None` 原样交给传输层。
        _pref = getattr(req, "timeout_sec", None)
        self.want = (float(_pref) if _pref else
                     (None if default_timeout is None else float(default_timeout)))

    # -- 发请求前 ---------------------------------------------------------
    def limit(self) -> tuple:
        """`(生效上限, 拒绝原因)`——原因非空 ⇒ **不许发送**（余量耗尽）.

        上限保证 > 0 且不回退默认值：调用方直接把它交给传输层，不要再判真假。
        """
        _left = self.deadline.remaining()
        if _left is None:
            return self.want, ""
        if _left <= MIN_SLICE_SEC:
            return 0.0, self.deadline.expiry_reason(self.where, want=self.want)
        # 余量为 `None` 的偏好（该通道本就不传超时）⇒ 余量就是上限
        return (_left if self.want is None else min(self.want, _left)), ""

    def wait_budget(self) -> Optional[float]:
        """可等待的余量（给 `KeyScheduler.acquire(timeout=…)` 之类的排队用）."""
        return self.deadline.remaining()

    def wait(self, want_sec) -> bool:
        """退避/Retry-After 的**有界**等待：返回 `False` = 余量不够，**不许再发**."""
        _w = max(0.0, float(want_sec))
        _left = self.deadline.remaining()
        if _left is None:
            time.sleep(_w)
            return True
        if _left <= MIN_WAIT_SEC:
            return False
        if _w >= _left:
            return False
        time.sleep(_w)
        return not self.deadline.expired()

    def expiry_reason(self, *, want=None) -> str:
        return self.deadline.expiry_reason(self.where, want=want or self.want)

    def timeout_reason(self, *, limit: float) -> str:
        return self.deadline.timeout_reason(self.where, limit=limit)

    # -- 传输 -------------------------------------------------------------
    def request(self, send_fn):
        """在**有界墙钟**内发一次请求，并在**发送前、返回后**各核一次截止时刻.

        `send_fn(limit)` 由调用方给出（内部用 `limit` 作 socket 超时）。返回
        `(响应, 是否超时/迟到, 拒绝原因)`：

        - 拒绝原因非空 ⇒ **没有发送**（主线程或**工作线程**任一处发现余量已耗尽）；
        - 超时/迟到 ⇒ 失败处置：**不宣称远端已取消**，也不采纳迟到的响应；
        - 传输异常 ⇒ 原样抛给调用方（各 provider 既有的分类与重试逻辑不改变）。

        复核第 45 轮 P1（两处都实测过）：
        ① 上限此前只在**主线程**算一次，工作线程直接拿旧值发送——线程被延迟调度时，
           "已经超时"的调用方返回之后，工作线程**仍然发出了一次 POST**。现在工作线程
           在真正传输前**重算**上限，过期就直接不发。
        ② 线程结束后没有复核——响应只要落在 `_JOIN_SLACK_SEC` 收尾余量里，
           **迟到的响应照样按 `success=True` 返回**。收尾余量不是采纳余量：返回后
           再核一次，迟到的一律记失败。
        """
        _limit, _deny = self.limit()               # 主线程先判（快速失败 + 给 join 上界）
        if _deny:
            return None, False, _deny
        if _limit is None:
            # 无截止时刻（旧请求）：直接同步调用，行为与改造前逐字一致
            return send_fn(None), False, ""
        _box: dict = {}

        def _run():
            # **真正发送前**用当刻余量重算：主线程算出的值可能已经过期（调度延迟），
            # 也可能被这期间的等待吃掉了一部分（排队/退避之后才会走到这里）
            _fresh, _why = self.limit()
            if _why:
                _box["deny"] = _why + "（发送前复核：本次**未发出**）"
                return
            _box["limit"] = _fresh
            try:
                _box["r"] = send_fn(_fresh)        # 上限是**发送当刻**算出来的
            except BaseException as _e:            # noqa: BLE001 —— 原样带回调用方线程
                _box["e"] = _e

        _th = threading.Thread(target=_run, daemon=True)
        _th.start()
        _th.join(_limit + _JOIN_SLACK_SEC)
        if _box.get("deny"):
            return None, False, _box["deny"]
        _sent_limit = float(_box.get("limit") or _limit)
        if _th.is_alive():
            return None, True, self.timeout_reason(limit=_sent_limit)
        if "e" in _box:
            raise _box["e"]
        if self.deadline.expired():
            # 响应在截止时刻**之后**才到手 ⇒ 只有"记失败"的资格（复核第 45 轮 P1-②）
            return None, True, (self.deadline.timeout_reason(self.where, limit=_sent_limit)
                                + "；响应在截止时刻之后才返回，**不采纳**")
        return _box.get("r"), False, ""
