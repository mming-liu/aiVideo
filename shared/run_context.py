# -*- coding: utf-8 -*-
"""M1：不可变运行上下文与只读守卫（2026-11-18）.

目标（中长期计划 M1）：
- 把章节范围/路径/时长带/通道/预算收进一个不可变 ``RunContext``，避免各阶段在
  import 期或内部 ``set_ledger_path`` 覆写外部配置；
- ``read_only=True`` 时**物理禁止**写/追加 ``step01.log`` 或章节目录下的诊断文件：
  日志落点改到 ``os.devnull``，JSON 原子写直接抛 ``ReadOnlyViolation``。

用法：
    ctx = RunContext.from_env(read_only=True)
    with ctx.activate():
        ...  # 探针/确定性层
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

_PROTECTED_LOG_NAMES = ("step01.log",)
_CURRENT: ContextVar["RunContext | None"] = ContextVar("run_context", default=None)


class ReadOnlyViolation(RuntimeError):
    """只读上下文下尝试写受保护路径."""


@dataclass(frozen=True)
class RunContext:
    chapter: int = 1
    book_index: int = 0
    book_name: str = ""
    output_base: str = ""
    read_only: bool = False
    duration_band: Mapping | None = None
    channel: Mapping | None = None
    budget: Mapping | None = None
    extra: Mapping = field(default_factory=dict)

    @classmethod
    def from_env(cls, *, read_only: bool | None = None) -> "RunContext":
        def _int(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, "") or default)
            except ValueError:
                return default

        if read_only is None:
            read_only = str(os.getenv("STEP01_READ_ONLY", "")).strip().lower() in (
                "1", "true", "yes", "on")
        return cls(
            chapter=_int("NOVEL_CHAPTER", 1),
            book_index=_int("BOOK_INDEX", 0),
            book_name=str(os.getenv("BOOK_NAME", "") or ""),
            output_base=str(os.getenv("OUTPUT_BASE", "") or ""),
            read_only=bool(read_only),
        )

    @property
    def scope(self) -> dict:
        return {"chapter": self.chapter, "book_index": self.book_index,
                "book_name": self.book_name}

    def chapter_dir(self) -> Path:
        """章节目录（延迟 import，避免在隔离根确定前冻结 paths 配置）."""
        from shared.paths import chapter_dir
        return Path(chapter_dir())

    def _is_protected(self, path) -> bool:
        p = Path(path)
        if p.name in _PROTECTED_LOG_NAMES:
            return True
        try:
            p.resolve().relative_to(self.chapter_dir().resolve())
            return True
        except (ValueError, OSError):
            return False

    def assert_writable(self, path) -> None:
        if self.read_only and self._is_protected(path):
            raise ReadOnlyViolation(
                "read_only RunContext 禁止写受保护路径: %s" % path)

    def log_path(self, path) -> Path:
        """只读时把受保护日志改到 devnull；其余路径原样返回."""
        if self.read_only and Path(path).name in _PROTECTED_LOG_NAMES:
            return Path(os.devnull)
        return Path(path)

    @contextmanager
    def activate(self):
        token = _CURRENT.set(self)
        try:
            yield self
        finally:
            _CURRENT.reset(token)


def current() -> RunContext | None:
    return _CURRENT.get()


def guard_log_path(path):
    """日志落点守卫：当前上下文只读且目标是 step01.log ⇒ devnull."""
    ctx = current()
    if ctx is not None:
        return ctx.log_path(path)
    return Path(path)


def assert_writable(path) -> None:
    ctx = current()
    if ctx is not None:
        ctx.assert_writable(path)


def install(ctx: RunContext) -> RunContext:
    """进程级安装（入口模块使用；测试优先用 ``with ctx.activate()``）."""
    _CURRENT.set(ctx)
    return ctx


def activate(ctx: RunContext):
    return ctx.activate()
