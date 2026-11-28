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
import hashlib
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


#: 核心代码区红线（2026-11-28 O13）：代码+测试的磁盘占用不得超过 200 MB。
CORE_RED_LINE_MB = 200.0
#: `.tmp/` 子目录超过这么多天没有更新就算过期孤儿。
STALE_DAYS = 14
#: 重复大文件检查的起算大小（更小的文件哈希成本高、清理收益小）。
DUP_MIN_BYTES = 16 * 1024 * 1024
#: 单章 `snapshots/` 超过这个数量就提示堆积。
SNAPSHOT_WARN_COUNT = 30

CORE_PREFIXES = ("shared", "step01_script_gen", "step02a_frame_gen",
                 "step02b_video_gen", "step03_tts_audio", "step04_merge",
                 "tts", "tools", "_text_gen", "_image_gen", "_video_gen", "tests")


def _tree_bytes(path: Path) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                total += (Path(dirpath) / name).stat().st_size
            except OSError:
                continue
    return total


def _newest_mtime(path: Path) -> float:
    """目录内**最新内容**的 mtime；目录本身被 touch 过不算（避免误判为新鲜）."""
    newest = 0.0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                newest = max(newest, (Path(dirpath) / name).stat().st_mtime)
            except OSError:
                continue
    if newest:
        return newest
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def check_thresholds(root: Path, *, stale_days: int = STALE_DAYS,
                     core_limit_bytes: int = int(CORE_RED_LINE_MB * 1024 * 1024),
                     now: float | None = None,
                     dup_min_bytes: int = DUP_MIN_BYTES,
                     snapshot_warn: int = SNAPSHOT_WARN_COUNT) -> dict:
    """只读水位巡检：**绝不删除**，只报告越界项与清理建议（确定性，无随机）."""
    root = Path(root)
    now_ts = float(now if now is not None else _dt.datetime.now().timestamp())
    checks: list[dict] = []
    suggestions: list[str] = []

    # 1) 核心代码区体积红线
    core_bytes = 0
    for name in CORE_PREFIXES:
        target = root / name
        if target.is_dir():
            core_bytes += _tree_bytes(target)
    for path in sorted(root.glob("*.py")):
        try:
            core_bytes += path.stat().st_size
        except OSError:
            continue
    core_ok = core_bytes <= core_limit_bytes
    checks.append({"id": "core_code_size", "ok": core_ok, "bytes": core_bytes,
                   "limit_bytes": core_limit_bytes,
                   "detail": "%s / %s" % (_human(core_bytes), _human(core_limit_bytes))})
    if not core_ok:
        suggestions.append("核心代码区 %s 超过红线 %s：检查是否误提交了产物/日志"
                           % (_human(core_bytes), _human(core_limit_bytes)))

    # 2) `.tmp/` 过期孤儿目录
    tmp = root / ".tmp"
    stale: list[dict] = []
    if tmp.is_dir():
        cutoff = now_ts - max(0, int(stale_days)) * 86400
        for child in sorted(tmp.iterdir()):
            newest = _newest_mtime(child)
            if newest and newest < cutoff:
                stale.append({"path": child.name,
                              "age_days": int((now_ts - newest) // 86400),
                              "bytes": _tree_bytes(child)})
    stale.sort(key=lambda item: item["bytes"], reverse=True)
    checks.append({"id": "tmp_stale_dirs", "ok": not stale,
                   "stale_days": int(stale_days), "count": len(stale),
                   "items": stale[:20], "total_bytes": sum(i["bytes"] for i in stale)})
    if stale:
        suggestions.append("`.tmp/` 有 %d 个超过 %d 天的孤儿目录（共 %s）：确认后可删除 %s"
                           % (len(stale), stale_days,
                              _human(sum(i["bytes"] for i in stale)),
                              "、".join(i["path"] for i in stale[:5])))

    # 3) 重复大文件（同大小 + 同 sha256；跳过 .git 与受控基线）
    by_size: dict[int, list[Path]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "node_modules", "__pycache__")]
        for name in filenames:
            path = Path(dirpath) / name
            rel = _rel(path, root)
            if rel.startswith("baselines/"):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size >= dup_min_bytes:
                by_size.setdefault(size, []).append(path)
    duplicates: list[dict] = []
    for size, paths in sorted(by_size.items(), reverse=True):
        if len(paths) < 2:
            continue
        groups: dict[str, list[str]] = defaultdict(list)
        for path in paths:
            digest = _sha256_file(path)
            if digest:
                groups[digest].append(_rel(path, root))
        for digest, members in groups.items():
            if len(members) > 1:
                duplicates.append({"bytes": size, "sha256": digest[:16],
                                   "paths": sorted(members)[:6],
                                   "count": len(members)})
    duplicates.sort(key=lambda item: item["bytes"], reverse=True)
    checks.append({"id": "duplicate_large_files", "ok": not duplicates,
                   "min_bytes": dup_min_bytes, "count": len(duplicates),
                   "items": duplicates[:10],
                   "wasted_bytes": sum(i["bytes"] * (i["count"] - 1)
                                       for i in duplicates)})
    if duplicates:
        suggestions.append("发现 %d 组重复大文件（可回收 %s）：%s"
                           % (len(duplicates),
                              _human(sum(i["bytes"] * (i["count"] - 1)
                                         for i in duplicates)),
                              duplicates[0]["paths"][0]))

    # 4) `snapshots/` 堆积
    crowded: list[dict] = []
    for chapter in sorted(root.glob("output/*/*/chapter_*")):
        snaps = chapter / "snapshots"
        if not snaps.is_dir():
            continue
        count = sum(1 for p in snaps.iterdir() if p.is_dir())
        if count > snapshot_warn:
            crowded.append({"chapter": _rel(chapter, root), "snapshots": count,
                            "bytes": _tree_bytes(snaps)})
    checks.append({"id": "snapshot_accumulation", "ok": not crowded,
                   "warn_count": snapshot_warn, "count": len(crowded),
                   "items": crowded[:10]})
    if crowded:
        suggestions.append("%d 个章节的 snapshots/ 超过 %d 份（%s）：确认后可清理最旧快照"
                           % (len(crowded), snapshot_warn,
                              crowded[0]["chapter"]))

    violations = [c["id"] for c in checks if not c["ok"]]
    return {"schema": "workspace.thresholds/1", "root": str(root),
            "ok": not violations, "violations": violations, "checks": checks,
            "suggestions": suggestions, "writes": []}


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
    # 2026-11-28 O13：只读水位巡检（不删任何文件，只给清理建议）
    ap.add_argument("--check-thresholds", action="store_true",
                    help="只读阈值巡检：核心代码体积/过期 .tmp 目录/重复大文件/snapshots 堆积"
                         "；有越界项退出码 1")
    ap.add_argument("--stale-days", type=int, default=STALE_DAYS,
                    help="`.tmp/` 子目录超过多少天算过期（默认 %d）" % STALE_DAYS)
    ap.add_argument("--core-limit-mb", type=float, default=CORE_RED_LINE_MB,
                    help="核心代码区红线（MB，默认 %.0f）" % CORE_RED_LINE_MB)
    # 报告名里的日期戳**不再写死**：写死会让第二天重跑覆盖同一份文件、
    # 让"最新体检"变成"某一天的体检"，也让回归无法断言产出文件名。
    ap.add_argument("--stamp", default=_dt.date.today().isoformat(),
                    help="报告文件名日期戳，默认今天（YYYY-MM-DD）")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()
    if args.check_thresholds:
        report = check_thresholds(root, stale_days=max(0, int(args.stale_days)),
                                  core_limit_bytes=int(args.core_limit_mb * 1024 * 1024))
        print(json.dumps(report, ensure_ascii=False, indent=1))
        for item in report["suggestions"]:
            print("[建议] %s" % item)
        return 0 if report["ok"] else 1
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
