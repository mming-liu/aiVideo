# -*- coding: utf-8 -*-
"""I 包：拒绝在未隔离的环境里启动（计划 §15「回归与完整交付基线」）.

`tests/conftest.py` 把 ``OUTPUT_BASE`` 指到 ``.pytest_tmp``。缺少它时，
本目录下的用例会 import 生产模块，而它们在 import 期就会按 OUTPUT_BASE
建目录/开日志（2026-09-26、2026-10-03 两次真实污染）。此处把判据前移到
session 启动：不满足 ⇒ ``pytest.UsageError``（退出码 4），一个用例都不收集。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_guard():
    """按路径加载同目录守卫，不依赖 sys.path（缺 tests/conftest.py 时它也没被设好）."""
    path = Path(__file__).with_name("isolation_guard.py")
    spec = importlib.util.spec_from_file_location("_i_isolation_guard", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("_i_isolation_guard", module)
    spec.loader.exec_module(module)
    return module


def pytest_sessionstart(session):  # noqa: ARG001 - pytest hook 签名
    problems = _load_guard().isolation_problems()
    if problems:
        raise pytest.UsageError(
            "refusing_to_start_without_output_isolation :: "
            + "; ".join(problems)
            + " :: tests/conftest.py 必须把 OUTPUT_BASE 指到隔离目录后才能收集本目录"
        )
