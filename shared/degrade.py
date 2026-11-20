# -*- coding: utf-8 -*-
"""降级登记：把「本可静默吞掉」的失败显式记下来.

用法（替代 ``except Exception: pass``）::

    from shared.degrade import note_degraded
    try:
        ...
    except Exception as _exc:
        note_degraded("shared/foo.py:bar", _exc)

* 有 ``logger`` 时调用 ``logger.warn(...)``（项目 tee_logger 的接口），否则走标准
  ``logging``；
* 同时把 ``{site, error}`` 追加到进程内列表（上限 `_MAX_RECORDS`），运行结束可用
  `degraded_sites()` 汇总；
* 本函数**自身绝不抛异常**——它出现在异常处理分支里，再抛会盖掉原有降级语义。

site 用 ``"<相对路径>:<函数名>"``，不带行号（行号随编辑漂移，不利于聚合）。
"""
from __future__ import annotations

import logging
import threading
from typing import Any

_MAX_RECORDS = 200
_MAX_LOGS_PER_SITE = 5
_RECORDS: list[dict] = []
_LOG_COUNTS: dict[str, int] = {}
_LOCK = threading.Lock()
_STD_LOGGER = logging.getLogger("aiVideo.degrade")


def _describe(exc: BaseException) -> str:
    try:
        return f"{type(exc).__name__}: {exc}"
    except Exception:  # __str__ 本身抛错
        return type(exc).__name__


def note_degraded(site: str, exc: BaseException, *, logger: Any = None) -> None:
    """登记一次降级；任何情况下都不抛异常.

    同一 site 只输出前 `_MAX_LOGS_PER_SITE` 条日志（热路径持续失败时不刷屏），
    登记列表照常累计到上限。
    """
    try:
        error = _describe(exc)
        with _LOCK:
            if len(_RECORDS) < _MAX_RECORDS:
                _RECORDS.append({"site": str(site), "error": error})
            seen = _LOG_COUNTS.get(str(site), 0)
            _LOG_COUNTS[str(site)] = seen + 1
        if seen >= _MAX_LOGS_PER_SITE:
            return
        message = f"[DEGRADED] {site}: {error}"
        try:
            if logger is not None:
                logger.warn(message)
            else:
                _STD_LOGGER.warning(message)
        except Exception:
            try:
                _STD_LOGGER.warning(message)
            except Exception:
                pass
    except Exception:
        pass


def degraded_sites() -> list[dict]:
    """本进程已登记的降级（副本）."""
    with _LOCK:
        return [dict(r) for r in _RECORDS]


def reset_degraded() -> None:
    """清空登记（测试用）."""
    with _LOCK:
        _RECORDS.clear()
        _LOG_COUNTS.clear()


__all__ = ["note_degraded", "degraded_sites", "reset_degraded"]
