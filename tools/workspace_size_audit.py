# -*- coding: utf-8 -*-
"""工作区体积/文件数体检（**只读、零副作用**）.

按顶层目录与 B1–B5 五类膨胀源输出精确的字节数、文件数、Top 30 最大目录/文件，
并区分 ``git_tracked`` / ``git_internal`` / ``ignored_runtime_deps`` /
``ignored_production_output`` / ``ignored_agent_staging`` / ``other_untracked``。

用法::

    python tools/workspace_size_audit.py [--root .] [--out-dir docs/step01-layered-execution/artifacts]
    python tools/workspace_size_audit.py --no-write      # 只打印 JSON

不删除、不移动、不写项目文件（除 ``--out-dir`` 下的报告）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

#: 膨胀源分类（B1–B5）——路径前缀/片段 → 类别。
B1_PREFIXES = tuple("output_step01_%s_20261105" % s for s in (
    "n03fix", "n03fix2", "n03fix3", "n03fix4", "n03fix5", "rerun"))
B1_EXTRA = ("d12_work",)

B2_PARTS = ("tools/motion_runtime/node_modules", "tools/motion_runtime/.browsers",
            ".venv", "venv", ".conda")

B3_PREFIXES = (".dsh", ".p1a_exp", ".clean_p1a", ".workbuddy", ".codex",
               "baselines", "tools/dump", "tools/ab_results", ".review_tmp")

B4_PREFIXES = ("output",)

B5_PREFIXES = ("skills/seedance-2.0/assets", ".git")


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _category(rel: str, tracked: bool) -> str:
    if rel == ".git" or rel.startswith(".git/"):
        return "git_internal"
    if rel.startswith(B2_PARTS):
        return "ignored_runtime_deps"
    if rel.startswith(B4_PREFIXES):
        return "ignored_production_output"
    if rel.startswith(B3_PREFIXES):
        return "ignored_agent_staging"
    if tracked:
        return "git_tracked"
    return "other_untracked"


def _b_category(rel: str) -> str:
    if rel.startswith(B1_PREFIXES) or any(
            rel == p or rel.startswith(p + "/") for p in B1_EXTRA):
        return "B1_output_step01_snapshots"
    if rel.startswith(B2_PARTS):
        return "B2_runtime_deps"
    if rel.startswith(B3_PREFIXES):
        return "B3_baselines_and_agent_staging"
    if rel.startswith(B4_PREFIXES):
        return "B4_output_accumulation"
    if rel.startswith(B5_PREFIXES):
        return "B5_git_objects_and_promo_assets"
    return ""


def _tracked_files(root: Path) -> set:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return set()
    return {p for p in out.decode("utf-8", "replace").split("\0") if p}


def audit(root: Path, top_n: int = 30) -> dict:
    tracked = _tracked_files(root)
    by_top: dict[str, dict] = defaultdict(lambda: {"bytes": 0, "files": 0})
    by_category: dict[str, dict] = defaultdict(lambda: {"bytes": 0, "files": 0})
    by_b: dict[str, dict] = defaultdict(lambda: {"bytes": 0, "files": 0})
    dirs: dict[str, dict] = defaultdict(lambda: {"bytes": 0, "files": 0})
    files: list[tuple[int, str]] = []
    total_bytes = 0
    total_files = 0
    for dirpath, dirnames, filenames in os.walk(root):
        here = Path(dirpath)
        # 不进入 .git 的对象目录（单独按顶层计），但统计其文件大小。
        for name in filenames:
            path = here / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            rel = _rel(path, root)
            top = rel.split("/", 1)[0] if "/" in rel else rel
            cat = _category(rel, rel in tracked)
            bcat = _b_category(rel)
            total_bytes += size
            total_files += 1
            for bucket in (by_top[top], by_category[cat]):
                bucket["bytes"] += size
                bucket["files"] += 1
            if bcat:
                by_b[bcat]["bytes"] += size
                by_b[bcat]["files"] += 1
            d = rel.rsplit("/", 1)[0] if "/" in rel else "."
            dirs[d]["bytes"] += size
            dirs[d]["files"] += 1
            files.append((size, rel))
    top_dirs = sorted(dirs.items(), key=lambda kv: kv[1]["bytes"], reverse=True)[:top_n]
    top_files = sorted(files, reverse=True)[:top_n]
    return {
        "root": str(root),
        "total": {"bytes": total_bytes, "files": total_files},
        "by_top_dir": dict(sorted(by_top.items(), key=lambda kv: kv[1]["bytes"],
                                  reverse=True)),
        "by_category": dict(sorted(by_category.items(),
                                   key=lambda kv: kv[1]["bytes"], reverse=True)),
        "by_b_source": dict(sorted(by_b.items(), key=lambda kv: kv[1]["bytes"],
                                   reverse=True)),
        "top_dirs": [{"path": k, "bytes": v["bytes"], "files": v["files"]}
                     for k, v in top_dirs],
        "top_files": [{"path": p, "bytes": s} for s, p in top_files],
        "tracked_files": len(tracked),
    }


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0
    return "%d B" % n


def _markdown(report: dict) -> str:
    out = ["# 工作区体积体检（只读）", "",
           "- 根目录：`%s`" % report["root"],
           "- 合计：**%s / %s 文件**" % (_human(report["total"]["bytes"]),
                                         report["total"]["files"]),
           "- git 跟踪文件：%s" % report["tracked_files"], "",
           "## 顶层目录", "", "| 目录 | 体积 | 文件数 |", "|---|---:|---:|"]
    for name, row in report["by_top_dir"].items():
        out.append("| `%s` | %s | %d |" % (name, _human(row["bytes"]), row["files"]))
    out += ["", "## 分类", "", "| 类别 | 体积 | 文件数 |", "|---|---:|---:|"]
    for name, row in report["by_category"].items():
        out.append("| %s | %s | %d |" % (name, _human(row["bytes"]), row["files"]))
    out += ["", "## B1–B5 膨胀源", "", "| 编号 | 体积 | 文件数 |", "|---|---:|---:|"]
    for name, row in report["by_b_source"].items():
        out.append("| %s | %s | %d |" % (name, _human(row["bytes"]), row["files"]))
    out += ["", "## Top 30 目录", "", "| 目录 | 体积 | 文件数 |", "|---|---:|---:|"]
    for row in report["top_dirs"]:
        out.append("| `%s` | %s | %d |" % (row["path"], _human(row["bytes"]),
                                            row["files"]))
    out += ["", "## Top 30 文件", "", "| 文件 | 体积 |", "|---|---:|"]
    for row in report["top_files"]:
        out.append("| `%s` | %s |" % (row["path"], _human(row["bytes"])))
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".", help="项目根（默认当前目录）")
    ap.add_argument("--out-dir", default="docs/step01-layered-execution/artifacts",
                    help="报告输出目录（默认 docs/.../artifacts）")
    ap.add_argument("--no-write", action="store_true", help="只打印 JSON，不写报告")
    # 报告名里的日期戳**不再写死**：写死会让第二天重跑覆盖同一份文件、
    # 让"最新体检"变成"某一天的体检"，也让回归无法断言产出文件名。
    ap.add_argument("--stamp", default=_dt.date.today().isoformat(),
                    help="报告文件名日期戳，默认今天（YYYY-MM-DD）")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    report = audit(root)
    payload = json.dumps(report, ensure_ascii=False, indent=1)
    if args.no_write:
        print(payload)
        return 0
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = _ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = args.stamp
    (out_dir / ("workspace-size-audit-%s.json" % stamp)).write_text(
        payload, encoding="utf-8")
    (out_dir / ("workspace-size-audit-%s.md" % stamp)).write_text(
        _markdown(report), encoding="utf-8")
    print("total: %s / %d files" % (_human(report["total"]["bytes"]),
                                    report["total"]["files"]))
    print("report: %s" % (out_dir / ("workspace-size-audit-%s.md" % stamp)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
