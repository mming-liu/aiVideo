# -*- coding: utf-8 -*-
"""M4 Step04：--dry-run 秒级预检（零编码报告）与 CLI 参数."""
from __future__ import annotations

import json
from pathlib import Path

from step04_merge.cli import ENHANCEMENT_FLAGS, build_parser
from step04_merge.dry_run import SCHEMA, build_report, timeline_digest, write_report
from step04_merge.timeline import ClipEntry, MergeTimeline


def _timeline():
    return MergeTimeline(
        scope="chapter:1", script_revision="rev-1", width=720, height=1280, fps=24,
        clips=(ClipEntry(shot_index=1, kind="shot", stable_id="shot:1",
                         path=Path("clip_1.mp4"), duration_sec=4.0,
                         raw_duration_sec=4.0, frames=96, has_audio=True),),
        boundary_transitions=(), audio_mode="native",
    )


def test_dry_run_cli_flag_needs_no_candidate_root():
    args = build_parser().parse_args(["--dry-run"])
    assert args.dry_run is True
    assert "dry_run" not in ENHANCEMENT_FLAGS


def test_report_is_zero_network_zero_encode():
    timeline = _timeline()
    report = build_report(timeline, output=Path("final.mp4"),
                          srt_path=Path("subtitles.srt"), elapsed_ms=12.5)

    assert report["schema"] == SCHEMA
    assert report["network_calls"] == 0
    assert report["ffmpeg_encode_calls"] == 0
    assert report["encoded"] is False
    assert report["timeline_validated"] is True
    assert report["clip_count"] == 1
    assert report["total_duration_sec"] == 4.0
    assert report["windows"] == [{"clip_index": 0, "start_sec": 0.0, "end_sec": 4.0}]
    assert report["subtitle"]["cue_count"] == 0
    assert report["wrote_formal_outputs"] is False
    assert report["elapsed_ms"] == 12.5
    assert report["timeline_digest"] == timeline_digest(timeline)


def test_report_can_be_written_to_candidate_dir(tmp_path):
    report = build_report(_timeline(), output=tmp_path / "candidate.mp4",
                          srt_path=tmp_path / "subtitles.srt")
    target = tmp_path / "attempt" / "dry_run_report.json"
    write_report(report, target)
    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert on_disk["schema"] == SCHEMA
    assert on_disk["wrote_formal_outputs"] is False


def test_report_includes_local_media_precheck():
    report = build_report(
        _timeline(), output=Path("final.mp4"), srt_path=Path("subtitles.srt"),
        media_prechecks=[{"path": "clip_1.mp4", "verdict": "ok",
                          "warnings": [{"code": "duration_deviation"}]}])
    assert report["media_precheck"][0]["path"] == "clip_1.mp4"
    assert report["media_precheck_warnings"] == 1
