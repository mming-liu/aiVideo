# -*- coding: utf-8 -*-
"""I 包：测试环境隔离守卫（计划 §15「回归与完整交付基线」）.

判据只有一条但必须**真的能拒**：测试进程的 OUTPUT_BASE 必须落在隔离目录里，
且该隔离由 ``tests/conftest.py`` 提供。任一不满足 ⇒ 拒绝启动。

为什么不是"检查一下就好"：``OUTPUT_BASE`` 是 `shared/paths.py` 计算章节目录的唯一
输入（`shared.paths.step01_log_file()` 等全部由它派生）。它在测试进程里指向生产
``output/`` 时，任何 import 生产模块的用例都会直接往正式产物目录写日志/中间件——
2026-09-26 与 2026-10-03 各有一次真实污染记录（见交付台账 §7）。
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = ["isolation_problems", "ISOLATION_MARKER"]

#: 允许的隔离根标记：OUTPUT_BASE 的路径必须含这一级目录名，
#: 且不得落在仓库的 output/ 下。
ISOLATION_MARKER = ".pytest_tmp"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def isolation_problems(repo_root: Path | None = None) -> list[str]:
    """返回不满足隔离的判据清单；空表 = 可以启动."""
    root = Path(repo_root) if repo_root is not None else _repo_root()
    problems: list[str] = []

    harness = root / "tests" / "conftest.py"
    if not harness.is_file():
        problems.append("missing_isolation_harness:%s" % harness)

    raw = os.environ.get("OUTPUT_BASE", "")
    if not raw.strip():
        problems.append("output_base_unset")
        return problems

    try:
        out = Path(raw).resolve()
    except OSError as exc:  # pragma: no cover - 只可能是极端路径
        problems.append("output_base_unusable:%s" % exc)
        return problems

    if ISOLATION_MARKER not in out.parts:
        problems.append("output_base_not_isolated:%s" % out)

    prod = (root / "output").resolve()
    if out == prod or prod in out.parents:
        problems.append("output_base_points_at_production:%s" % out)

    return problems
