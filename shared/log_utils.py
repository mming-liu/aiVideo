"""共享日志工具 — 供各 provider 使用（code-audit 07-29：_verbose_write 重复 ×4）.

2026-10-03 复核 P2-10：候选/实验模式必须**从入口到 provider**写实验目录的日志。
旧写法在这里（以及 provider / 适配器里）各自 `chapter_dir() / x.log`，于是"候选
模式零正式写入"在日志这一项上不成立。落点判据因此收成一份 `resolve_log_file`：
进程级上下文有实验日志目录就用它，否则才回章目录。
"""
from __future__ import annotations

from pathlib import Path

#: 实验/候选日志目录（None = 生产章目录）。由入口设置，provider 只读。
_LOG_CONTEXT: dict = {"log_dir": None}


def set_log_context(log_dir) -> None:
    """设置本次运行的日志落点（候选模式：实验根下的 ``logs/``；None = 恢复生产）."""
    _LOG_CONTEXT["log_dir"] = Path(log_dir) if log_dir else None


def current_log_dir():
    return _LOG_CONTEXT["log_dir"]


def resolve_log_file(log_file: str = "step02b.log") -> Path:
    """**唯一**的日志落点判据（候选上下文优先，否则章目录）."""
    base = _LOG_CONTEXT["log_dir"]
    if base is not None:
        return Path(base) / log_file
    from shared.paths import chapter_dir
    return chapter_dir() / log_file


def verbose_write(msg: str, log_file: str = "step02b.log") -> None:
    """写入日志文件，静默控制台（fallback 到 print 时输出到控制台）。

    Args:
        msg: 日志消息
        log_file: 日志文件名（step02a.log 用于图片生成，step02b.log 用于视频生成）
    """
    try:
        from shared.tee_logger import get_logger
        _vl = get_logger(resolve_log_file(log_file), console_level="INFO")
        _vl.verbose(msg)
    except Exception:
        print(msg)