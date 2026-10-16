"""shared.run_scope - 运行范围（章/部）的命令行覆盖：``--chapter`` / ``--book`` / ``--book-name``

**背景**：章号原本只能由 ``.env.content`` 的 ``NOVEL_CHAPTER`` 决定（或 shell 前缀
``NOVEL_CHAPTER=2 python ...`` 覆盖）。跑第 2 章要先改配置文件，改完容易忘；dotenv
不覆盖已存在的 env，shell 前缀还会一路黏住后续进程。忘改 / 改错的后果是**静默用错章
跑完**——写进 ``global/`` 下跨章共享的 bank（``last_chapter`` 永久污染）、窗口判据
``NOVEL_CHAPTER - 1`` 依然成立，看不出任何异常。

**机制**：把 CLI 值写进 ``os.environ``，而不是逐层改函数签名。理由是 ``shared/paths.py``
全部惰性读 ``os.getenv``（``chapter_index()`` / ``chapter_dir()`` …），且 ``load_dotenv``
**不覆盖已存在的 env**，所以写 env 之后路径层与所有直接 ``os.getenv("NOVEL_CHAPTER")``
的读者一起跟随，一处生效、无需穿透几十个调用点。

**代价（因此有使用位置要求）**：进程内改 env 对**已经 import 过的**模块无效——
``shared/config.py:24`` 的 ``NOVEL_CHAPTER``、``tts/config.py`` 的 ``TTSCfg.OUTPUT_PATH``
都是 import 时求值。step01 / step03 / step04 在模块顶部就把章号冻进常量，所以这三个入口
必须在**第一个 config/env 读取之前**调用 :func:`bootstrap`；step02a / step02b 的章级路径
全在 ``main()`` 内惰性解析，常规 argparse + :func:`apply_scope` 即可。

**本模块在 import 期只允许使用标准库**：它会在 ``shared.config`` / ``shared.paths``
之前被导入，一旦在这里 import 它们，章号就会被提前冻结，整个机制失效。
``shared.env_guard`` / ``shared.paths`` 一律在函数内惰性 import。

优先级：**CLI > shell env > ``.env.content``**（写 env 即最高，故 shell 已有不同值时另行 WARN）。
"""
from __future__ import annotations

import os
from typing import Any, Optional

# 只有这些键属于"运行范围"，其余配置项不在此覆盖
#: 呈现模式的环境变量名（**与 `step01_script_gen.outline.PRESENTATION_MODE_ENV` 同值**
#: ——这里不 import 它，避免包依赖倒挂；`test_run_scope` 里核两处一致）。
PRESENTATION_MODE_ENV = "STEP01_PRESENTATION_MODE"
ENV_CHAPTER = "NOVEL_CHAPTER"
ENV_BOOK_INDEX = "BOOK_INDEX"
ENV_BOOK_NAME = "BOOK_NAME"

_LOG_PREFIX = "[SCOPE]"


def add_scope_arguments(parser: Any, *, presentation_mode: bool = False) -> None:
    """给已有 ArgumentParser 挂上运行范围参数（step02a / step02b 用）。

    `presentation_mode=True` 时额外登记 `--presentation-mode`——**只有 step01 需要**。
    登记在**同一个严格 parser** 上，是为了让"未知参数拒收"这条约束原样保留：旧写法把这个
    开关留给 `main()` 自己扫 `sys.argv`，于是走命令行时报 `unrecognized arguments` 退出 2，
    只有环境变量能生效（2026-10-06 实跑就是这么绕过去的）。
    """
    parser.add_argument(
        "--chapter", "-C", type=int, default=None, metavar="N",
        help="指定章节序号，覆盖 .env.content 的 NOVEL_CHAPTER（如 --chapter 2 -> chapter_002）",
    )
    parser.add_argument(
        "--book", type=int, default=None, metavar="N",
        help="指定部序号，覆盖 BOOK_INDEX（如 --book 1 -> part_01_xxx）",
    )
    parser.add_argument(
        "--book-name", default=None, metavar="NAME",
        help="指定部名，覆盖 BOOK_NAME（如 --book-name 杀青州 -> part_01_杀青州）",
    )
    if presentation_mode:
        parser.add_argument(
            "--presentation-mode", default=None, metavar="MODE",
            choices=["legacy_audio", "screen_hybrid"],
            help="呈现模式：legacy_audio（默认，图外旁白）| screen_hybrid（画面文字卡）。"
                 "在拿锁、建目录、算缓存键之前生效。",
        )


