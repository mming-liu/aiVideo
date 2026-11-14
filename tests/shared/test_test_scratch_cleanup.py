# -*- coding: utf-8 -*-
"""测试临时产物治理：计划/保留策略/删除语义/清理工具（离线、零模型调用）.

对应 `shared/test_scratch.py` 与 `tools/cleanup_test_artifacts.py`。
所有用例在 `tmp_path` 内构造假 PID 根/旧 basetemp，不触碰真实 `.pytest_tmp`。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(_ROOT / "tools"))

import shared.test_scratch as ts  # noqa: E402
import cleanup_test_artifacts as tool  # noqa: E402


def _make_dir(root: Path, name: str, *, age_sec: float, files: int = 2) -> Path:
    path = root / name
    path.mkdir(parents=True)
    for i in range(files):
        (path / ("f%d.bin" % i)).write_bytes(b"x" * 1024)
    stamp = time.time() - age_sec
    os.utime(path, (stamp, stamp))
    return path


def test_parse_pid_root():
    assert ts.parse_pid_root("motion_revise_123") == ("motion_revise_", 123)
    assert ts.parse_pid_root("motion_cli_7") == ("motion_cli_", 7)
    assert ts.parse_pid_root("motion_revise_abc") is None
    assert ts.parse_pid_root("merge8") is None


def test_plan_pid_reap_keeps_live_current_recent_and_latest(tmp_path, monkeypatch):
    old_dead = _make_dir(tmp_path, "motion_revise_111", age_sec=10 * 3600)
    _make_dir(tmp_path, "motion_revise_222", age_sec=10 * 3600)   # 假活进程
    new_dead = _make_dir(tmp_path, "motion_revise_333", age_sec=60)  # 太新
    current = _make_dir(tmp_path, "motion_revise_%d" % os.getpid(),
                        age_sec=10 * 3600)
    monkeypatch.setattr(ts, "pid_is_alive", lambda pid: pid == 222)

    plan = ts.plan_pid_reap(tmp_path, current_pid=os.getpid(),
                            min_age_sec=6 * 3600, keep_latest=1)
    reap_names = {item.path.name for item in plan["reap"]}
    keep_names = {item.path.name for item in plan["keep"]}
    active_names = {item.path.name for item in plan["active"]}
    assert reap_names == {old_dead.name}
    # keep_latest=1 保留最新的死进程根；current/active 各自保留
    assert keep_names == {new_dead.name}
    assert active_names == {"motion_revise_222", current.name}
    assert old_dead.exists(), "计划阶段不得删除任何文件"


def test_plan_basetemp_reap_exclude_current(tmp_path):
    old = _make_dir(tmp_path, "run_old", age_sec=10 * 3600)
    new = _make_dir(tmp_path, "run_new", age_sec=60)
    plan = ts.plan_basetemp_reap(tmp_path, min_age_sec=6 * 3600,
                                 keep_latest=1, exclude={old})
    assert [item.path.name for item in plan["reap"]] == []
    assert {item.path.name for item in plan["keep"]} == {new.name}
    # 不排除时，旧目录（且超过 keep_latest）进入候选
    plan2 = ts.plan_basetemp_reap(tmp_path, min_age_sec=6 * 3600,
                                  keep_latest=1)
    assert {item.path.name for item in plan2["reap"]} == {old.name}


def test_plan_pytest_tmp_reap_keeps_latest(tmp_path):
    _make_dir(tmp_path, "pytest-1", age_sec=10 * 3600)
    _make_dir(tmp_path, "pytest-2", age_sec=10 * 3600)
    _make_dir(tmp_path, "pytest-3", age_sec=60)
    plan = ts.plan_pytest_tmp_reap(tmp_path, min_age_sec=6 * 3600,
                                   keep_latest=1)
    assert {item.path.name for item in plan["reap"]} == {"pytest-1", "pytest-2"}
    assert {item.path.name for item in plan["keep"]} == {"pytest-3"}


def test_remove_tree_permanent_and_recycle(tmp_path, monkeypatch):
    target = _make_dir(tmp_path, "a", age_sec=0)
    ok, method = ts.remove_tree(target, permanent=True)
    assert ok and method == "removed" and not target.exists()

    target2 = _make_dir(tmp_path, "b", age_sec=0)
    monkeypatch.setattr(ts, "send_to_recycle", lambda p: True)
    ok, method = ts.remove_tree(target2, permanent=False)
    assert ok and method == "recycled"

    target3 = _make_dir(tmp_path, "c", age_sec=0)
    monkeypatch.setattr(ts, "send_to_recycle", lambda p: False)
    ok, method = ts.remove_tree(target3, permanent=False)
    assert not ok and method == "recycle_unavailable"
    assert target3.exists(), "回收站不可用时不得回退到永久删除"


def test_keep_sentinel_protects_old_basetemp(tmp_path):
    """`.keep` 标记的目录即使很旧也不得回收（长期证据安全网）."""
    keep = _make_dir(tmp_path, "u6_closeout", age_sec=10 * 3600)
    (keep / ".keep").write_text("long-lived evidence\n", encoding="utf-8")
    old = _make_dir(tmp_path, "run_old", age_sec=10 * 3600)
    plan = ts.plan_basetemp_reap(tmp_path, min_age_sec=6 * 3600,
                                 keep_latest=0)
    reap_names = {item.path.name for item in plan["reap"]}
    keep_names = {item.path.name for item in plan["keep"]}
    assert reap_names == {old.name}
    assert keep_names == {keep.name}
    assert plan["keep"][0].reason == "keep_sentinel"


def test_keep_sentinel_protects_dead_pid_root(tmp_path, monkeypatch):
    root = _make_dir(tmp_path, "motion_revise_111", age_sec=10 * 3600)
    (root / "KEEP").write_text("do not reap\n", encoding="utf-8")
    monkeypatch.setattr(ts, "pid_is_alive", lambda pid: False)
    plan = ts.plan_pid_reap(tmp_path, current_pid=os.getpid(),
                            min_age_sec=6 * 3600, keep_latest=0)
    assert plan["reap"] == []
    assert [item.path.name for item in plan["keep"]] == ["motion_revise_111"]


def test_tool_dry_run_then_apply(tmp_path):
    root = tmp_path / "pytest_tmp"
    root.mkdir()
    old = _make_dir(root, "run_old", age_sec=10 * 3600)
    new = _make_dir(root, "run_new", age_sec=60)
    report_path = tmp_path / "report.json"

    rc = tool.main(["--root", str(root), "--keep-latest", "1",
                    "--json-out", str(report_path)])
    assert rc == 0
    assert old.exists(), "dry-run 不得删除任何文件"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["candidate_roots"] == 1
    assert report["summary"]["candidate_files"] >= 2

    rc = tool.main(["--root", str(root), "--keep-latest", "1",
                    "--apply", "--permanent", "--json-out", str(report_path)])
    assert rc == 0
    assert not old.exists()
    assert new.exists(), "保留窗口内的 basetemp 不得被删除"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["result"]["removed"] == 1


def test_tool_pid_roots_keep_active(tmp_path, monkeypatch):
    root = tmp_path / "pytest_tmp"
    root.mkdir()
    old = _make_dir(root, "motion_revise_111", age_sec=10 * 3600)
    live = _make_dir(root, "motion_revise_222", age_sec=10 * 3600)
    monkeypatch.setattr(ts, "pid_is_alive", lambda pid: pid == 222)
    rc = tool.main(["--root", str(root), "--keep-latest", "0",
                    "--apply", "--permanent"])
    assert rc == 0
    assert not old.exists()
    assert live.exists()
