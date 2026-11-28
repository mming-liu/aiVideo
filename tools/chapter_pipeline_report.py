# -*- coding: utf-8 -*-
"""跨阶段统一计量与成本汇总台账（2026-11-28 O5）.

单入口、只读、容错：把 Step01–Step04 分散的产物/账本收敛成一份报表，回答
「这一章花了多少次真实请求、重试放大多少、卡在哪一步」。

用法::

    python tools/chapter_pipeline_report.py --chapter-dir output/<book>/<part>/chapter_001
    python tools/chapter_pipeline_report.py --book-dir output/<book>/<part> --markdown
    python tools/chapter_pipeline_report.py --chapter-dir ... --json

输出 schema `pipeline.chapter_report/1`（多章为 `pipeline.book_report/1`）。

设计约束：
- **只读**：只 stat/读取，不写任何文件、不触发模型调用；
- **容错**：缺阶段标 `not_run`、缺文件标 `partial`、坏 JSONL 行进 `parse_warnings`，
  其余有效行照常统计（不因一行坏了整份报表作废）；
- **口径复用**：Step02a/02b 直接复用既有 `summarize_step02a_events` /
  `summarize_step02b_events`，不重写一套指标。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA = "pipeline.chapter_report/1"
BOOK_SCHEMA = "pipeline.book_report/1"
STAGES = ("step01", "step02a", "step02b", "step03", "step04")

#: 「真实付费请求」的事件类型（本地缓存命中/决策台账不计）。
#: 新旧账本并存：早期只写 `request_sent`，2026-10 之后写 `http_attempt`；
#: 视觉 QC 写 `qc_http_attempt`。发送前失败（`pre_send_failed`）不算发出。
_PAID_EVENTS = frozenset({"request_sent", "http_attempt", "video_submit",
                          "qc_http_attempt"})
_FAILURE_MARKERS = ("failed", "error", "timeout", "blocked", "rejected", "exhausted")


# ---------------------------------------------------------------------------
# 读取原语（全部容错）
# ---------------------------------------------------------------------------
def _load_json(path: Path, warnings: list[str]) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append("%s 不可读: %s" % (path.name, exc))
        return None
    return data if isinstance(data, dict) else None


def _read_jsonl(path: Path, warnings: list[str]) -> list[dict]:
    """逐行容错解析：坏行计入 warnings 并跳过，其余行保留."""
    if not path.is_file():
        return []
    rows: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        warnings.append("%s 不可读: %s" % (path.name, exc))
        return []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            warnings.append("%s:%d JSONL 行损坏，已跳过" % (path.name, lineno))
            continue
        if isinstance(row, dict):
            rows.append(row)
        else:
            warnings.append("%s:%d 不是 JSON 对象，已跳过" % (path.name, lineno))
    return rows


def _stage_status(present: bool, optional_missing: bool = False) -> str:
    if not present:
        return "not_run"
    return "partial" if optional_missing else "complete"


def _count_files(path: Path, pattern: str) -> int:
    if not path.is_dir():
        return 0
    return sum(1 for p in path.glob(pattern) if p.is_file())


# ---------------------------------------------------------------------------
# 各阶段采集
# ---------------------------------------------------------------------------
def _step01(ch: Path, warnings: list[str]) -> dict:
    script = _load_json(ch / "script_result.json", warnings)
    quality = _load_json(ch / "step01_quality.json", warnings)
    commit = _load_json(ch / "step01_commit.json", warnings)
    timing = _load_json(ch / "step01_timing.json", warnings)
    metrics = _load_json(ch / "step01_metrics.json", warnings)
    repair_rounds = _load_json(ch / "step01_repair_rounds.json", warnings)
    calls = _read_jsonl(ch / "step01_calls.jsonl", warnings)

    if script is None and quality is None:
        return {"status": "not_run"}

    shots = (script or {}).get("shots") or []
    repair_round_count = 0
    if isinstance(repair_rounds, dict):
        for key in ("rounds", "entries", "history"):
            value = repair_rounds.get(key)
            if isinstance(value, list):
                repair_round_count = len(value)
                break
    elif isinstance(repair_rounds, list):
        repair_round_count = len(repair_rounds)

    return {
        "status": _stage_status(script is not None, optional_missing=quality is None),
        "script_revision": (script or {}).get("script_revision")
                               or (quality or {}).get("script_revision"),
        "rule_source_fingerprint": (quality or {}).get("rule_source_fingerprint")
                                        or (commit or {}).get("rule_source_fingerprint"),
        "shots_count": len(shots),
        "hook_present": isinstance((script or {}).get("hook"), dict),
        "total_duration_sec": (script or {}).get("total_duration"),
        "duration_band": (quality or {}).get("duration_band"),
        "quality": {
            "status": (quality or {}).get("status"),
            "blocking": bool((quality or {}).get("blocking")),
            "hard_count": (quality or {}).get("hard_count"),
            "visual_risk_count": (quality or {}).get("visual_risk_count"),
            "advisory_count": (quality or {}).get("advisory_count"),
            "blocking_categories": list((quality or {}).get("blocking_categories") or []),
        },
        "commit_status": (commit or {}).get("status"),
        "run_id": (commit or {}).get("run_id"),
        "llm_calls": len(calls),
        "repair_rounds": repair_round_count,
        "timing": timing or metrics or None,
    }


def _step02a(ch: Path, warnings: list[str]) -> dict:
    events = _read_jsonl(ch / "step02_events.jsonl", warnings)
    quality = _load_json(ch / "step02a_quality.json", warnings)
    if not events and quality is None:
        return {"status": "not_run"}
    summary = None
    if events:
        try:
            from tools.step02a_metrics_report import summarize_step02a_events

            summary = summarize_step02a_events(events)
        except Exception as exc:                       # noqa: BLE001 - 容错：指标不可算不影响台账
            warnings.append("step02a 指标汇总失败: %s" % exc)
    return {
        "status": _stage_status(bool(events) or quality is not None,
                                optional_missing=summary is None),
        "frames_on_disk": _count_files(ch / "images", "*.png"),
        "quality": quality,
        "summary": summary,
        "events": len(events),
    }


def _step02b(ch: Path, warnings: list[str]) -> dict:
    events = _read_jsonl(ch / "step02b_events.jsonl", warnings)
    if not events:
        # 旧版把视频事件也写进 step02_events.jsonl
        events = [e for e in _read_jsonl(ch / "step02_events.jsonl", warnings)
                  if str(e.get("kind") or "").startswith("video")]
    quality = _load_json(ch / "step02b_quality.json", warnings)
    if not events and quality is None:
        return {"status": "not_run"}
    summary = None
    if events:
        try:
            from tools.step02b_metrics_report import summarize_step02b_events

            summary = summarize_step02b_events(events)
        except Exception as exc:                       # noqa: BLE001
            warnings.append("step02b 指标汇总失败: %s" % exc)
    return {
        "status": _stage_status(bool(events) or quality is not None,
                                optional_missing=summary is None),
        "clips_on_disk": _count_files(ch / "videos", "*.mp4"),
        "quality": quality,
        "summary": summary,
        "events": len(events),
    }


def _step03(ch: Path, warnings: list[str]) -> dict:
    quality = _load_json(ch / "step03_quality.json", warnings)
    duration_audit = _load_json(ch / "step03_duration_audit.json", warnings)
    audio_files = _count_files(ch / "audio", "*.*")
    if quality is None and duration_audit is None and not audio_files:
        return {"status": "not_run"}
    units = None
    if isinstance(duration_audit, dict):
        units = duration_audit.get("units") or duration_audit.get("entries")
    return {
        "status": _stage_status(quality is not None,
                                optional_missing=duration_audit is None),
        "audio_files": audio_files,
        "units": len(units) if isinstance(units, list) else None,
        "quality": quality,
        "duration_audit": duration_audit,
    }


def _step04(ch: Path, warnings: list[str]) -> dict:
    quality = _load_json(ch / "step04_quality.json", warnings)
    final_dir = ch / "final"
    finals = sorted(p for p in final_dir.glob("*.mp4") if p.is_file()) if final_dir.is_dir() else []
    if quality is None and not finals:
        return {"status": "not_run"}
    media = (quality or {}).get("media") if isinstance(quality, dict) else None
    return {
        "status": _stage_status(bool(finals) or quality is not None,
                                optional_missing=quality is None),
        "final_files": [{"name": p.name, "bytes": p.stat().st_size} for p in finals],
        "media": media,
        "quality": quality,
    }


# ---------------------------------------------------------------------------
# 跨阶段总账
# ---------------------------------------------------------------------------
def _event_universe(ch: Path, warnings: list[str]) -> list[dict]:
    rows: list[dict] = []
    for name in ("step02_events.jsonl", "step02b_events.jsonl"):
        rows.extend(_read_jsonl(ch / name, warnings))
    return rows


def _paid_requests(events: Iterable[dict]) -> dict[str, int]:
    out: Counter = Counter()
    for ev in events:
        if str(ev.get("event") or "") not in _PAID_EVENTS:
            continue
        if ev.get("pre_send_failed"):
            continue                       # 发送前被守卫拦下：没有真实请求
        provider = str(ev.get("provider") or "").strip()
        model = str(ev.get("model_id") or ev.get("model") or "").strip()
        if not provider and not model:
            # 早期账本没有通道字段：按事件类型/kind 归类，明确标 unknown（不冒充已知通道）
            label = str(ev.get("kind") or ev.get("event") or "").strip() or "unknown"
            out["unknown/%s" % label] += 1
            continue
        key = "%s/%s" % (provider or "unknown", model or "unknown")
        out[key] += 1
    return dict(sorted(out.items()))


def _failure_classes(events: Iterable[dict]) -> dict[str, int]:
    out: Counter = Counter()
    for ev in events:
        event = str(ev.get("event") or "")
        status = str(ev.get("status") or ev.get("error_class") or "").strip().lower()
        if any(marker in event for marker in _FAILURE_MARKERS) or (
                status and any(marker in status for marker in _FAILURE_MARKERS)):
            out[status or event or "unknown"] += 1
    return dict(out.most_common(5))


def _logical_requests(events: Iterable[dict]) -> int:
    """逻辑请求数：按 `request_id` 去重（没有 request_id 的按行计）."""
    seen: set[str] = set()
    total = 0
    for ev in events:
        if str(ev.get("event") or "") not in _PAID_EVENTS:
            continue
        if ev.get("pre_send_failed"):
            continue
        rid = str(ev.get("request_id") or "").strip()
        if rid:
            if rid in seen:
                continue
            seen.add(rid)
        total += 1
    return total


def build_chapter_report(chapter_dir: Path) -> dict[str, Any]:
    """单章报表（只读；任何阶段缺失都降级，不抛）."""
    ch = Path(chapter_dir)
    warnings: list[str] = []
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "chapter_dir": str(ch),
        "chapter": ch.name,
        "stages": {},
    }
    report["stages"]["step01"] = _step01(ch, warnings)
    report["stages"]["step02a"] = _step02a(ch, warnings)
    report["stages"]["step02b"] = _step02b(ch, warnings)
    report["stages"]["step03"] = _step03(ch, warnings)
    report["stages"]["step04"] = _step04(ch, warnings)

    events = _event_universe(ch, warnings)
    paid = _paid_requests(events)
    logical = _logical_requests(events)
    attempts = sum(paid.values())
    report["totals"] = {
        "paid_requests_total": attempts,
        "logical_requests": logical,
        "total_paid_requests_by_provider_model": paid,
        "retry_amplification_ratio": (round(attempts / logical, 3) if logical else None),
        "top_failure_classes": _failure_classes(events),
    }
    report["parse_warnings"] = warnings
    report["stage_status"] = {stage: report["stages"][stage].get("status", "not_run")
                              for stage in STAGES}
    return report


def build_book_report(book_dir: Path) -> dict[str, Any]:
    """多章汇总：逐章报表 + 加和（加和来自各章 totals，不用汇总事件重复计数）."""
    book = Path(book_dir)
    chapters = sorted(p for p in book.glob("chapter_*") if p.is_dir())
    reports = [build_chapter_report(p) for p in chapters]
    paid: Counter = Counter()
    for rep in reports:
        for key, count in rep["totals"]["total_paid_requests_by_provider_model"].items():
            paid[key] += int(count)
    attempts = sum(rep["totals"]["paid_requests_total"] for rep in reports)
    logical = sum(rep["totals"]["logical_requests"] for rep in reports)
    failures: Counter = Counter()
    for rep in reports:
        for key, count in rep["totals"]["top_failure_classes"].items():
            failures[key] += int(count)
    return {
        "schema": BOOK_SCHEMA,
        "book_dir": str(book),
        "chapters": [{"chapter": rep["chapter"], "report": rep} for rep in reports],
        "totals": {
            "chapters": len(reports),
            "paid_requests_total": attempts,
            "logical_requests": logical,
            "total_paid_requests_by_provider_model": dict(sorted(paid.items())),
            "retry_amplification_ratio": (round(attempts / logical, 3) if logical else None),
            "top_failure_classes": dict(failures.most_common(5)),
        },
        "parse_warnings": [w for rep in reports for w in rep["parse_warnings"]],
    }


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------
def _md_chapter(rep: dict) -> str:
    st = rep["stages"]
    s1, s2a, s2b, s3, s4 = (st["step01"], st["step02a"], st["step02b"],
                            st["step03"], st["step04"])
    q = s1.get("quality") or {}
    lines = [
        "## %s" % rep["chapter"],
        "",
        "| 阶段 | 状态 | 关键读数 |",
        "| --- | --- | --- |",
        "| step01 | %s | %s 镜 / %s s / quality=%s / blocking=%s |" % (
            s1.get("status"), s1.get("shots_count"), s1.get("total_duration_sec"),
            q.get("status"), q.get("blocking")),
        "| step02a | %s | events=%s / frames=%s |" % (
            s2a.get("status"), s2a.get("events"), s2a.get("frames_on_disk")),
        "| step02b | %s | events=%s / clips=%s |" % (
            s2b.get("status"), s2b.get("events"), s2b.get("clips_on_disk")),
        "| step03 | %s | units=%s / audio=%s |" % (
            s3.get("status"), s3.get("units"), s3.get("audio_files")),
        "| step04 | %s | finals=%s |" % (
            s4.get("status"), len(s4.get("final_files") or [])),
        "",
        "总请求 **%s**（逻辑 %s，重试放大 %s）" % (
            rep["totals"]["paid_requests_total"], rep["totals"]["logical_requests"],
            rep["totals"]["retry_amplification_ratio"]),
    ]
    if rep["totals"]["total_paid_requests_by_provider_model"]:
        lines.append("")
        lines.append("| provider/model | 请求数 |")
        lines.append("| --- | --- |")
        for key, count in rep["totals"]["total_paid_requests_by_provider_model"].items():
            lines.append("| %s | %s |" % (key, count))
    if rep["totals"]["top_failure_classes"]:
        lines.append("")
        lines.append("失败分布：" + ", ".join(
            "%s×%s" % (k, v) for k, v in rep["totals"]["top_failure_classes"].items()))
    if rep["parse_warnings"]:
        lines.append("")
        lines.append("解析告警 %d 条（%s）" % (
            len(rep["parse_warnings"]), rep["parse_warnings"][0]))
    return "\n".join(lines)


def render_markdown(report: dict) -> str:
    if report.get("schema") == BOOK_SCHEMA:
        head = ["# 章节全链路台账", "",
                "共 **%d** 章，真实请求 **%s** 次（逻辑 %s，重试放大 %s）" % (
                    report["totals"]["chapters"], report["totals"]["paid_requests_total"],
                    report["totals"]["logical_requests"],
                    report["totals"]["retry_amplification_ratio"]), ""]
        body = [_md_chapter(item["report"]) for item in report["chapters"]]
        return "\n\n".join(head + body) + "\n"
    return "# 章节全链路台账\n\n" + _md_chapter(report) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="跨阶段统一计量与成本汇总（只读、零调用）")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--chapter-dir", type=Path)
    src.add_argument("--book-dir", type=Path)
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--markdown", action="store_true", help="输出 Markdown 表")
    args = ap.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if args.chapter_dir is not None:
        if not args.chapter_dir.is_dir():
            print("[ERROR] 章节目录不存在: %s" % args.chapter_dir)
            return 2
        report = build_chapter_report(args.chapter_dir)
    else:
        if not args.book_dir.is_dir():
            print("[ERROR] 部目录不存在: %s" % args.book_dir)
            return 2
        report = build_book_report(args.book_dir)

    if args.markdown or not args.json:
        print(render_markdown(report))
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