def _file_values() -> dict:
    """只读 ``.env.content`` 的现值（不写回 env），用于报告"覆盖了什么"。"""
    try:
        from shared.env_guard import _read_env_content
        return dict(_read_env_content())
    except Exception:  # 报告用途，读不到就不报
        return {}


def _validate_int(flag: str, value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"--{flag} 必须是整数，收到 {value!r}") from None
    if number < 1:
        raise ValueError(f"--{flag} 必须 >= 1，收到 {number}")
    return number


def _emit(level: str, message: str, logger: Any) -> None:
    """有 logger 走 logger，否则直接写 stdout（bootstrap 阶段 logger 尚未建立）。"""
    if logger is not None:
        getattr(logger, level, logger.info)(message)
    else:
        print(f"{_LOG_PREFIX} {message}", flush=True)


def apply_scope(chapter: Any = None,
                book: Any = None,
                book_name: Any = None,
                *,
                logger: Any = None,
                max_echo: int = 3) -> dict:
    """把命令行给的章/部写进 ``os.environ``，返回解析结果。

    Args:
        chapter: 章号（>=1）；None = 不改，沿用 env / .env.content
        book: 部序号（>=1，映射 BOOK_INDEX）；None = 不改
        book_name: 部名（映射 BOOK_NAME）；None = 不改，空串视为非法
        logger: 可选日志器；缺省写 stdout

    Returns:
        {"chapter"/"book_index"/"book_name": 生效值, "source": {key: "cli"|"env"},
         "overrides": {key: (旧值, 新值)}, "summary": 单行摘要}

    Raises:
        ValueError: 章/部序号不是整数或 < 1，或部名为空 —— 显式失败，不静默回落。
    """
    values = {
        ENV_CHAPTER: _validate_int("chapter", chapter),
        ENV_BOOK_INDEX: _validate_int("book", book),
    }
    if book_name is not None:
        cleaned = str(book_name).strip()
        if not cleaned:
            raise ValueError("--book-name 不能为空（不给该参数即沿用 BOOK_NAME）")
        values[ENV_BOOK_NAME] = cleaned

    # 始终读一份 .env.content：既要报告"覆盖了什么"，也要在 env 里还没有该键时
    # 补全摘要（bootstrap 发生在 shared.paths 加载 .env 之前，此时 BOOK_NAME 还不在 env）
    file_values = _file_values()
    overrides: dict[str, tuple] = {}
    for key, value in values.items():
        if value is None:
            continue
        shell_prev = os.environ.get(key)
        file_prev = file_values.get(key)
        os.environ[key] = str(value)
        overrides[key] = (shell_prev if shell_prev is not None else file_prev, value)
        if shell_prev is not None and file_prev is not None and str(shell_prev) != str(file_prev):
            # shell 前缀覆盖是记过档的事故模式（见 shared/env_guard.py 顶部），额外点出来
            _emit("warn",
                  f"{key}: shell 已有 {shell_prev}（.env.content 为 {file_prev}）"
                  f"，本次命令行覆盖为 {value}", logger)
        elif file_prev is not None and str(file_prev) == str(value):
            _emit("info", f"{key}: {value}（命令行，与 .env.content 相同）", logger)
        else:
            _emit("info", f"{key}: {file_prev if file_prev is not None else '(未设置)'}"
                          f" -> {value}（命令行）", logger)

    def _effective(key: str, fallback: str) -> str:
        raw = os.environ.get(key)
        if raw is None or raw == "":
            raw = file_values.get(key)
        return fallback if raw is None or raw == "" else str(raw)

    try:
        resolved = {
            "chapter": int(_effective(ENV_CHAPTER, "1")),
            "book_index": int(_effective(ENV_BOOK_INDEX, "1")),
            "book_name": _effective(ENV_BOOK_NAME, "").strip(),
        }
    except ValueError:
        raise ValueError(
            f"{ENV_CHAPTER}/{ENV_BOOK_INDEX} 不是整数："
            f"NOVEL_CHAPTER={os.environ.get(ENV_CHAPTER)!r} "
            f"BOOK_INDEX={os.environ.get(ENV_BOOK_INDEX)!r}") from None
    resolved["source"] = {key: ("cli" if key in overrides else "env") for key in
                          (ENV_CHAPTER, ENV_BOOK_INDEX, ENV_BOOK_NAME)}
    resolved["overrides"] = overrides
    resolved["summary"] = _summary(resolved)
    if overrides and max_echo:
        _emit("info", f"生效：{resolved['summary']}", logger)
    return resolved


