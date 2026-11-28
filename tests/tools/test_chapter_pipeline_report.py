# -*- coding: utf-8 -*-
"""跨阶段统一台账 `tools/chapter_pipeline_report.py`（2026-11-28 O5）.

夹具全部在 `tmp_path` 现搭，不依赖真实 `output/`：完整五阶段、只跑一半、
损坏 JSONL 行、多章加和守恒、Markdown/JSON 渲染。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import chapter_pipeline_report as cpr


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_events(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")


def _chapter(root: Path, chapter: int = 1, *, full: bool = True) -> Path:
    ch = root / "book" / "part_01" / ("chapter_%03d" % chapter)
    ch.mkdir(parents=True)
    _write_json(ch / "script_result.json", {
        "shots": [{"index": 1}, {"index": 2}], "hook": {"index": 0, "prompt": "x"},
        "total_duration": 60.0, "script_revision": "rev-%d" % chapter})
    _write_json(ch / "step01_quality.json", {
        "status": "PASS", "blocking": False, "rule_source_fingerprint": "fp-1",
        "hard_count": 0, "visual_risk_count": 0, "advisory_count": 1,
        "blocking_categories": [], "script_revision": "rev-%d" % chapter,
        "duration_band": {"min": 60, "max": 120, "target_min": 85, "target_max": 110}})
    _write_json(ch / "step01_commit.json", {"status": "COMMITTED", "run_id": "run-1"})
    _write_json(ch / "step01_timing.json", {"wall_sec": 42.0})
    _write_events(ch / "step02_events.jsonl", [
        {"event": "http_attempt", "kind": "first_frame", "request_id": "a%d" % chapter,
         "operation_id": "op-%d" % chapter, "provider": "volcengine", "model_id": "seedream"},
    ])
    _write_events(ch / "step02b_events.jsonl", [
        {"event": "video_submit", "kind": "video_submit", "request_id": "v%d" % chapter,
         "provider": "volcengine", "model_id": "seedance"},
    ])
    if not full:
        return ch
    _write_json(ch / "step03_quality.json", {"status": "PASS", "report_type": "product_acceptance"})
    _write_json(ch / "step03_duration_audit.json", {"units": [{"unit_id": "u1"}, {"unit_id": "u2"}]})
    (ch / "audio").mkdir()
    (ch / "audio" / "shot_1.mp3").write_bytes(b"a" * 16)
    _write_json(ch / "step04_quality.json", {"status": "PASS", "media": {"fps": 24}})
    (ch / "final").mkdir()
    (ch / "final" / "final.mp4").write_bytes(b"v" * 32)
    return ch


def test_full_chapter_report_collects_all_stages(tmp_path):
    ch = _chapter(tmp_path)
    report = cpr.build_chapter_report(ch)
    assert report["schema"] == cpr.SCHEMA
    assert report["stage_status"] == {
        "step01": "complete", "step02a": "complete", "step02b": "complete",
        "step03": "complete", "step04": "complete"}
    s1 = report["stages"]["step01"]
    assert s1["shots_count"] == 2 and s1["total_duration_sec"] == 60.0
    assert s1["quality"]["status"] == "PASS" and s1["quality"]["blocking"] is False
    assert s1["timing"] == {"wall_sec": 42.0}
    assert report["stages"]["step03"]["audio_files"] == 1
    assert report["stages"]["step03"]["units"] == 2
    assert len(report["stages"]["step04"]["final_files"]) == 1
    assert report["totals"]["paid_requests_total"] == 2
    assert report["totals"]["logical_requests"] == 2
    assert report["totals"]["retry_amplification_ratio"] == 1.0
    assert report["totals"]["total_paid_requests_by_provider_model"] == {
        "volcengine/seedance": 1, "volcengine/seedream": 1}


def test_partial_chapter_degrades_without_crash(tmp_path):
    ch = _chapter(tmp_path, full=False)
    report = cpr.build_chapter_report(ch)
    assert report["stage_status"]["step01"] == "complete"
    assert report["stage_status"]["step03"] == "not_run"
    assert report["stage_status"]["step04"] == "not_run"
    assert report["totals"]["paid_requests_total"] == 2
    # 02a/02b 只有事件账本（没有 quality 报告）也能出指标
    assert report["stage_status"]["step02a"] in ("complete", "partial")
    assert report["stage_status"]["step02b"] in ("complete", "partial")


def test_corrupt_jsonl_lines_are_reported_not_fatal(tmp_path):
    ch = _chapter(tmp_path)
    with (ch / "step02_events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write('{"event": "http_attempt", "provider": "volcengine", '
                 '"model_id": "seedream", "request_id": "b1"}\n')
        fh.write("{not-json}\n")
        fh.write('["not", "an", "object"]\n')
    report = cpr.build_chapter_report(ch)
    assert report["totals"]["paid_requests_total"] == 3
    assert any("JSONL 行损坏" in w for w in report["parse_warnings"])
    assert any("不是 JSON 对象" in w for w in report["parse_warnings"])


def test_missing_reports_are_not_run_and_totals_are_zero(tmp_path):
    ch = tmp_path / "empty" / "chapter_001"
    ch.mkdir(parents=True)
    report = cpr.build_chapter_report(ch)
    assert set(report["stage_status"].values()) == {"not_run"}
    assert report["totals"]["paid_requests_total"] == 0
    assert report["totals"]["retry_amplification_ratio"] is None


def test_book_report_sums_conservatively(tmp_path):
    _chapter(tmp_path, chapter=1)
    ch2 = _chapter(tmp_path, chapter=2)
    _write_events(ch2 / "step02_events.jsonl", [
        {"event": "http_attempt", "request_id": "a2", "provider": "volcengine",
         "model_id": "seedream"},
        {"event": "http_attempt", "request_id": "a2-retry", "provider": "volcengine",
         "model_id": "seedream"},
    ])
    book = cpr.build_book_report(tmp_path / "book" / "part_01")
    assert book["schema"] == cpr.BOOK_SCHEMA
    assert book["totals"]["chapters"] == 2
    per_chapter = [item["report"]["totals"]["paid_requests_total"]
                   for item in book["chapters"]]
    assert book["totals"]["paid_requests_total"] == sum(per_chapter) == 5
    assert book["totals"]["total_paid_requests_by_provider_model"] == {
        "volcengine/seedance": 2, "volcengine/seedream": 3}
    # 加和守恒：逻辑请求也逐章相加
    assert book["totals"]["logical_requests"] == sum(
        item["report"]["totals"]["logical_requests"] for item in book["chapters"])


def test_markdown_renders_key_rows(tmp_path):
    ch = _chapter(tmp_path)
    text = cpr.render_markdown(cpr.build_chapter_report(ch))
    assert "章节全链路台账" in text
    assert "| step01 |" in text and "| step04 |" in text
    assert "总请求 **2**" in text


def test_cli_json_and_markdown_paths(tmp_path, capsys):
    ch = _chapter(tmp_path)
    assert cpr.main(["--chapter-dir", str(ch), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["totals"]["paid_requests_total"] == 2

    assert cpr.main(["--book-dir", str(tmp_path / "book" / "part_01"), "--markdown"]) == 0
    text = capsys.readouterr().out
    assert "章节全链路台账" in text and "chapter_001" in text

    assert cpr.main(["--chapter-dir", str(tmp_path / "nope")]) == 2
