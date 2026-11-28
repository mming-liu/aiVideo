# -*- coding: utf-8 -*-
"""I 包 §15 守卫：缺 `tests/conftest.py` 的 OUTPUT_BASE 隔离必须**真的拒绝启动**.

判别性设计：不是断言"当前环境是隔离的"（那只能证明这一台机器），而是把一棵
**缺隔离夹具的副本**真的拿去跑 pytest，断言它以退出码 4 停下且原因是隔离守卫，
一个用例都没跑。对照组把同一份 harness 放回去，断言正常跑完。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.isolation_guard import ISOLATION_MARKER, isolation_problems

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]
_PROBE = "def test_probe():\n    assert True\n"


def _materialize(root: Path, *, with_harness: bool) -> Path:
    """把最小可跑的 tests/ 骨架复制到 root；with_harness 决定要不要放隔离夹具."""
    tests_dir = root / "tests"
    (tests_dir / "integration").mkdir(parents=True, exist_ok=True)
    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    for name in ("conftest.py", "isolation_guard.py", "__init__.py"):
        shutil.copy2(_HERE / name, tests_dir / "integration" / name)
    (tests_dir / "integration" / "test_probe.py").write_text(_PROBE, encoding="utf-8")
    if with_harness:
        (tests_dir / "conftest.py").write_text(
            "import os\n"
            "from pathlib import Path\n"
            "_TMP = Path(__file__).resolve().parent.parent / %r\n"
            "_TMP.mkdir(parents=True, exist_ok=True)\n"
            "os.environ['OUTPUT_BASE'] = str(_TMP)\n" % ISOLATION_MARKER,
            encoding="utf-8")
    return tests_dir


def _run_pytest(root: Path) -> subprocess.CompletedProcess:
    env = {"PATH": __import__("os").environ.get("PATH", ""),
           "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
           "TEMP": __import__("os").environ.get("TEMP", ""),
           "TMP": __import__("os").environ.get("TMP", ""),
           # 子进程在中文 Windows 上默认用 GBK 输出；父进程按 UTF-8 解码会
           # 在 reader 线程抛 UnicodeDecodeError，stdout 变空、诊断丢失。
           "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "tests/integration", "-q",
         "-p", "no:cacheprovider", "--rootdir", str(root)],
        cwd=str(root), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300)


def test_current_session_is_isolated():
    assert isolation_problems() == []
    import shared.paths as paths
    # 生产路径派生入口：章节目录必须落在隔离根里
    assert ISOLATION_MARKER in Path(str(paths._output_base())).parts
    assert ISOLATION_MARKER in Path(str(paths.chapter_dir())).parts


def test_refuses_to_start_when_isolation_harness_is_missing(tmp_path):
    root = tmp_path / "no_harness"
    _materialize(root, with_harness=False)
    proc = _run_pytest(root)
    blob = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 4, blob[-3000:]
    assert "refusing_to_start_without_output_isolation" in blob
    assert "test_probe" not in blob  # 一个用例都没跑


def test_starts_normally_when_isolation_harness_present(tmp_path):
    root = tmp_path / "with_harness"
    _materialize(root, with_harness=True)
    proc = _run_pytest(root)
    blob = (proc.stdout or "") + (proc.stderr or "")
    assert proc.returncode == 0, blob[-3000:]
    assert "1 passed" in blob