#: scope 的**身份**字段（其余是诊断/回显，不参与身份比较）
SCOPE_IDENTITY_KEYS = ("chapter", "book_index", "book_name")


def scope_identity(scope: Any) -> dict:
    """取 scope 的身份子集.

    判"是不是同一章/同一部"只认这三个键：``apply_scope`` 的返回值还带
    ``source/overrides/summary`` 等诊断字段，而 ``current_scope`` 只给身份三元组，
    直接比整份 dict 会把合法路径判成不符（2026-10-03 复核 P1-7 自查发现）。
    """
    src = dict(scope or {}) if isinstance(scope, dict) else {}
    return {k: src.get(k) for k in SCOPE_IDENTITY_KEYS if k in src}


def current_scope_identity() -> dict:
    """**当前运行**的 scope 身份（消费侧用它声明"我要的是哪一章/哪一部"）.

    与 ``step03_tts_audio.shot_tts.current_scope`` 同一口径：身份三元组取自
    ``shared.paths`` 的生效配置，不自己拼目录层级。
    """
    from shared.paths import book_index, book_name, chapter_index
    return {"chapter": int(chapter_index()), "book_index": book_index(),
            "book_name": book_name()}


def _summary(resolved: dict) -> str:
    part = f"part_{resolved['book_index']:02d}"
    if resolved["book_name"]:
        part = f"{part}_{resolved['book_name']}"
    return f"chapter_{resolved['chapter']:03d} @ {part}"


def scope_log_line(resolved: dict) -> str:
    """给阶段日志用的单行运行范围说明（含命令行覆盖明细，便于事后对账）。"""
    if not resolved.get("overrides"):
        return f"运行范围：{resolved.get('summary', '')}（.env.content）"
    detail = ", ".join(f"{key}={value[1]}" for key, value in resolved["overrides"].items())
    return f"运行范围：{resolved.get('summary', '')}（命令行覆盖 {detail}）"


def step01_hint(resolved: dict) -> str:
    """给下游阶段（step02a / step02b）的报错用：提示先跑哪条 step01 命令。

    带上本次生效的章/部，只把**用户显式给过**的部参数写进命令，避免建议里出现
    与本次不同的组合。
    """
    parts = [f"--chapter {resolved.get('chapter', 1)}"]
    source = resolved.get("source") or {}
    if source.get(ENV_BOOK_INDEX) == "cli":
        parts.append(f"--book {resolved.get('book_index', 1)}")
    if source.get(ENV_BOOK_NAME) == "cli" and resolved.get("book_name"):
        parts.append(f"--book-name {resolved['book_name']}")
    return "python step01_script_gen/main.py " + " ".join(parts)


def bootstrap(description: str = "", argv: Optional[list] = None, *,
              presentation_mode: bool = False) -> dict:
    """给没有 argparse 的入口（step01 / step03 / step04）在模块顶部用。

    **调用处必须带 ``if __name__ == "__main__":`` 守卫**——测试与工具会 import 这些
    main 模块，无守卫的 ``parse_args()`` 会把 pytest 的 argv 当参数解析并 SystemExit。
    该守卫由 ``tests/shared/test_run_scope.py`` 的源码扫描钉住。

    用严格 ``parse_args()``：拼错的参数立刻报错，不静默吞掉（宁可显式失败）。
    """
    import argparse

    parser = argparse.ArgumentParser(
        description=description or None,
        epilog="未给运行范围参数时，章/部沿用 .env.content 的设置。",
    )
    add_scope_arguments(parser, presentation_mode=presentation_mode)
    args = parser.parse_args(argv)
    if presentation_mode:
        _mode = getattr(args, "presentation_mode", None)
        if _mode:
            # 与章/部同一个时机生效：**配置与日志初始化之前**就写进环境，
            # 后面 `presentation_mode()` 读到的就是它（`main()` 再写回一次，两处一致）。
            os.environ[PRESENTATION_MODE_ENV] = str(_mode)
    return apply_scope(args.chapter, args.book, args.book_name)
