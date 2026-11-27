# -*- coding: utf-8 -*-
"""S05/M1：配音超窗的显式策略（fail / pad_video_tail / gentle_atempo / trim）.

纯算术判定 + 时间线/后端落实 + 真实 FFmpeg 渲染一次（0 模型调用）。
"""
from __future__ import annotations

import dataclasses
import os
import shutil
from pathlib import Path

import pytest

from step04_merge.audio_fit import (
    CODE_EXCEEDS_ATEMPO,
    CODE_EXCEEDS_PAD,
    CODE_TRIM_DISCLOSED,
    CODE_TRUNCATED,
    ENV_LEGACY_ALLOW_TRIM,
    ENV_POLICY,
    POLICY_ATEMPO,
    POLICY_FAIL,
    POLICY_PAD,
    POLICY_TRIM,
    plan_audio_fit,
    resolve_policy,
)
from step04_merge.backends.ffmpeg import EncodeSettings, compile_graph, render
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


# ---------------------------------------------------------------- 策略解析

def test_policy_resolution_defaults_to_fail_and_keeps_legacy_trim(monkeypatch):
    monkeypatch.delenv(ENV_POLICY, raising=False)
    monkeypatch.delenv(ENV_LEGACY_ALLOW_TRIM, raising=False)
    assert resolve_policy("") == POLICY_FAIL
    assert resolve_policy("", legacy_allow_trim=True) == POLICY_TRIM
    assert resolve_policy("pad_video_tail") == POLICY_PAD
    with pytest.raises(ValueError, match="非法"):
        resolve_policy("truncate")


# ---------------------------------------------------------------- 纯算术判定

def test_fail_policy_blocks_overflow_without_touching_audio():
    plan = plan_audio_fit([6.2], [5.0], shot_indexes=[3], policy=POLICY_FAIL)
    assert not plan.ok
    blocked = plan.blocked[0]
    assert blocked["code"] == CODE_TRUNCATED
    assert blocked["shot_index"] == 3
    assert abs(blocked["overflow_sec"] - 1.2) < 1e-6
    # 默认策略**不改**任何时长/音轨（拒绝静默截字）
    assert plan.clip_durations == [5.0]
    assert plan.pads == {} and plan.atempo == {}
    assert "至少" not in blocked["message"]


def test_tolerance_absorbs_frame_quantization_noise():
    plan = plan_audio_fit([5.1], [5.0], policy=POLICY_FAIL, tolerance_sec=0.15)
    assert plan.ok and plan.pads == {} and plan.atempo == {}
    assert [a.action for a in plan.adjustments] == ["tolerated"]
    assert plan.adjustments[0].code == CODE_TRIM_DISCLOSED


def test_pad_policy_extends_within_limit_and_blocks_beyond():
    ok = plan_audio_fit([5.8], [5.0], policy=POLICY_PAD, max_pad_sec=1.0)
    assert ok.ok
    assert abs(ok.pads[0] - 0.8) < 1e-6
    assert abs(ok.clip_durations[0] - 5.8) < 1e-6
    assert ok.adjustments[0].action == "pad"

    too_much = plan_audio_fit([6.2], [5.0], policy=POLICY_PAD, max_pad_sec=1.0)
    assert not too_much.ok
    assert too_much.blocked[0]["code"] == CODE_EXCEEDS_PAD


def test_atempo_policy_caps_at_1_12x_and_blocks_beyond():
    ok = plan_audio_fit([5.5], [5.0], policy=POLICY_ATEMPO, max_atempo=1.12)
    assert ok.ok
    assert abs(ok.atempo[0] - 1.1) < 1e-6
    assert ok.adjustments[0].action == "atempo"

    too_fast = plan_audio_fit([6.0], [5.0], policy=POLICY_ATEMPO, max_atempo=1.12)
    assert not too_fast.ok
    assert too_fast.blocked[0]["code"] == CODE_EXCEEDS_ATEMPO


def test_trim_policy_discloses_what_is_cut():
    plan = plan_audio_fit([6.2], [5.0], policy=POLICY_TRIM)
    assert plan.ok
    assert plan.adjustments[0].action == "trim"
    assert abs(plan.adjustments[0].applied_sec - 1.2) < 1e-6
    assert plan.adjustments[0].code == CODE_TRIM_DISCLOSED


def test_no_dialogue_track_is_untouched():
    plan = plan_audio_fit([None, 0.0], [4.0, 4.0], policy=POLICY_FAIL)
    assert plan.ok and plan.adjustments == []


