# -*- coding: utf-8 -*-
"""3.10/3.11 源码语法回归：f-string 表达式内不得复用外层**单字符**引号.

为什么需要这条守卫
------------------
PEP 701（Python 3.12）才允许 f-string 的表达式部分复用外层引号::

    f"hard={d.get("k", 0)}"     # ≤3.11 -> SyntaxError: f-string: unmatched '('
    f"hard={d.get('k', 0)}"     # 所有版本合法

本项目声明支持 Python 3.10+（AGENTS.md / CLAUDE.md），但开发机解释器是 3.14，
所以本机 `compile()` / `import` 都不会报错——缺陷只在 3.10/3.11 上炸。
2026-10 实测：`step01_script_gen/main.py` 的该写法在真实 CPython 3.11.15 下
`SyntaxError: f-string: unmatched '('`，在 3.14 下正常。

判据来自对真实 3.11.15 的实测（不是推断）
------------------------------------------
下表「外层引号 / 表达式内引号」指定界符与表达式里用到的引号（避免在本文档里
写出三引号字面量，故不直接抄源码）：

    外层双引号 + 表达式内双引号        FAIL  f-string: unmatched '('
    外层双引号 + 表达式内单引号        OK
    外层三引号 + 表达式内同字符引号     OK      ← 三引号定界符合法
    外层单引号 + 表达式内单引号        FAIL  f-string: unmatched '('
    外层双引号 + 表达式内双引号（下标）  FAIL  f-string: unmatched '['

即：**只有单字符定界符**（双引号 / 单引号）受限制；三引号定界符内的同引号合法。
所以本检查只对单字符定界符报违规——避免误报三引号这种既有合法写法。

即：**只有单字符定界符**（`"` / `'`）受限制；三引号定界符内的同引号合法。
所以本检查只对单字符定界符报违规——避免误报三引号这种既有合法写法。

为什么不用 `ast.parse(feature_version=(3, 11))`
-----------------------------------------------
实测该参数**不覆盖**这条规则（对上面第一个 snippet 同样返回 OK），因为它不会
重新实现 3.12 之前的 f-string 词法分析。故这里按 3.11 的规则做 token 级检查。
"""
from __future__ import annotations

import io
import os
import tokenize
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: 不下钻的目录：体积巨大的临时/产物目录 + 非项目源码
_SKIP_DIRS = {
    ".git", ".pytest_tmp", ".kilo", ".tmp", ".venv", "venv", "__pycache__",
    "node_modules", "output", ".mypy_cache", ".pytest_cache", ".idea",
    #: `reference_skills/` 是**外部参考技能**的 vendored 拷贝（`.gitignore:61` 整目录
    #: 忽略、0 个受版本控制文件、189 个 .py，项目源码无一处 import 它）。
    #: 它里面确实有一处同类写法（`Micro-Drama-Skills/projects/DM-002_tjkc/
    #: generate_media.py:392`），但那不是本项目要保证 3.10+ 可运行的源码，故不纳入。
    #: 注：`skills/` 是**受版本控制的项目内容**（338 个文件），必须继续纳入扫描。
    "reference_skills",
}

#: 只有单字符定界符受 ≤3.11 限制（见模块 docstring 的实测表）
_SINGLE_DELIMS = ('"', "'")

#: PEP 701（≥3.12）才有；≤3.11 为 None，此时走单 STRING token 路径
_FSTRING_START = getattr(tokenize, "FSTRING_START", None)
_FSTRING_END = getattr(tokenize, "FSTRING_END", None)


def _fstring_start_delim(text: str):
    """``FSTRING_START`` token 文本（形如 ``f"`` / ``rf'''`` / ``F\"\"\"``）→ 定界符.

    不是 f-string 前缀或形态不认识时返回 None（调用方按「无定界符」处理，不误报）。
    """
    i = 0
    while i < len(text) and text[i].isalpha():
        i += 1
    prefix, rest = text[:i], text[i:]
    if "f" not in prefix.lower():
        return None
    for delim in ('"""', "'''", '"', "'"):
        if rest == delim:
            return delim
    return None


def _split_fstring(text: str):
    """把 STRING token 拆成 ``(prefix, delimiter, body)``；非字符串字面量返回 None."""
    i = 0
    while i < len(text) and text[i].isalpha():
        i += 1
    prefix, rest = text[:i], text[i:]
    for delim in ('"""', "'''", '"', "'"):
        if (rest.startswith(delim) and rest.endswith(delim)
                and len(rest) >= 2 * len(delim)):
            return prefix, delim, rest[len(delim):-len(delim)]
    return None


