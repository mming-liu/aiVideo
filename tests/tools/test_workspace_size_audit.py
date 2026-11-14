# -*- coding: utf-8 -*-
"""`tools/workspace_size_audit.py` 的回归（夹具驱动，只读、只写临时目录）.

它回答两个必须可信的问题：**体积涨在哪**（顶层目录 / B1–B5 膨胀源）与
**这些字节算谁的**（git 跟踪 / .git 内部 / 运行时依赖 / 生产产物 / 代理暂存 /
其他未跟踪）。分类错一格，'回收空间'就会去删正在用的东西，或者对 25GB 的
`node_modules` 视而不见。

夹具是**目录布局**（每个分支造一个最小文件），不含任何作品内容。
顺带钉住两处可测性缺陷的修复：报告名里写死的日期戳、`--no-write` 必须真的不写。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import workspace_size_audit as tool                      # noqa: E402


def _size(path: Path, n: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * n)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    _size(root / "src" / "app.py", 100)                                   # other_untracked
    _size(root / "output" / "clip.bin", 2000)                             # 生产产物 / B4
    _size(root / ".git" / "objects" / "ab" / "cdef", 500)                 # .git 内部 / B5
    _size(root / "tools" / "motion_runtime" / "node_modules" / "p.js", 300)  # 运行时依赖 / B2
    _size(root / ".dsh" / "staging.json", 50)                             # 代理暂存 / B3
    _size(root / "output_step01_n03fix_20261105" / "run.json", 70)        # B1（无顶层类别）
    return root


def test_totals_are_exact(workspace):
    report = tool.audit(workspace)
    assert report["total"] == {"bytes": 100 + 2000 + 500 + 300 + 50 + 70, "files": 6}


@pytest.mark.parametrize("rel,category", [
    ("src/app.py", "other_untracked"),
    ("output/clip.bin", "ignored_production_output"),
    (".git/objects/ab/cdef", "git_internal"),
    ("tools/motion_runtime/node_modules/p.js", "ignored_runtime_deps"),
    (".dsh/staging.json", "ignored_agent_staging"),
])
def test_category_of_each_branch(workspace, rel, category):
    assert tool._category(rel, tracked=False) == category
    report = tool.audit(workspace)
    assert report["by_category"][category]["bytes"] >= (workspace / rel).stat().st_size


def test_output_prefix_also_covers_historical_step01_snapshots(workspace):
    """`output_step01_*`（B1 历史快照）在**归属**上同样是生产产物：
    B1–B5 回答"为什么大"，by_category 回答"这些字节算谁的"，两者正交。"""
    report = tool.audit(workspace)
    assert report["by_b_source"]["B1_output_step01_snapshots"]["files"] == 1
    assert report["by_category"]["ignored_production_output"]["files"] == 2


@pytest.mark.parametrize("b_source", [
    "B1_output_step01_snapshots",
    "B2_runtime_deps",
    "B3_baselines_and_agent_staging",
    "B4_output_accumulation",
    "B5_git_objects_and_promo_assets",
])
def test_every_b_source_bucket_is_reachable(workspace, b_source):
    assert b_source in tool.audit(workspace)["by_b_source"]


def test_git_category_wins_over_b4_prefix(workspace):
    """`.git` 在 `_git_objects` 里，但分类必须先判 .git——否则会被当成生产产物。"""
    _size(workspace / ".git" / "objects" / "zz", 10)
    report = tool.audit(workspace)
    assert report["by_category"]["git_internal"]["files"] == 2


def test_no_tracked_files_outside_a_repo(workspace):
    """临时目录不是 git 仓库：`git ls-files` 失败必须退化成空集合，而不是抛错。"""
    assert tool.audit(workspace)["tracked_files"] == 0


def test_top_n_limits_output(workspace):
    report = tool.audit(workspace, top_n=2)
    assert len(report["top_dirs"]) == 2
    assert len(report["top_files"]) == 2
    assert report["top_files"][0]["bytes"] == 2000


def test_no_write_prints_json_and_writes_nothing(workspace, tmp_path, capsys):
    out_dir = tmp_path / "reports"
    assert tool.main(["--root", str(workspace), "--out-dir", str(out_dir),
                      "--no-write"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["total"]["files"] == 6
    assert not out_dir.exists()


def test_write_uses_the_stamp_and_produces_both_files(workspace, tmp_path):
    out_dir = tmp_path / "reports"
    assert tool.main(["--root", str(workspace), "--out-dir", str(out_dir),
                      "--stamp", "2026-01-02"]) == 0
    json_path = out_dir / "workspace-size-audit-2026-01-02.json"
    md_path = out_dir / "workspace-size-audit-2026-01-02.md"
    assert json_path.is_file() and md_path.is_file()
    assert "# 工作区体积体检（只读）" in md_path.read_text(encoding="utf-8")


def test_default_stamp_is_today_not_a_frozen_date(workspace, tmp_path):
    """报告名里的日期不许写死：写死会让第二天的体检覆盖同一份文件。"""
    import datetime as dt
    out_dir = tmp_path / "reports"
    tool.main(["--root", str(workspace), "--out-dir", str(out_dir)])
    today = dt.date.today().isoformat()
    assert (out_dir / ("workspace-size-audit-%s.json" % today)).is_file()


def test_human_readable_units():
    assert tool._human(999) == "999 B"
    assert tool._human(1024) == "1.0 KB"
    assert tool._human(1024 * 1024) == "1.0 MB"
