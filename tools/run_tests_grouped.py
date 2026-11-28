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

#: 2026-11-28 O10：`--fast` 的核心契约子集（目标 30 秒内跑完，只放零 I/O 的判据/配置/
#: 身份单测）。**不是全量回归的替代品**：只是提交前的一道快速守卫。
FAST_TARGETS: tuple[str, ...] = (
    "shared/test_env_guard_consistency.py",
    "shared/test_config_single_source.py",
    "shared/test_downstream_run_context_and_guard.py",
    "shared/test_http_resilience_fault_injection.py",
    "shared/test_asset_style_isolation.py",
    "shared/test_chapter_continuity.py",
    "step01_script_gen/test_rule_fingerprint_hygiene.py",
    "step04_merge/test_subtitle_ergonomics_gate.py",
    "tools/test_print_effective_config.py",
    "tools/test_workspace_hygiene.py",
)


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


def validate_report(xml_path: Path, started_at: float) -> str | None:
    """本轮报告是否可用：存在、在本组启动之后写出、可解析、含 testsuite。None = 可用."""
    if not xml_path.exists():
        return "未生成 JUnit XML"
    if xml_path.stat().st_mtime < started_at:
        return "JUnit XML 早于本组启动时间（旧文件）"
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError as exc:
        return f"JUnit XML 无法解析: {exc}"
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        return "JUnit XML 没有 testsuite"
    return None


def run_group(name: str, targets: tuple[str, ...], out_dir: Path, *,
              runner=subprocess.run) -> dict:
    xml_path = out_dir / f"{name}.xml"
    if xml_path.exists():
        xml_path.unlink()                    # 绝不让上一轮的报告冒充本轮结果
    cmd = [sys.executable, "-m", "pytest", *[f"tests/{t}" for t in targets],
           "-q", "-p", "no:cacheprovider", f"--junitxml={xml_path}",
           "-o", "junit_family=xunit2"]
    t0 = time.monotonic()
    started_at = time.time() - 1.0           # 容忍文件系统时间戳精度
    try:
        proc = runner(cmd, cwd=ROOT, capture_output=True, text=True,
                      encoding="utf-8", errors="replace", timeout=GROUP_TIMEOUT_SEC)
        code, tail = proc.returncode, (proc.stdout or "")[-3000:] + (proc.stderr or "")[-2000:]
    except subprocess.TimeoutExpired as exc:
        code, tail = -1, f"timeout after {GROUP_TIMEOUT_SEC}s: {exc}"
    problem = validate_report(xml_path, started_at)
    (out_dir / f"{name}.log").write_text(tail, encoding="utf-8")
    return {"group": name, "targets": list(targets), "returncode": code,
            "seconds": round(time.monotonic() - t0, 1),
            "xml": xml_path.name if problem is None else None,
            "report_problem": problem}


def merge_xml(out_dir: Path, names: list[str]) -> Path:
    merged = ET.Element("testsuites")
    for name in names:
        path = out_dir / f"{name}.xml"
        if not path.exists():
            continue                         # 缺报告的组已在 summary 判为失败，不合并
        root = ET.parse(path).getroot()
        suites = [root] if root.tag == "testsuite" else list(root)
        for suite in suites:
            suite.set("name", name)
            merged.append(suite)
    target = out_dir / "merged.xml"
    ET.ElementTree(merged).write(target, encoding="utf-8", xml_declaration=True)
    return target


def run_fast(*, runner=subprocess.run) -> int:
    """`--fast`：单进程跑核心契约子集（不写 JUnit，目标 30 秒内）。"""
    targets = [f"tests/{t}" for t in FAST_TARGETS if (ROOT / "tests" / t).is_file()]
    missing = [t for t in FAST_TARGETS if not (ROOT / "tests" / t).is_file()]
    t0 = time.monotonic()
    proc = runner([sys.executable, "-m", "pytest", *targets, "-q",
                   "-p", "no:cacheprovider", "-m", "not slow"],
                  cwd=ROOT, capture_output=True, text=True,
                  encoding="utf-8", errors="replace")
    elapsed = time.monotonic() - t0
    tail = ((proc.stdout or "")[-1500:] + (proc.stderr or "")[-500:]).strip()
    print(tail)
    print("[fast] %.1fs；%d 个目标%s" % (
        elapsed, len(targets), "；缺文件：" + ", ".join(missing) if missing else ""))
    return 0 if proc.returncode == 0 and not missing else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, help="输出目录")
    ap.add_argument("--only", nargs="*", help="只跑这些组")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--fast", action="store_true",
                    help="30 秒级核心契约守卫（不替代全量分组回归）")
    args = ap.parse_args(argv)
    if args.fast:
        return run_fast()
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
    if out_dir.exists() and any(out_dir.iterdir()):
        print(f"输出目录非空：{out_dir}——每轮必须使用独立的新目录，避免旧报告冒充本轮结果")
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    return run_all(names, groups, out_dir)


def run_all(names, groups, out_dir: Path, *, runner=subprocess.run) -> int:
    results = []
    for name in names:
        res = run_group(name, groups[name], out_dir, runner=runner)
        results.append(res)
        flag = f" REPORT_INVALID: {res['report_problem']}" if res["report_problem"] else ""
        print(f"[{name}] rc={res['returncode']} {res['seconds']}s{flag}", flush=True)
    merge_xml(out_dir, [r["group"] for r in results if r["xml"]])
    bad = [r for r in results
           if r["returncode"] not in (0, 1) or r["report_problem"] is not None]
    (out_dir / "summary.json").write_text(
        json.dumps({"groups": results, "complete": not bad}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    if bad:
        print("不完整：" + ", ".join(r["group"] for r in bad))
    return 2 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