def _expression_quote_hit(body: str, quote: str):
    """表达式区内首个与外层同引号的位置（无则 None）.

    只跟踪单花括号深度；``{{`` / ``}}`` 是字面花括号转义，不改变深度。
    """
    depth = 0
    i = 0
    n = len(body)
    while i < n:
        ch = body[i]
        if ch in "{}" and i + 1 < n and body[i + 1] == ch:
            i += 2                      # {{ 或 }} → 字面转义
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif depth > 0 and ch == quote:
            return i
        i += 1
    return None


def _find_violations(source: str):
    """返回 ``[(行号, 列偏移, 片段)]``：f-string 表达式内复用了外层同引号.

    两套 token 形态都要处理：

    * **Python ≥3.12**（PEP 701）：f-string 被拆成 ``FSTRING_START`` /
      ``FSTRING_MIDDLE`` / ``FSTRING_END``，表达式部分是普通 token——所以表达式里
      的嵌套字符串就是一个 ``STRING`` token。判据：f-string 栈非空、花括号深度 > 0，
      且该 ``STRING`` 的首字符等于当前 f-string 的单字符定界符。
    * **Python ≤3.11**：整个 f-string 是**一个** ``STRING`` token，按 token 文本
      逐字符扫表达式区（见 `_expression_quote_hit`）。守卫本身要在 3.11 上也能跑，
      所以两条路径都留。
    """
    found = []
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return found

    if _FSTRING_START is not None:
        stack: list = []                    # [delim, 花括号深度]
        for tok in tokens:
            if tok.type == _FSTRING_START:
                stack.append([_fstring_start_delim(tok.string), 0])
                continue
            if _FSTRING_END is not None and tok.type == _FSTRING_END:
                if stack:
                    stack.pop()
                continue
            if not stack:
                continue
            if tok.type == tokenize.OP:
                if tok.string == "{":
                    stack[-1][1] += 1
                elif tok.string == "}":
                    stack[-1][1] = max(0, stack[-1][1] - 1)
                continue
            if tok.type == tokenize.STRING and stack[-1][1] > 0:
                delim = stack[-1][0]
                if delim in _SINGLE_DELIMS and tok.string[:1] == delim:
                    found.append((tok.start[0], tok.start[1], tok.string))
        return found

    # Python ≤3.11：f-string 是单个 STRING token
    for tok in tokens:
        if tok.type != tokenize.STRING:
            continue
        parsed = _split_fstring(tok.string)
        if parsed is None:
            continue
        prefix, delim, body = parsed
        if "f" not in prefix.lower():
            continue
        if delim not in _SINGLE_DELIMS:
            continue                    # 三引号定界符实测合法
        hit = _expression_quote_hit(body, delim)
        if hit is not None:
            found.append((tok.start[0], hit, body[max(0, hit - 30):hit + 30]))
    return found


def _iter_py_files():
    for dirpath, dirnames, filenames in os.walk(_PROJECT_ROOT):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if name.endswith(".py"):
                yield Path(dirpath) / name


# ---------------------------------------------------------------------------
# 先证明判据本身有效：零命中只有在「探针能抓到已知违规」时才有意义
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [
    'x = f"{d.get("k", 0)}"',
    "x = f'{d.get('k')}'",
    'x = f"{d["k"]}"',
])
def test_detector_flags_known_bad_snippets(bad):
    assert _find_violations(bad), "探针失效：已知违规没有被检出"


@pytest.mark.parametrize("good", [
    "x = f\"{d.get('k', 0)}\"",         # 换用不同引号
    'x = f"""{d.get("k")}"""',          # 三引号定界符（实测合法）
    "x = f'''{d.get('k')}'''",          # 同上
    'x = f"{ {1: 2} }"',                # 嵌套花括号、无引号
    'x = f"a{{b}}c"',                   # 双花括号转义
    'x = "plain {not an f-string}"',    # 非 f-string
])
def test_detector_accepts_legal_snippets(good):
    assert _find_violations(good) == [], "误报：合法写法被当成违规"


def test_no_fstring_quote_reuse_in_project_sources():
    """全项目源文件不得出现 ≤3.11 下非法的 f-string 单引号复用."""
    offenders = []
    for path in _iter_py_files():
        try:
            source = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            continue
        for lineno, _col, snippet in _find_violations(source):
            offenders.append(
                f"{path.relative_to(_PROJECT_ROOT)}:{lineno}: ...{snippet}...")
    assert not offenders, (
        "以下 f-string 在 Python 3.10/3.11 下是 SyntaxError（PEP 701 仅 3.12+ 支持）；"
        "把表达式内的引号换成另一种引号即可：\n  " + "\n  ".join(offenders))
