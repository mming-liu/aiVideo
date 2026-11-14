# -*- coding: utf-8 -*-
"""会话外清理测试临时产物（默认 dry-run，只扫描不删除）.

`tests/conftest.py` 在**会话开始**回收遗留根；本工具用于**会话外**彻底清盘
（例如一天跑完多轮后），或排查「为什么 `.pytest_tmp` 又大了」。

用法：
    python tools/cleanup_test_artifacts.py                    # 只扫描，输出报告
    python tools/cleanup_test_artifacts.py --apply            # 移入系统回收站（可逆）
    python tools/cleanup_test_artifacts.py --apply --permanent  # 直接删除（释放磁盘）
    python tools/cleanup_test_artifacts.py --keep-hours 24 --keep-latest 2
    python tools/cleanup_test_artifacts.py --root .pytest_tmp --json-out report.json

安全边界：
- 只处理已知测试根（项目 `.pytest_tmp`、`%TEMP%/pytest-of-<user>`）；
- 活进程/当前进程的 PID 根永远保留；每个前缀保留最近 N 个；
- 候选必须早于 `--keep-hours`（默认 6 小时）；
- 默认 `--apply` 走回收站（可逆）；`--permanent` 才直接删除；
- 任何删除失败都保留现场并写进报告。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.test_scratch import (  # noqa: E402
    DEFAULT_KEEP_LATEST,
    DEFAULT_MIN_AGE_SEC,
    plan_basetemp_reap,
    plan_pid_reap,
    plan_pytest_tmp_reap,
    plan_to_dict,
    remove_tree,
)


def _default_roots() -> list[Path]:
    roots = [_ROOT / ".pytest_tmp"]
    try:
        import getpass
        import tempfile

        roots.append(Path(tempfile.gettempdir())
                     / ("pytest-of-%s" % getpass.getuser()))
    except Exception:
        pass
    return roots


def _plan_root(root: Path, *, keep_hours: float, keep_latest: int,
               exclude: set | None = None) -> dict:
    if root.name.startswith("pytest-of-"):
        plan = plan_pytest_tmp_reap(
            root, min_age_sec=keep_hours * 3600,
            keep_latest=keep_latest, measure=True)
        return {"root": str(root), "kind": "pytest_tmp",
                **plan_to_dict(plan)}
    return {
        "root": str(root),
        "kind": "pytest_tmp",
        "pid_roots": plan_to_dict(plan_pid_reap(
            root, current_pid=os.getpid(), min_age_sec=keep_hours * 3600,
            keep_latest=keep_latest, measure=True)),
        "basetemps": plan_to_dict(plan_basetemp_reap(
            root, min_age_sec=keep_hours * 3600, keep_latest=keep_latest,
            measure=True, exclude=exclude or set())),
    }


def _iter_candidates(section: dict):
    """遍历一个根报告里的候选（pid_roots / basetemps / pytest_tmp 单层）."""
    if "pid_roots" in section:
        yield from section["pid_roots"].get("reap") or []
        yield from section["basetemps"].get("reap") or []
    else:
        yield from section.get("reap") or []


def build_report(*, roots: list[Path], keep_hours: float, keep_latest: int,
                 exclude: set | None = None) -> dict:
    sections = []
    for root in roots:
        if not root.exists():
            sections.append({"root": str(root), "kind": "missing"})
            continue
        sections.append(_plan_root(root, keep_hours=keep_hours,
                                   keep_latest=keep_latest, exclude=exclude))
    candidates = [item for section in sections
                  for item in _iter_candidates(section)]
    return {
        "schema": "test_scratch.cleanup.v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "keep_hours": keep_hours,
        "keep_latest": keep_latest,
        "roots": sections,
        "summary": {
            "candidate_roots": len(candidates),
            "candidate_bytes": sum(int(item.get("size_bytes") or 0)
                                   for item in candidates),
            "candidate_files": sum(int(item.get("files") or 0)
                                   for item in candidates),
        },
    }


def apply_report(report: dict, *, permanent: bool) -> dict:
    """按报告回收；返回 `{"removed": n, "freed_bytes": x, "failed": [...]}`."""
    removed = 0
    freed = 0
    failed: list[dict] = []
    for section in report.get("roots") or []:
        for item in _iter_candidates(section):
            path = Path(str(item.get("path") or ""))
            ok, method = remove_tree(path, permanent=permanent)
            if ok:
                removed += 1
                freed += int(item.get("size_bytes") or 0)
            else:
                failed.append({"path": str(path), "reason": method})
    return {"removed": removed, "freed_bytes": freed, "failed": failed}


def _human(n: int) -> str:
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return "%.1f %s" % (value, unit)
        value /= 1024
    return "%.1f TB" % value


def _print_report(report: dict, result: dict | None, *, permanent: bool) -> None:
    print("测试临时产物清理报告（%s）" % report["generated_at"])
    print("保留策略：最近 %d 个/前缀；早于 %s 小时才回收"
          % (report["keep_latest"], report["keep_hours"]))
    for section in report["roots"]:
        root = section.get("root")
        if section.get("kind") == "missing":
            print("  [缺失] %s" % root)
            continue
        print("  [根] %s" % root)
        if "pid_roots" in section:
            for label, key in (("PID 根", "pid_roots"),
                               ("basetemp", "basetemps")):
                plan = section.get(key) or {}
                for item in plan.get("reap") or []:
                    print("    可回收 %-8s %10s %6d 文件  %s"
                          % (label, _human(item.get("size_bytes") or 0),
                             item.get("files") or 0, item.get("path")))
                for item in plan.get("active") or []:
                    print("    保留   %-8s（活动进程）%s"
                          % (label, item.get("path")))
        else:
            for item in section.get("reap") or []:
                print("    可回收 %-8s %10s %6d 文件  %s"
                      % ("pytest", _human(item.get("size_bytes") or 0),
                         item.get("files") or 0, item.get("path")))
    summary = report["summary"]
    print("候选：%d 个根 / %s / %d 文件"
          % (summary["candidate_roots"],
             _human(summary["candidate_bytes"]),
             summary["candidate_files"]))
    if result is not None:
        print("已回收：%d 个根 / %s（%s）"
              % (result["removed"], _human(result["freed_bytes"]),
                 "永久删除" if permanent else "移入回收站"))
        for item in result["failed"][:10]:
            print("  失败：%s（%s）" % (item["path"], item["reason"]))
    else:
        print("dry-run：未删除任何文件；确认后加 --apply（默认回收站）"
              "或 --apply --permanent（直接释放磁盘）")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="清理测试临时产物（默认 dry-run）")
    parser.add_argument("--root", action="append", default=None,
                        help="测试临时根；可重复（默认 .pytest_tmp + pytest-of-<user>）")
    parser.add_argument("--keep-hours", type=float, default=DEFAULT_MIN_AGE_SEC / 3600,
                        help="只回收早于 N 小时的候选（默认 6）")
    parser.add_argument("--keep-latest", type=int, default=DEFAULT_KEEP_LATEST,
                        help="每个前缀保留最近 N 个（默认 2）")
    parser.add_argument("--exclude", action="append", default=None,
                        help="必须保留的路径（可重复；如本轮 --basetemp）")
    parser.add_argument("--apply", action="store_true",
                        help="执行回收（默认只扫描）")
    parser.add_argument("--permanent", action="store_true",
                        help="直接删除；不设则移入系统回收站")
    parser.add_argument("--json-out", default=None,
                        help="把报告写到指定 JSON 路径")
    args = parser.parse_args(argv)

    roots = [Path(p) for p in (args.root or _default_roots())]
    exclude = {Path(p).resolve() for p in (args.exclude or [])}
    report = build_report(roots=roots, keep_hours=args.keep_hours,
                          keep_latest=args.keep_latest, exclude=exclude)
    result = apply_report(report, permanent=args.permanent) if args.apply else None
    report["apply"] = bool(args.apply)
    report["permanent"] = bool(args.permanent)
    report["result"] = result
    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    _print_report(report, result, permanent=args.permanent)
    if result is not None and result["failed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
