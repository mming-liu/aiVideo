# -*- coding: utf-8 -*-
"""M1：不可变运行上下文与只读守卫（2026-11-18，O3 扩展于 2026-11-28）.

目标（中长期计划 M1 + O3）：
- 把章节范围/路径/时长带/通道/预算/画幅/帧率/画风收进一个不可变 ``RunContext``，
  避免各阶段在 import 期或内部 ``set_ledger_path`` 覆写外部配置；
- ``read_only=True`` 时**物理禁止**写/追加日志或章节目录下的诊断文件：
  日志落点改到 ``os.devnull``，JSON 原子写直接抛 ``ReadOnlyViolation``；
- 下游四个阶段入口接受可选 ``ctx``（未传时 ``from_env()``），并用
  ``apply_env()`` 把上下文里的值**幂等**写进进程环境——只在调用时读 ``os.getenv``
  的消费者（画幅、声音合同、配置预检）因此能看到同一份上下文；
- ``apply_overrides()`` 提供**作用域**版本：进入时覆盖、退出时逐键还原，
  同一进程里连续用两套上下文不会串扰（测试与嵌入式调用用）。

用法：
    ctx = RunContext.from_env(read_only=True)
    with ctx.activate():
        ...  # 探针/确定性层

    with RunContext(video_size="2560*1440").apply_overrides():
        ...  # 这段代码里的 call-time 读 env 的消费者看到 16:9
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

#: 上下文 → 环境变量（`apply_env` / `apply_overrides` 共用同一张表）。
#: 只包含**调用时读**的键；`shared.config` 的 import 期常量不在此列（改了也不会重算）。
_ENV_FIELDS: tuple[tuple[str, str], ...] = (
    ("NOVEL_CHAPTER", "chapter"),
    ("BOOK_INDEX", "book_index"),
    ("BOOK_NAME", "book_name"),
    ("OUTPUT_BASE", "output_base"),
    ("VIDEO_SIZE", "video_size"),
    ("RENDER_FPS", "merge_fps"),
    ("DURATION_MIN_SEC", "duration_min_sec"),
    ("DURATION_MAX_SEC", "duration_max_sec"),
    ("VIDEO_STYLE", "video_style"),
)


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
    # ---- O3 扩展：下游阶段需要的生效配置 ----
    #: 生成画布（`WxH` / `W*H`）；空 = 不覆盖，由调用时读取方自行回退默认值
    video_size: str = ""
    #: 合并帧率；0 = 不覆盖
    merge_fps: int = 0
    #: 时长合同下限/上限（秒）；0.0 = 不覆盖
    duration_min_sec: float = 0.0
    duration_max_sec: float = 0.0
    #: 画风预设名（`shared.style_prefix.STYLE_PRESETS` 的键）；空 = 不覆盖
    video_style: str = ""
    #: 章节目录显式覆盖（**不叫 `chapter_dir`**：与既有方法同名会遮蔽它）
    chapter_dir_override: str = ""
    #: 出站预算守卫对象（`shared.outbound_guard.OutboundGuard`）；None = 未挂载
    budget_guard: object | None = None

    @classmethod
    def from_env(cls, *, read_only: bool | None = None,
                 env: Mapping[str, str] | None = None) -> "RunContext":
        """从环境映射构造（默认进程环境；`env` 供测试/嵌入方显式传入）."""
        source: Mapping[str, str] = os.environ if env is None else env

        def _raw(name: str) -> str:
            return str(source.get(name) or "")

        def _int(name: str, default: int) -> int:
            try:
                return int(_raw(name) or default)
            except ValueError:
                return default

        def _float(name: str) -> float:
            try:
                return float(_raw(name))
            except ValueError:
                return 0.0

        if read_only is None:
            read_only = _raw("STEP01_READ_ONLY").strip().lower() in (
                "1", "true", "yes", "on")
        return cls(
            chapter=_int("NOVEL_CHAPTER", 1),
            book_index=_int("BOOK_INDEX", 0),
            book_name=_raw("BOOK_NAME"),
            output_base=_raw("OUTPUT_BASE"),
            read_only=bool(read_only),
            video_size=_raw("VIDEO_SIZE"),
            merge_fps=_int("RENDER_FPS", 0),
            duration_min_sec=_float("DURATION_MIN_SEC"),
            duration_max_sec=_float("DURATION_MAX_SEC"),
            video_style=_raw("VIDEO_STYLE"),
        )

    @property
    def scope(self) -> dict:
        return {"chapter": self.chapter, "book_index": self.book_index,
                "book_name": self.book_name}

    def chapter_dir(self) -> Path:
        """章节目录：显式覆盖优先；否则延迟 import paths（避免隔离根确定前冻结）."""
        if self.chapter_dir_override:
            return Path(self.chapter_dir_override)
        from shared.paths import chapter_dir
        return Path(chapter_dir())

    # ---- 环境投影 --------------------------------------------------------
    def env_values(self) -> dict[str, str]:
        """上下文里**非空**字段对应的环境变量（决定覆盖哪些键）."""
        out: dict[str, str] = {}
        for env_name, field_name in _ENV_FIELDS:
            value = getattr(self, field_name)
            if value in (None, "", 0, 0.0):
                continue
            out[env_name] = str(value)
        out["STEP01_READ_ONLY"] = "1" if self.read_only else ""
        return out

    def apply_env(self, *, env: dict | None = None) -> dict[str, str]:
        """把上下文**幂等**写进进程环境（阶段入口用）；返回实际写入的键值."""
        target = os.environ if env is None else env
        written = self.env_values()
        for key, value in written.items():
            if value == "":
                target.pop(key, None)
            else:
                target[key] = value
        return written

    @contextmanager
    def apply_overrides(self, *, env: dict | None = None):
        """作用域覆盖：进入时写入，退出时逐键还原（同进程两套上下文零串扰）."""
        target = os.environ if env is None else env
        written = self.env_values()
        missing = object()
        saved = {key: target.get(key, missing) for key in written}
        for key, value in written.items():
            if value == "":
                target.pop(key, None)
            else:
                target[key] = value
        try:
            yield self
        finally:
            for key, old in saved.items():
                if old is missing:
                    target.pop(key, None)
                else:
                    target[key] = old

    # ---- 只读守卫 --------------------------------------------------------
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


def bind(ctx: RunContext | None = None, *, env: Mapping[str, str] | None = None,
         apply: bool = True) -> RunContext:
    """阶段入口统一绑定：`ctx=None` 时从环境构造；安装并把值投影进进程环境.

    幂等：`from_env()` 出来的上下文再 `apply_env()` 只是把同样的值写回去。
    """
    ctx = ctx or RunContext.from_env(env=env)
    install(ctx)
    if apply:
        ctx.apply_env()
    return ctx
