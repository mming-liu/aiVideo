# -*- coding: utf-8 -*-
"""**本轮运行身份**（run_id）的单一来源.

为什么要单独一个模块（2026-10-02 日志复核）：

  · **同一轮内必须恒定**：旧实现 `main._step01_run_id()` 每次都按**当前秒**重新拼
    `%Y%m%dT%H%M%S-<pid>`——同一进程里相隔一秒调用就得到两个不同 id（实测
    `…183824-18820` vs `…183825-18820`）。于是同一轮里「计划诊断写一个 id、
    提交 manifest 写另一个 id」，产物**无法自证属于同一轮**。
  · **跨层可见**：bank（`shared/`）、文本 provider（`_text_gen/`）、step01 诊断
    都要写「来源 run_id」。它们不能反向 import step01 的 main（成环，也把 step01
    变成底层依赖），所以要有一个**不依赖任何业务包**的落脚点。

取值优先级：`STEP01_RUN_ID`（跑批驱动注入，父子同身份）> **进程内自动生成一次并缓存**。
缓存只作用于自动生成值：注入是"每次都读"，所以驱动在运行中途钉住身份也立刻生效。
"""
from __future__ import annotations

import os
import time

_GENERATED: str | None = None


def _generate() -> str:
    return "%s-%d" % (time.strftime("%Y%m%dT%H%M%S"), os.getpid())


def set_run_id(run_id: str) -> str:
    """显式钉住本轮身份（跑批驱动/测试用）；空串 = 清除缓存，退回自动生成."""
    global _GENERATED
    _GENERATED = str(run_id or "").strip() or None
    return current_run_id()


def current_run_id() -> str:
    """本轮 run_id：`STEP01_RUN_ID` 优先，否则**首次调用时生成一次**并缓存.

    缓存是硬要求：同一轮内所有产物（诊断 / manifest / bank / 调用记录）必须拿到
    同一个值，否则"来源 run_id"只是又一个每次不同的时间戳。
    """
    _pinned = str(os.environ.get("STEP01_RUN_ID") or "").strip()
    if _pinned:
        return _pinned
    global _GENERATED
    if not _GENERATED:
        _GENERATED = _generate()
    return _GENERATED


def reset_for_tests() -> None:
    """测试用：清掉进程内缓存（不碰环境变量）."""
    global _GENERATED
    _GENERATED = None
