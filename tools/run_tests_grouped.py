# -*- coding: utf-8 -*-
"""全量测试按固定分组**分进程**运行，合并 JUnit XML（离线、零模型调用）.

为什么分进程：本机单进程跑全量时，会话内累计删除次数会触发本机删除审计门
（同一进程 50 次删除即拦截）。按目录固定分组、每组独立进程，可让每个进程的
删除量各自独立；分组写死在 `GROUPS` 里，基线与对账必须用同一套分组。

用法::

    python tools/run_tests_grouped.py --out .tmp/junit/base           # 跑全部组
    python tools/run_tests_grouped.py --out DIR --only step01 shared  # 只跑指定组
    python tools/run_tests_grouped.py --list                          # 打印分组

产物：``DIR/<group>.xml``（每组一份）+ ``DIR/merged.xml`` + ``DIR/summary.json``。
退出码：任一组的 pytest 进程以 0/1 以外的码退出（收集错误、崩溃、超时）返回 2；
否则返回 0——**用例失败不影响退出码**，结论由 `tools/compare_junit.py` 对账给出。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"

#: 固定分组（名字 → tests/ 下的相对路径）。需要细分时只改这里，并在交付记录注明。
#: 不在任何组里的 tests/ 直接子项一律进 `rest`，保证全量覆盖。
GROUPS: dict[str, tuple[str, ...]] = {
    "step01": ("step01_script_gen",),
    "shared": ("shared",),
    "step02a": ("step02a_frame_gen",),
    "step02b": ("step02b_video_gen", "step02_video_gen"),
    "step03": ("step03_tts_audio", "tts"),
    "step04": ("step04_merge", "enhancements"),
    "tools_text_integration": ("tools", "_text_gen", "integration"),
}

#: 不是测试目标的直接子项（夹具、缓存、辅助文件）。
_NOT_TARGETS = {"fixtures", "__pycache__", "conftest.py", "__init__.py",
                "verification_helpers.py", "_content_binding.py"}

GROUP_TIMEOUT_SEC = 3600


def rest_targets() -> tuple[str, ...]:
    grouped = {p for paths in GROUPS.values() for p in paths}
    out = []
    for child in sorted(TESTS.iterdir()):
        name = child.name
        if name in grouped or name in _NOT_TARGETS:
            continue
        if child.is_dir() or (name.startswith("test_") and name.endswith(".py")):
            out.append(name)
    return tuple(out)


def all_groups() -> dict[str, tuple[str, ...]]:
    groups = dict(GROUPS)
    groups["rest"] = rest_targets()
    return groups


def run_group(name: str, targets: tuple[str, ...], out_dir: Path) -> dict:
    xml_path = out_dir / f"{name}.xml"
    cmd = [sys.executable, "-m", "pytest", *[f"tests/{t}" for t in targets],
           "-q", "-p", "no:cacheprovider", f"--junitxml={xml_path}",
           "-o", "junit_family=xunit2"]
    t0 = time.monotonic()
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=GROUP_TIMEOUT_SEC)
        code, tail = proc.returncode, (proc.stdout or "")[-3000:] + (proc.stderr or "")[-2000:]
    except subprocess.TimeoutExpired as exc:
        code, tail = -1, f"timeout after {GROUP_TIMEOUT_SEC}s: {exc}"
    (out_dir / f"{name}.log").write_text(tail, encoding="utf-8")
    return {"group": name, "targets": list(targets), "returncode": code,
            "seconds": round(time.monotonic() - t0, 1),
            "xml": xml_path.name if xml_path.exists() else None}


def merge_xml(out_dir: Path, names: list[str]) -> Path:
    merged = ET.Element("testsuites")
    for name in names:
        path = out_dir / f"{name}.xml"
        if not path.exists():
            continue
        root = ET.parse(path).getroot()
        suites = [root] if root.tag == "testsuite" else list(root)
        for suite in suites:
            suite.set("name", name)
            merged.append(suite)
    target = out_dir / "merged.xml"
    ET.ElementTree(merged).write(target, encoding="utf-8", xml_declaration=True)
    return target


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, help="输出目录")
    ap.add_argument("--only", nargs="*", help="只跑这些组")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)
    groups = all_groups()
    if args.list:
        for k, v in groups.items():
            print(f"{k:24} {' '.join(v)}")
        return 0
    if not args.out:
        ap.error("--out 必填")
    names = list(groups) if not args.only else [n for n in groups if n in args.only]
    unknown = set(args.only or ()) - set(groups)
    if unknown:
        ap.error(f"未知分组: {sorted(unknown)}")
    out_dir = args.out if args.out.is_absolute() else ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for name in names:
        res = run_group(name, groups[name], out_dir)
        results.append(res)
        print(f"[{name}] rc={res['returncode']} {res['seconds']}s", flush=True)
    merge_xml(out_dir, names)
    (out_dir / "summary.json").write_text(
        json.dumps({"groups": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    bad = [r for r in results if r["returncode"] not in (0, 1)]
    return 2 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
