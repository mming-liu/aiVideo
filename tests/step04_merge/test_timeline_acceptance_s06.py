# -*- coding: utf-8 -*-
"""S06：时间轴验收、转场扣时、音频越界、字幕边界、缺轨与可播放样片."""
from __future__ import annotations

import dataclasses
import json
import shutil
from pathlib import Path

import pytest

from step04_merge.timeline import (
    build_timeline_acceptance,
    validate_timeline,
    verify_rendered_output,
)
from shared.media_inspect import probe_media
from tools.build_timeline_sample import (
    build_sample_timeline,
    main as build_sample_main,
    make_wav,
    probe_duration,
)

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(FFMPEG is None or FFPROBE is None,
                                reason="需要本机 ffmpeg/ffprobe")


def test_acceptance_reports_transition_overlap_and_body_hook_split(tmp_path):
    timeline, acceptance = build_sample_timeline(tmp_path / "sample")
    assert acceptance["status"] == "pass"
    duration = acceptance["duration"]
    assert duration["hook_sec"] == 2.0
    assert duration["body_sec"] == 5.0
    assert abs(duration["transition_overlap_sec"] - 0.833334) < 0.05
    assert abs(duration["total_sec"] - (7.0 - duration["transition_overlap_sec"])) < 1e-6
    assert [c["kind"] for c in acceptance["clips"]] == ["hook", "shot", "shot"]
    assert all(c["probe_status"] == "ok" for c in acceptance["clips"])
    assert acceptance["audio_tracks"]
    assert acceptance["subtitles"][0]["cue_id"] == "c1"


def test_audio_overflow_and_short_duration_are_located(tmp_path):
    timeline, _ = build_sample_timeline(tmp_path / "sample")
    long_tts = make_wav(tmp_path / "long.wav", 4.0)
    tracks = tuple(
        dataclasses.replace(t, path=long_tts) if t.kind == "shot_tts" else t
        for t in timeline.audio_tracks)
    changed = validate_timeline(dataclasses.replace(timeline, audio_tracks=tracks))
    acceptance = build_timeline_acceptance(
        changed, expected_body_sec=7.0, expected_hook_sec=2.0)
    codes = set(acceptance["codes"])
    assert "audio_window_overflow" in codes
    assert "timeline_duration_short" in codes
    overflow = next(i for i in acceptance["issues"]
                    if i["code"] == "audio_window_overflow")
    assert overflow["shot_index"] == 1 and overflow["clip_index"] == 1
    short = next(i for i in acceptance["issues"]
                 if i["code"] == "timeline_duration_short")
    assert short["delta_sec"] == -2.0
    assert short["affected_clips"]


def test_subtitle_out_of_window_is_reported(tmp_path):
    timeline, _ = build_sample_timeline(tmp_path / "sample")
    cue = dataclasses.replace(timeline.subtitle_plan.cues[0], end_sec=99.0)
    plan = dataclasses.replace(timeline.subtitle_plan, cues=(cue,))
    changed = dataclasses.replace(timeline, subtitle_plan=plan)
    # validate_timeline 只校验 cue 自身结构；越界由验收报告定位。
    acceptance = build_timeline_acceptance(changed)
    assert "subtitle_out_of_window" in acceptance["codes"]
    issue = next(i for i in acceptance["issues"]
                 if i["code"] == "subtitle_out_of_window")
    assert issue["cue_id"] == "c1"
    assert issue["overflow_sec"] > 0


def test_missing_tracks_are_reported_in_tts_mix(tmp_path):
    timeline, _ = build_sample_timeline(tmp_path / "sample")
    changed = dataclasses.replace(
        timeline, audio_tracks=(),
        clips=tuple(dataclasses.replace(c, has_audio=False) for c in timeline.clips))
    acceptance = build_timeline_acceptance(changed, required_audio=True)
    assert "audio_track_missing" in acceptance["codes"]
    assert acceptance["status"] == "blocked"


def test_rendered_sample_is_playable_and_matches_timeline(tmp_path):
    out = tmp_path / "render"
    code = build_sample_main(["--out", str(out)])
    assert code == 0
    report = json.loads((out / "timeline_acceptance.json").read_text(encoding="utf-8"))
    sample = Path(report["sample_path"])
    assert sample.is_file() and sample.stat().st_size > 1024
    assert report["acceptance"]["status"] == "pass"
    assert report["rendered"]["status"] == "pass"
    assert report["rendered"]["has_audio"] is True
    info = probe_media(str(sample))
    assert info.get("audio") or any(s.get("codec_type") == "audio"
                                    for s in info.get("streams") or [])
    actual = probe_duration(sample)
    assert abs(actual - report["acceptance"]["duration"]["total_sec"]) < 0.2


def test_render_check_detects_duration_mismatch(tmp_path):
    timeline, acceptance = build_sample_timeline(tmp_path / "sample")
    from tools.build_timeline_sample import render_timeline
    sample = render_timeline(timeline, tmp_path / "ok.mp4")
    assert verify_rendered_output(sample, timeline, acceptance)["status"] == "pass"
    # 构造一个与时间轴不符的媒体（时长更短）⇒ 自动检出。
    short = tmp_path / "short.mp4"
    _run_short(short)
    result = verify_rendered_output(short, timeline, acceptance)
    assert result["status"] == "blocked"
    assert "rendered_duration_mismatch" in result["codes"]


def _run_short(path: Path) -> None:
    import subprocess
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "color=c=black:s=320x240:r=24:d=1.0",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
                   check=True)
