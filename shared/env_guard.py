"""环境变量覆盖检测 — 检测 shell 变量是否覆盖 .env.content 设置.

v6.59 (2026-09-04) 新增：
  2026-09-04 21:12 实跑：shell `export TEXT_PROVIDER=agent_plan` 覆盖了 .env.content 的
  `TEXT_PROVIDER=deepseek`，导致 9-12 分钟慢章 + doubao-seed-2-1-turbo 在 70k input +
  strict json_schema 下结构化输出能力不足（kf t_sec 越界、narration 吐出"镜头降级"占位
  文本等）。本模块启动时检测 shell 覆盖并 WARN 提示，让"配置漂移"可见。

设计原则：
  - **不阻断**：shell 覆盖仍生效（保留调试灵活性），仅 WARN 提示用户可见
  - **不修改 dotenv 行为**：默认 shell > .env.content 优先级保留
  - **集中**：所有需要检测的 key 集中传入，单点维护（避免每个 main 都重复检测）
  - **可升级**：raise_if_override=True 切强约束（CI/生产环境用，调试期不推荐）

调用方式（在 main 入口处一次）：
    from shared.env_guard import warn_shell_overrides
    warn_shell_overrides(
        ["TEXT_PROVIDER", "IMAGE_PROVIDER", "VIDEO_PROVIDER", "TTS_PROVIDER"],
        logger=_logger,
    )
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, Optional

try:
    from dotenv import dotenv_values
except ImportError:  # pragma: no cover
    dotenv_values = None  # type: ignore[assignment]


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_CONTENT = _PROJECT_ROOT / ".env.content"


def _read_env_content() -> dict[str, str]:
    """读取 .env.content 文件中的所有 key=value（不写回 os.environ）.

    复用 dotenv_values 解析（与 load_dotenv 同款语法），但只读不写。
    """
    if dotenv_values is None:
        return {}
    if not _ENV_CONTENT.exists():
        return {}
    return {k: v for k, v in dotenv_values(_ENV_CONTENT).items() if v is not None}


def find_shell_overrides(keys: Iterable[str]) -> list[tuple[str, str, str]]:
    """检测 shell 环境变量中哪些 key 覆盖了 .env.content 的值.

    Returns:
        [(key, shell_value, file_value), ...] 仅返回真正不一致的项。
        顺序与输入 keys 一致。.env.content 未设的 key 不算覆盖（shell 是唯一来源）。

    注意：os.environ.get(key) 直接读真实 env（含 shell 导出），不受 load_dotenv 影响——
    load_dotenv 默认不覆盖已存在的 env var，所以 shell 值会被保留。
    """
    file_values = _read_env_content()
    overrides: list[tuple[str, str, str]] = []
    for key in keys:
        shell_value = os.environ.get(key)
        if shell_value is None:
            # shell 没设这个 key，.env.content 的值生效，无需 WARN
            continue
        file_value = file_values.get(key)
        if file_value is None:
            # .env.content 没设这个 key，shell 是唯一来源，不算覆盖
            continue
        if shell_value.strip() != file_value.strip():
            overrides.append((key, shell_value, file_value))
    return overrides


def warn_shell_overrides(
    keys: Iterable[str],
    logger=None,
    *,
    raise_if_override: bool = False,
) -> list[tuple[str, str, str]]:
    """检测并 WARN shell 覆盖（推荐在 main 入口处调用一次）.

    Args:
        keys: 要检测的环境变量名列表（如 ["TEXT_PROVIDER", "IMAGE_PROVIDER"]）
        logger: 可选 logger 对象（有 warn 方法）；None 时用 print（控制台 + 落 log）
        raise_if_override: 若为 True，遇到任何覆盖直接 raise RuntimeError。
            仅在 CI/生产环境用——调试期设为 False 保留 shell 调试灵活性。

    Returns:
        覆盖项列表（[(key, shell_value, file_value), ...]），调用方可自行处理。
    """
    overrides = find_shell_overrides(keys)
    if not overrides:
        return overrides

    msg_lines = [
        "[!] 检测到 shell 环境变量覆盖 .env.content 设置（dotenv 默认行为，shell 优先）：",
        "    建议 unset 后重跑，避免配置漂移：",
    ]
    for key, shell_value, file_value in overrides:
        msg_lines.append(
            f"      unset {key}   # shell={shell_value!r} ≠ .env.content={file_value!r}"
        )
    msg = "\n".join(msg_lines)

    if raise_if_override:
        raise RuntimeError(msg)

    if logger is not None and hasattr(logger, "warn"):
        logger.warn(msg)
    else:
        print(msg)
    return overrides
