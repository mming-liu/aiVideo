# -*- coding: utf-8 -*-
"""会话级磁盘治理的判别性测试（`tests/conftest.py`「3) 会话级磁盘治理」）.

为什么要单独钉住：`.pytest_tmp` 曾涨到 **189 GB / 33.8 万文件**，成因是
`motion_revise_<pid>` 之类的 PID 隔离根从不回收。治理逻辑写在 conftest 的
session fixture 里，而 fixture 每个会话只跑一次——只看「盘上现在小了」无法区分
「回收生效」与「有人手工删过」。这里直接对两个纯函数做故障注入：

* 死 PID 的目录**必须删**（负例）；
* 当前进程 / 存活 PID 的目录**必须留**（正例，防误删正在跑的会话）；
* 不符合 `<前缀><数字>` 形态的目录**不碰**（防误伤普通目录）；
* 只有**超限**日志被截断，小日志原样保留。
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

_CONFTEST_PATH = Path(__file__).resolve().parent / "conftest.py"

#: 一个几乎不可能存在的 PID：Windows OpenProcess / POSIX kill 都会判为不存在。
#: 不用 0/1 这类特殊值，避免平台语义差异把用例变成假阳性。
_DEAD_PID = 4000000


def _load_conftest():
    spec = importlib.util.spec_from_file_location("_aivideo_tests_conftest", _CONFTEST_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def conftest_mod():
    return _load_conftest()


def _mkdir_with_file(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "payload.bin").write_bytes(b"x" * 32)
    return path


def test_probe_validity_dead_pid_is_reported_dead(conftest_mod):
    """先证明判据本身有效：否则「死 PID 被删」可能只是因为判据恒为假."""
    assert conftest_mod._pid_is_alive(_DEAD_PID) is False
    assert conftest_mod._pid_is_alive(os.getpid()) is True


def test_reaper_removes_dead_pid_dirs_and_keeps_live_ones(conftest_mod, tmp_path):
    dead_revise = _mkdir_with_file(tmp_path / f"motion_revise_{_DEAD_PID}")
    dead_cli = _mkdir_with_file(tmp_path / f"motion_cli_{_DEAD_PID}")
    dead_fixtures = _mkdir_with_file(tmp_path / f"motion_fixtures_{_DEAD_PID}")
    live_revise = _mkdir_with_file(tmp_path / f"motion_revise_{os.getpid()}")
    # 非本轮治理目标：真实章节目录与不合形态的名字都必须留着
    keep_chapter = _mkdir_with_file(tmp_path / "锦衣夜行")
    keep_non_numeric = _mkdir_with_file(tmp_path / "motion_revise_abc")

    survivors = conftest_mod._reap_stale_pid_dirs(tmp_path, os.getpid())

    assert not dead_revise.exists(), "死 PID 的 motion_revise_ 必须被回收"
    assert not dead_cli.exists(), "死 PID 的 motion_cli_ 必须被回收"
    assert not dead_fixtures.exists(), "死 PID 的 motion_fixtures_ 必须被回收"
    assert live_revise.exists(), "当前进程的隔离根必须保留（否则会删掉正在跑的会话）"
    assert keep_chapter.exists(), "非 PID 形态的目录不得被误删"
    assert keep_non_numeric.exists(), "后缀非数字的目录不得被误删"
    assert set(survivors) == {live_revise.name}


def test_reaper_survives_missing_root(conftest_mod, tmp_path):
    """根目录不存在时不抛异常（会话可能在干净检出上首次运行）."""
    assert conftest_mod._reap_stale_pid_dirs(tmp_path / "nope", os.getpid()) == []


def test_log_cap_truncates_only_oversized_logs(conftest_mod, tmp_path):
    small = tmp_path / "small.log"
    small.write_text("keep me\n" * 10, encoding="utf-8")
    big = tmp_path / "chapter" / "step01.log"
    big.parent.mkdir(parents=True, exist_ok=True)
    cap = conftest_mod._TEST_LOG_CAP_BYTES
    big.write_bytes(b"A" * (cap + 1))

    truncated = conftest_mod._truncate_oversized_test_logs(tmp_path)

    assert truncated == 1
    assert "truncated by tests/conftest.py" in big.read_text(encoding="utf-8")
    assert big.stat().st_size < cap
    assert small.read_text(encoding="utf-8") == "keep me\n" * 10, "小日志不得被改动"
