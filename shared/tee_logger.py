"""TeeLogger — 日志双通道输出工具.

将日志同时写入文件 + 控制台，支持日志级别过滤。

用法:
    from shared.tee_logger import TeeLogger

    logger = TeeLogger(Path("step01.log"), console_level="INFO")

    logger.info("关键信息")       # 文件 + 控制台
    logger.warn("警告信息")       # 文件 + 控制台
    logger.verbose("详细进度")    # 仅文件（不污染控制台）
    logger.console("仅控制台")    # 仅控制台（不写入文件）

    logger.close()  # 可选，强制刷新并关闭文件句柄
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional


class TeeLogger:
    """同时写入文件和控制台的日志记录器.

    日志级别（从高到低）：
    - ERROR:   错误（总是输出到控制台 + 文件）
    - WARN:    警告（总是输出到控制台 + 文件）
    - INFO:    关键摘要（控制台 + 文件）
    - VERBOSE: 详细进度（仅文件）
    """

    _LEVELS = {"ERROR": 10, "WARN": 20, "INFO": 30, "VERBOSE": 40}

    def __init__(
        self,
        log_path: Path,
        console_level: str = "INFO",
        auto_timestamp: bool = True,
    ):
        """初始化日志记录器.

        Args:
            log_path: 日志文件路径
            console_level: 控制台最低输出级别（默认 INFO）
            auto_timestamp: 是否自动添加时间戳前缀
        """
        self.log_path = Path(log_path)
        self.console_level = self._LEVELS.get(console_level.upper(), 30)
        self.auto_timestamp = auto_timestamp
        self._fp: Optional[object] = None
        # 线程安全锁（v6.65：多候选并行生成时多线程并发写同一日志文件）
        self._lock = threading.Lock()
        # 静默控制台：stdout 被重定向到日志时置 True，避免 logger 的 print
        # 回流日志文件造成每条消息双写（see step01 main.py _redirect_to_log）
        self.console_silent = False

        # 确保目录存在
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _open(self):
        if self._fp is None:
            self._fp = open(str(self.log_path), "a", encoding="utf-8")

    def _write_file(self, msg: str):
        with self._lock:
            self._open()
            if self.auto_timestamp:
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._fp.write(f"[{ts}] {msg}\n")
            else:
                self._fp.write(f"{msg}\n")
            self._fp.flush()

    def _should_console(self, level: str) -> bool:
        lv = self._LEVELS.get(level.upper(), 40)
        return lv <= self.console_level

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def log(self, msg: str, level: str = "INFO"):
        """写日志（文件 + 控制台，按 level 决定是否写控制台）."""
        self._write_file(msg)
        if self._should_console(level) and not self.console_silent:
            try:
                print(msg)
            except UnicodeEncodeError:
                print(msg.encode("ascii", errors="replace").decode("ascii"))

    def info(self, msg: str):
        """INFO 级别：文件 + 控制台."""
        self.log(msg, "INFO")

    def warn(self, msg: str):
        """WARN 级别：文件 + 控制台."""
        self.log(msg, "WARN")

    def error(self, msg: str):
        """ERROR 级别：文件 + 控制台."""
        self.log(msg, "ERROR")

    def verbose(self, msg: str):
        """VERBOSE 级别：仅文件."""
        self._write_file(msg)

    def console(self, msg: str):
        """仅输出到控制台（不写文件）."""
        try:
            print(msg)
        except UnicodeEncodeError:
            print(msg.encode("ascii", errors="replace").decode("ascii"))

    def separator(self, char: str = "=", width: int = 50):
        """输出分隔线到文件和/或控制台."""
        line = char * width
        self.log(line, "INFO")

    def close(self):
        """关闭文件句柄."""
        if self._fp:
            self._fp.close()
            self._fp = None

    def __del__(self):
        self.close()


# ------------------------------------------------------------------
# 全局单例（按 log_path 缓存，M8 修复 2026-08-14）
# 原实现只缓存第一个 log_path 的 logger，后续 get_logger(其他路径) 会
# 静默返回错误文件句柄，日志写错文件。现按 log_path 分别缓存。
# ------------------------------------------------------------------
_loggers: dict[str, TeeLogger] = {}


def get_logger(log_path: Optional[Path] = None, console_level: str = "INFO") -> Optional[TeeLogger]:
    """获取按 log_path 缓存的 logger 实例（脚本化场景足够，无需加锁）."""
    if log_path is None:
        return next(iter(_loggers.values())) if _loggers else None
    key = str(Path(log_path))
    if key not in _loggers:
        _loggers[key] = TeeLogger(log_path, console_level=console_level)
    return _loggers[key]


def reset_logger():
    """关闭并清空全部 logger 缓存."""
    global _loggers
    for logger in _loggers.values():
        logger.close()
    _loggers = {}