# ---------------------------------------------------------------- 时间线/后端落实

def _one_clip_timeline(tmp_path: Path, *, clip_sec: float, tts_sec: float,
                       pad_sec: float = 0.0, atempo: float = 1.0,
                       validate: bool = True):
    from tools.build_timeline_sample import make_clip, make_wav
    clip = make_clip(tmp_path / "clip.mp4", clip_sec, "darkgreen")
    tts = make_wav(tmp_path / "tts.wav", tts_sec, freq=520)
    frames, normalized = normalize_duration(clip_sec)
    entry = ClipEntry(shot_index=1, kind="shot", stable_id=stable_shot_id(
        "s05", "rev", "shot", 1), path=clip, duration_sec=normalized,
        raw_duration_sec=clip_sec, frames=frames, has_audio=True, pad_sec=pad_sec)
    timeline = MergeTimeline(
        scope="s05", script_revision="rev", width=320, height=240, fps=24,
        clips=(entry,), boundary_transitions=(), endpoint_effects=(),
        audio_mode="tts_mix",
        audio_tracks=(TimelineAudioTrack(kind="shot_tts", stable_id="shot-1:tts",
                                         path=tts, clip_index=0, atempo=atempo),),
        source={"kind": "s05_fixture"})
    return (validate_timeline(timeline) if validate else timeline), timeline


def test_compile_graph_applies_pad_and_atempo(tmp_path):
    timeline, _ = _one_clip_timeline(tmp_path, clip_sec=5.0, tts_sec=5.8,
                                     pad_sec=0.8, atempo=1.0)
    _inputs, graph, _v, _a, _meta = compile_graph(timeline, EncodeSettings(
        width=320, height=240, preset="ultrafast"))
    assert "tpad=stop_mode=clone:stop_duration=0.800000" in graph
    assert "atempo=" not in graph

    timeline2, _ = _one_clip_timeline(tmp_path, clip_sec=5.0, tts_sec=5.6,
                                      pad_sec=0.0, atempo=1.12)
    _i2, graph2, _v2, _a2, _m2 = compile_graph(timeline2, EncodeSettings(
        width=320, height=240, preset="ultrafast"))
    assert "atempo=1.120000" in graph2
    assert "tpad=" not in graph2


def test_validate_rejects_atempo_above_limit(tmp_path):
    from step04_merge.timeline import TimelineError
    _timeline, raw = _one_clip_timeline(tmp_path, clip_sec=5.0, tts_sec=6.2,
                                        atempo=1.5, validate=False)
    with pytest.raises(TimelineError) as exc:
        validate_timeline(raw)
    assert exc.value.code == "invalid_atempo"


def test_padded_render_keeps_full_audio_and_equal_lengths(tmp_path):
    """pad_video_tail 实渲染：人声尾音保留，音画等长（不靠 atrim 截字）."""
    from shared.media_inspect import probe_media

    timeline, _ = _one_clip_timeline(tmp_path, clip_sec=2.0, tts_sec=2.8,
                                     pad_sec=0.8)
    assert abs(timeline.clips[0].duration_sec - 2.8) < 0.06
    out = tmp_path / "padded.mp4"
    result = render(timeline, out, tmp_path / "work", settings=EncodeSettings(
        width=320, height=240, preset="ultrafast", crf="28"))
    assert abs(float(result.duration_sec) - timeline.total_duration_sec) < 0.25
    info = probe_media(str(out))
    streams = info.get("streams") or []
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    assert audio is not None and video is not None
    v_dur = float(video.get("duration") or result.duration_sec)
    a_dur = float(audio.get("duration") or result.duration_sec)
    assert a_dur >= 2.7            # 2.8s 人声没有被截掉
    assert abs(v_dur - a_dur) <= 0.15   # 音画等长


def test_production_backend_render_honors_pad(tmp_path):
    """生产后端（compile_graph + render）也按 pad 补帧，不改其它行为."""
    timeline, _ = _one_clip_timeline(tmp_path, clip_sec=2.0, tts_sec=2.8,
                                     pad_sec=0.8)
    out = tmp_path / "backend.mp4"
    result = render(timeline, out, tmp_path / "work2", settings=EncodeSettings(
        width=320, height=240, preset="ultrafast", crf="28"))
    assert out.is_file() and out.stat().st_size > 1024
    assert result.duration_sec > 0
    assert abs(float(result.duration_sec) - timeline.total_duration_sec) < 0.25
