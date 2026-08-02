"""共享日志工具 — 供各 provider 使用（code-audit 07-29：_verbose_write 重复 ×4）."""


def verbose_write(msg: str, log_file: str = "step02b.log") -> None:
    """写入日志文件，静默控制台（fallback 到 print 时输出到控制台）。

    Args:
        msg: 日志消息
        log_file: 日志文件名（step02a.log 用于图片生成，step02b.log 用于视频生成）
    """
    try:
        from shared.tee_logger import get_logger
        from shared.paths import chapter_dir
        _vl = get_logger(chapter_dir() / log_file, console_level="INFO")
        _vl.verbose(msg)
    except Exception:
        print(msg)