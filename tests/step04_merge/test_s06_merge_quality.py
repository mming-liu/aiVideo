# -*- coding: utf-8 -*-
"""S06/M2+M3：切点淡变、`step04_quality` 成片终检（编码/音画/黑场/静帧/字幕/响度）.

全部本地合成媒体 + 真实 FFmpeg，**0 模型调用**。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from step04_merge.backends.ffmpeg import EncodeSettings, compile_graph, render
from step04_merge.quality_gate import evaluate_final
from step04_merge.timeline import (
    ClipEntry,
    MergeTimeline,
    TimelineAudioTrack,
    normalize_duration,
    stable_shot_id,
    validate_timeline,
)

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(FFMPEG is None or FFPROBE is None,
                                reason="需要本机 ffmpeg/ffprobe")


def _clip(path: Path, seconds: float = 2.0, *, color: str = "darkgreen",
          audio: bool = True, source: str = "testsrc2") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if source == "color":
        lavfi = f"color=c={color}:s=320x240:r=24:d={seconds}"
    else:
        lavfi = f"{source}=size=320x240:rate=24:duration={seconds}"
    args = [FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", lavfi]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast"]
    if audio:
        args += ["-c:a", "aac", "-ar", "48000", "-shortest"]
    args += [str(path)]
    subprocess.run(args, check=True)
    return path


def _timeline(tmp_path: Path, *, clips_secs=(2.0, 2.0), audio_mode="native"):
    paths, entries = [], []
    for i, secs in enumerate(clips_secs):
        p = _clip(tmp_path / f"c{i}.mp4", secs)
        frames, normalized = normalize_duration(secs)
        entries.append(ClipEntry(shot_index=i + 1, kind="shot",
                                 stable_id=stable_shot_id("s06", "rev", "shot", i + 1),
                                 path=p, duration_sec=normalized, raw_duration_sec=secs,
                                 frames=frames, has_audio=True))
        paths.append(p)
    timeline = MergeTimeline(scope="s06", script_revision="rev", width=320, height=240,
                             fps=24, clips=tuple(entries),
                             boundary_transitions=(), endpoint_effects=(),
                             audio_mode=audio_mode, audio_tracks=(),
                             source={"kind": "s06_fixture"})
    return validate_timeline(timeline)


def _render(timeline, out: Path, *, cut_fade_ms: int = 20) -> Path:
    render(timeline, out, out.parent / "work", settings=EncodeSettings(
        width=320, height=240, preset="ultrafast", crf="28",
        cut_fade_ms=cut_fade_ms, audio_encoder_args=(
            "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2")))
    return out


# ---------------------------------------------------------------- M2 切点淡变

def test_cut_fade_applied_to_native_audio_only(tmp_path):
    from tools.build_timeline_sample import make_wav
    tts = make_wav(tmp_path / "tts.wav", 1.5, freq=520)
    timeline = _timeline(tmp_path)
    timeline = validate_timeline(MergeTimeline(**{
        **timeline.__dict__, "audio_mode": "tts_mix",
        "audio_tracks": (TimelineAudioTrack(kind="shot_tts", stable_id="shot-1:tts",
                                            path=tts, clip_index=0),)}))
    _i, graph, _v, _a, _m = compile_graph(timeline, EncodeSettings(
        width=320, height=240, preset="ultrafast", cut_fade_ms=20))
    assert "afade=t=in:st=0:d=0.020" in graph
    assert "afade=t=out:st=" in graph
    # TTS 链（[tts0] 前缀）不得淡出人声尾音
    tts_line = next(line for line in graph.split(";") if "[tts0]" in line or "[1:a]" in line
                    and "tts" in line)
    assert "afade" not in tts_line


# ---------------------------------------------------------------- M3 正常样本 PASS

def test_good_final_passes_all_media_checks(tmp_path):
    timeline = _timeline(tmp_path)
    out = _render(timeline, tmp_path / "final.mp4")
    report = evaluate_final(
        out, expected_duration=timeline.total_duration_sec,
        width=320, height=240, fps=24, audio_required=True, audio_sample_rate=48000)
    assert report["status"] == "PASS", report["defects"]
    names = {c["name"] for c in report["checks"]}
    assert {"black_detect", "freeze_detect", "silence_detect", "loudness",
            "moov_faststart"} <= names
    assert report["metrics"]["av_drift_ms"] <= 100
    assert report["metrics"]["moov_before_mdat"] is True
    assert abs(report["metrics"]["loudness"]["integrated_lufs"]
               - (-16.0)) <= 1.5


# ---------------------------------------------------------------- M3 缺陷拦截

def _mux(video: Path, audio: Path | None, out: Path) -> Path:
    args = [FFMPEG, "-y", "-v", "error", "-i", str(video)]
    if audio is not None:
        args += ["-i", str(audio)]
    args += ["-c:v", "copy"]
    if audio is not None:
        args += ["-c:a", "aac", "-ar", "48000", "-map", "0:v:0", "-map", "1:a:0"]
    else:
        args += ["-an"]
    args += ["-movflags", "+faststart", str(out)]
    subprocess.run(args, check=True)
    return out


def test_av_drift_blocks(tmp_path):
    video = _clip(tmp_path / "v.mp4", 2.0, audio=False)
    audio = tmp_path / "a.m4a"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=2.6", "-c:a", "aac", "-ar", "48000",
                    str(audio)], check=True)
    final = _mux(video, audio, tmp_path / "drift.mp4")
    report = evaluate_final(final, audio_required=True, check_loudness=False)
    assert report["status"] == "BLOCK"
    assert "av_duration_drift" in {d["code"] for d in report["defects"]}


def test_missing_audio_blocks(tmp_path):
    video = _clip(tmp_path / "v.mp4", 2.0, audio=False)
    final = _mux(video, None, tmp_path / "silent.mp4")
    report = evaluate_final(final, audio_required=True, check_loudness=False)
    assert "audio_stream_missing" in {d["code"] for d in report["defects"]}


def test_unexpected_black_blocks_unless_declared(tmp_path):
    # 2s 绿 → 1s 黑 → 2s 绿，用 concat 拼
    parts = []
    for name, color, secs in (("a", "darkgreen", 2.0), ("b", "black", 1.0),
                              ("c", "darkgreen", 2.0)):
        p = _clip(tmp_path / f"{name}.mp4", secs, color=color, audio=False,
                  source="color")
        parts.append(p)
    listing = tmp_path / "list.txt"
    listing.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts),
                       encoding="utf-8")
    silent = tmp_path / "silent.mp4"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0",
                    "-i", str(listing), "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-preset", "ultrafast", str(silent)], check=True)
    audio = tmp_path / "a.m4a"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=5.0", "-c:a", "aac", "-ar", "48000",
                    str(audio)], check=True)
    final = _mux(silent, audio, tmp_path / "black_mid.mp4")
    report = evaluate_final(final, audio_required=True, check_loudness=False)
    assert "unexpected_black" in {d["code"] for d in report["defects"]}

    declared = evaluate_final(final, audio_required=True, check_loudness=False,
                              declared_black_windows=[[2.0, 3.1]])
    assert "unexpected_black" not in {d["code"] for d in declared["defects"]}


def test_frozen_final_blocks(tmp_path):
    video = _clip(tmp_path / "v.mp4", 3.0, color="navy", audio=False,
                  source="color")
    audio = tmp_path / "a.m4a"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=3.0", "-c:a", "aac", "-ar", "48000",
                    str(audio)], check=True)
    final = _mux(video, audio, tmp_path / "frozen.mp4")
    report = evaluate_final(final, audio_required=True, check_loudness=False)
    assert "final_frozen" in {d["code"] for d in report["defects"]}


def test_subtitle_beyond_duration_blocks(tmp_path):
    timeline = _timeline(tmp_path)
    final = _render(timeline, tmp_path / "final.mp4")
    srt = tmp_path / "subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:99,000\n超界字幕\n", encoding="utf-8")
    report = evaluate_final(final, expected_duration=timeline.total_duration_sec,
                            width=320, height=240, fps=24, srt_path=srt,
                            check_loudness=False)
    assert "subtitle_beyond_duration" in {d["code"] for d in report["defects"]}


def test_quiet_final_flags_loudness(tmp_path):
    video = _clip(tmp_path / "v.mp4", 2.0, audio=False)
    audio = tmp_path / "quiet.m4a"
    # 明显偏小但仍可测（约 -30 LUFS）：低于"整轨静音"判据，应命中响度缺陷
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=2.0", "-af", "volume=0.05",
                    "-c:a", "aac", "-ar", "48000", str(audio)], check=True)
    final = _mux(video, audio, tmp_path / "quiet.mp4")
    report = evaluate_final(final, audio_required=True)
    assert "loudness_out_of_range" in {d["code"] for d in report["defects"]}


def test_silent_track_is_not_a_loudness_defect(tmp_path):
    """整轨静音（样片/design 内静音）不按响度不达标判废，只记 warn 与静音段."""
    video = _clip(tmp_path / "v.mp4", 4.0, audio=False)
    audio = tmp_path / "silent.m4a"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "anullsrc=r=48000:cl=mono", "-t", "4", "-c:a", "aac",
                    str(audio)], check=True)
    final = _mux(video, audio, tmp_path / "totally_silent.mp4")
    report = evaluate_final(final, audio_required=True)
    codes = {d["code"] for d in report["defects"]}
    assert "loudness_out_of_range" not in codes
    loud = next(c for c in report["checks"] if c["name"] == "loudness")
    assert loud["status"] == "warn" and loud.get("reason") == "silent_track"
    silence = next(c for c in report["checks"] if c["name"] == "silence_detect")
    assert silence["status"] == "warn" and silence.get("segments")
