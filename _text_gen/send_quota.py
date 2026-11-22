# -*- coding: utf-8 -*-
"""**发送额度**：按次数封顶的本地运行控制（M07，2026-11-22）.

只在设置了 ``TEXT_SEND_QUOTA_FILE`` 时生效；未设置时 `reserve()` 直接返回 ``None``，
发送路径与改动前逐字一致。

额度文件由运行方（隔离实跑脚本）预先写好：``{"max": 40, "used": 0, "attempts": []}``。
每一次**实际 POST 之前**（含重试，每次重发都算一次）由 provider 的唯一发送出口调用
`reserve()`：

- 在跨进程 / 跨线程的独占锁内读出、校验、``used+1``、原子写回，然后才允许发送；
- ``used >= max`` ⇒ 抛 `SendQuotaExhausted`，POST 不会发生；
- 文件缺失、不可读、形状非法、锁拿不到 ⇒ 抛 `SendQuotaUnavailable`（fail-closed）。

只计「发出去之前占到额度」的次数，不依赖调用台账（台账在调用结束后才写，且一次
逻辑调用内部可能有多次重发）。收尾时写本地记录不经过这里，不受额度影响。
异常名不含 timeout / connect，`retry_policy.classify_exception` 归为 UNKNOWN，不会被当作瞬态重试。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

ENV_FILE = "TEXT_SEND_QUOTA_FILE"
_LOCK_WAIT_SEC = 30.0


class SendQuotaExhausted(RuntimeError):
    """额度已用完：本次 POST 不得发出."""


class SendQuotaUnavailable(RuntimeError):
    """额度状态不可读或锁拿不到：按不允许发送处理."""


def _lock(path: Path):
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + _LOCK_WAIT_SEC
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return lock
        except (FileExistsError, PermissionError) as exc:
            # Windows：上一持有者刚删除、仍处于 delete-pending 的锁文件，O_EXCL 创建报
            # PermissionError（errno 13）而非 FileExistsError；按“锁忙”等待，超时仍 fail-closed。
            if time.monotonic() > deadline:
                raise SendQuotaUnavailable("发送额度锁 %s 在 %.0fs 内拿不到：%s"
                                           % (lock, _LOCK_WAIT_SEC, exc)) from exc
            time.sleep(0.02)
        except OSError as exc:
            raise SendQuotaUnavailable("发送额度锁 %s 无法创建：%s" % (lock, exc)) from exc


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SendQuotaUnavailable("发送额度文件 %s 不可读：%s" % (path, exc)) from exc
    if (not isinstance(data, dict) or not isinstance(data.get("max"), int)
            or isinstance(data.get("max"), bool) or data["max"] < 0
            or not isinstance(data.get("used"), int) or isinstance(data.get("used"), bool)
            or data["used"] < 0 or not isinstance(data.get("attempts", []), list)):
        raise SendQuotaUnavailable("发送额度文件 %s 形状非法：%r" % (path, data))
    return data


def _replace(tmp: Path, path: Path) -> None:
    """原子写回；Windows 上目标被其他进程短暂打开时 os.replace 报 PermissionError，有限重试."""
    deadline = time.monotonic() + _LOCK_WAIT_SEC
    while True:
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:
            if time.monotonic() > deadline:
                raise SendQuotaUnavailable("发送额度文件 %s 写回失败：%s" % (path, exc)) from exc
            time.sleep(0.02)
        except OSError as exc:
            raise SendQuotaUnavailable("发送额度文件 %s 写回失败：%s" % (path, exc)) from exc


def reserve(*, provider: str, endpoint: str, call_id: str = "") -> "int | None":
    """实际发送前占用一次额度；返回本次序号（1 起）。未启用额度时返回 ``None``."""
    raw = os.environ.get(ENV_FILE, "").strip()
    if not raw:
        return None
    path = Path(raw)
    lock = _lock(path)
    try:
        data = _read(path)
        if data["used"] >= data["max"]:
            raise SendQuotaExhausted("发送额度已用完（%d/%d）：本次 POST 不发出"
                                     % (data["used"], data["max"]))
        data["used"] += 1
        data.setdefault("attempts", []).append({
            "n": data["used"], "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "provider": str(provider), "endpoint": str(endpoint), "call_id": str(call_id or "")})
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        _replace(tmp, path)
        return data["used"]
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def init_file(path, max_sends: int) -> None:
    """运行方用：写一份新的额度文件（已存在即报错，不覆盖旧证据）."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(str(path))
    path.write_text(json.dumps({"max": int(max_sends), "used": 0, "attempts": []},
                               ensure_ascii=False, indent=1), encoding="utf-8")


__all__ = ["ENV_FILE", "SendQuotaExhausted", "SendQuotaUnavailable", "init_file", "reserve"]
