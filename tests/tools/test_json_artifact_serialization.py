# -*- coding: utf-8 -*-
"""U6-FIN WP1c：构建/冻结工具的 JSON 落盘必须走 atomic_dump_json（棘轮）.

2026-11-14 R6 S05 复核发现：候选构建脚本手写
`path.write_text(json.dumps(obj, ...) + "\\n")`，而生产侧 step01 走
`shared.json_io.atomic_dump_json`（**无**尾部换行）。同一份内容因此有两个
SHA-256（44619 vs 44618 字节），核对时只能靠 `dict` 相等去解释——这不是
"内容不一致"，是自己制造的身份噪声，而且下次一定会有人把它当成缺陷去重算指纹。

本棘轮只认一条：`tools/build_*.py` / `tools/freeze_*.py` 里不得出现
`write_text(json.dumps(` 这种手写落盘。stdout/stderr 打印不受限制（那是协议输出，
不是产物序列化）。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_TOOLS = _ROOT / "tools"

#: 手写落盘：`xxx.write_text(` 后紧跟 `json.dumps(`（允许中间换行/缩进）。
_MANUAL_DUMP = re.compile(r"\.write_text\(\s*json\.dumps\(", re.S)


def _artifact_tools() -> list[Path]:
    files = []
    for pattern in ("build_*.py", "freeze_*.py"):
        files.extend(sorted(_TOOLS.glob(pattern)))
    return files


def test_there_is_something_to_check():
    """棘轮自身不能空转：找不到文件就等于没保护."""
    assert len(_artifact_tools()) >= 5, [p.name for p in _artifact_tools()]


@pytest.mark.parametrize("path", _artifact_tools(), ids=lambda p: p.name)
def test_build_tools_use_atomic_dump_json(path: Path):
    text = path.read_text(encoding="utf-8")
    hits = _MANUAL_DUMP.findall(text)
    assert not hits, (
        "%s 仍在手写 JSON 落盘；改用 `from shared.json_io import atomic_dump_json`"
        "（否则会多写一个尾部换行，与生产侧字节不一致）" % path.name)
