# -*- coding: utf-8 -*-
"""BGM 氛围层与确定性 Ducking（2026-11-28 O8）.

全部离线：FFmpeg 现场生成正弦波，`volumedetect`/`ffprobe` 实测电平、时长与采样率。
不调用任何模型、不读 `output/`。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step04_merge import bgm_mix as bgm

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(not FFMPEG or not FFPROBE,
                                reason="需要 ffmpeg/ffprobe")


def _run(cmd: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=timeout)


def _sine(path: Path, *, seconds: float, freq: int, rate: int = 48000) -> Path:
    result = _run([FFMPEG, "-y", "-f", "lavfi",
                   "-i", f"sine=frequency={freq}:sample_rate={rate}:duration={seconds}",
                   "-ac", "2", "-c:a", "pcm_s16le", str(path)])
    assert result.returncode == 0, result.stderr[-2000:]
    return path


def _apply_filter(source: Path, chain: str, target: Path, *,
                  extra_input_args: list[str] | None = None) -> Path:
    args = [FFMPEG, "-y"]
    args.extend(extra_input_args or [])
    args.extend(["-i", str(source), "-filter_complex",
                 f"[0:a]{chain}[out]", "-map", "[out]",
                 "-c:a", "pcm_s16le", str(target)])
    result = _run(args)
    assert result.returncode == 0, result.stderr[-3000:]
    return target


def _mean_volume_db(path: Path, *, start: float, end: float) -> float:
    result = _run([FFMPEG, "-i", str(path), "-af",
                   f"atrim=start={start}:end={end},volumedetect", "-f", "null", "-"])
    assert result.returncode == 0, result.stderr[-2000:]
    match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", result.stderr)
    assert match, result.stderr[-2000:]
    return float(match.group(1))


def _probe(path: Path) -> dict:
    result = _run([FFPROBE, "-v", "error", "-print_format", "json",
                   "-show_format", "-show_streams", str(path)])
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# 配置与区间
# ---------------------------------------------------------------------------
def test_disabled_config_returns_none():
    assert bgm.BgmConfig.from_mapping({"enabled": False}) is None
    assert bgm.BgmConfig.from_mapping(None) is None


@pytest.mark.parametrize("kwargs,code", [
    ({"path": "x.wav", "base_gain_db": -26.0, "duck_gain_db": -16.0}, "bgm_gain_order"),
    ({"path": ""}, "bgm_missing"),
    ({"path": "x.ogg"}, "bgm_format"),
    ({"path": "x.wav", "attack_sec": -0.1}, "bgm_config_invalid"),
    ({"path": "x.wav", "fade_in_sec": float("nan")}, "bgm_config_invalid"),
])
def test_config_validation_fails_closed(tmp_path, kwargs, code):
    data = {"path": str(tmp_path / "missing.wav")}
    data.update(kwargs)
    cfg = bgm.BgmConfig(**data)
    with pytest.raises(bgm.BgmError) as excinfo:
        cfg.validate(require_file=True)
    assert excinfo.value.code == code


def test_valid_config_requires_existing_file(tmp_path):
    path = tmp_path / "bgm.wav"
    path.write_bytes(b"RIFF")
    cfg = bgm.BgmConfig(path=str(path)).validate(require_file=True)
    assert cfg.base_linear == pytest.approx(bgm.db_to_linear(-16.0))
    with pytest.raises(bgm.BgmError):
        bgm.BgmConfig(path=str(tmp_path / "nope.wav")).validate()


def test_voice_intervals_merge_shot_and_narration():
    timeline = SimpleNamespace(
        windows=[SimpleNamespace(start_sec=0.0, end_sec=5.0),
                 SimpleNamespace(start_sec=5.0, end_sec=9.0)],
        audio_tracks=[
            SimpleNamespace(kind="shot_tts", clip_index=0),
            SimpleNamespace(kind="narration", start_sec=5.0, end_sec=7.0),
            SimpleNamespace(kind="shot_tts", clip_index=None),
        ])
    assert bgm.voice_intervals(timeline) == [(0.0, 7.0)]


def test_duck_segments_expand_by_attack_release_and_clip():
    segments = bgm.duck_segments([(2.0, 5.0)], attack=0.15, release=0.35, total=10.0)
    assert segments == [(1.85, 2.0, 5.0, 5.35)]
    clipped = bgm.duck_segments([(0.05, 9.9)], attack=0.5, release=0.5, total=10.0)
    assert clipped == [(0.0, 0.05, 9.9, 10.0)]


def test_envelope_expression_is_deterministic_and_nested():
    first = bgm.envelope_expression([(1.85, 2.0, 5.0, 5.35)],
                                    base_linear=bgm.db_to_linear(-16.0),
                                    duck_linear=bgm.db_to_linear(-26.0))
    second = bgm.envelope_expression([(1.85, 2.0, 5.0, 5.35)],
                                     base_linear=bgm.db_to_linear(-16.0),
                                     duck_linear=bgm.db_to_linear(-26.0))
    assert first == second
    assert first.startswith("if(lt(t,1.850000)")
    assert "%.9f" % bgm.db_to_linear(-26.0) in first


def test_envelope_expression_merges_when_too_long(monkeypatch):
    monkeypatch.setattr(bgm, "MAX_EXPRESSION_CHARS", 400)
    segments = [(i * 2.0, i * 2.0 + 0.5, i * 2.0 + 1.0, i * 2.0 + 1.5)
                for i in range(20)]
    expr = bgm.envelope_expression(segments, base_linear=0.15, duck_linear=0.05)
    assert len(expr) <= 400 or len(segments) == 1


# ---------------------------------------------------------------------------
# FFmpeg 实测：Ducking 深度
# ---------------------------------------------------------------------------
def test_ducking_depth_matches_configured_gain(tmp_path):
    source = _sine(tmp_path / "bgm.wav", seconds=10, freq=440)
    cfg = bgm.BgmConfig(path=str(source), base_gain_db=-16.0, duck_gain_db=-26.0,
                        fade_in_sec=0.0, fade_out_sec=0.0,
                        attack_sec=0.15, release_sec=0.35)
    chain = bgm.bgm_chain(cfg, total_duration=10.0, intervals=[(2.0, 5.0)])
    out = _apply_filter(source, chain, tmp_path / "ducked.wav")

    base_db = _mean_volume_db(out, start=0.5, end=1.5)
    duck_db = _mean_volume_db(out, start=3.0, end=4.0)
    delta = base_db - duck_db
    assert abs(delta - 10.0) <= 1.5, f"压低差值 {delta:.2f} dB 偏离 10 dB"


def test_fade_in_and_out_are_applied(tmp_path):
    source = _sine(tmp_path / "bgm.wav", seconds=10, freq=440)
    cfg = bgm.BgmConfig(path=str(source), base_gain_db=-16.0, duck_gain_db=-26.0,
                        fade_in_sec=1.0, fade_out_sec=2.0,
                        attack_sec=0.15, release_sec=0.35)
    chain = bgm.bgm_chain(cfg, total_duration=10.0, intervals=[])
    out = _apply_filter(source, chain, tmp_path / "faded.wav")
    head = _mean_volume_db(out, start=0.0, end=0.1)
    body = _mean_volume_db(out, start=4.0, end=5.0)
    assert body - head > 6.0, f"淡入没有生效：head={head} body={body}"


def test_short_bgm_loops_to_total_duration(tmp_path):
    source = _sine(tmp_path / "short.wav", seconds=3, freq=440)
    cfg = bgm.BgmConfig(path=str(source), base_gain_db=-16.0, duck_gain_db=-26.0,
                        fade_in_sec=0.0, fade_out_sec=0.0,
                        attack_sec=0.15, release_sec=0.35)
    chain = bgm.bgm_chain(cfg, total_duration=9.0, intervals=[])
    out = _apply_filter(source, chain, tmp_path / "looped.wav",
                        extra_input_args=list(bgm.bgm_input_args(cfg)))
    probe = _probe(out)
    duration = float(probe["format"]["duration"])
    assert abs(duration - 9.0) < 0.05, f"循环后时长 {duration} 偏离 9s"
    audio = next(s for s in probe["streams"] if s.get("codec_type") == "audio")
    assert int(audio["sample_rate"]) == 48000


def test_mix_keeps_voice_level_and_output_rate(tmp_path):
    voice = _sine(tmp_path / "voice.wav", seconds=10, freq=880)
    source = _sine(tmp_path / "bgm.wav", seconds=10, freq=440)
    cfg = bgm.BgmConfig(path=str(source), base_gain_db=-16.0, duck_gain_db=-26.0,
                        fade_in_sec=0.0, fade_out_sec=0.0,
                        attack_sec=0.15, release_sec=0.35)
    chain = bgm.bgm_chain(cfg, total_duration=10.0, intervals=[(2.0, 5.0)])
    # 与生产同样的 amix：normalize=0，权重 1 1
    result = _run([FFMPEG, "-y", "-i", str(voice), "-i", str(source),
                   "-filter_complex",
                   f"[1:a]{chain}[bgm];[0:a][bgm]amix=inputs=2:duration=first:"
                   f"normalize=0:weights=1 1[out]",
                   "-map", "[out]", "-c:a", "pcm_s16le", str(tmp_path / "mix.wav")])
    assert result.returncode == 0, result.stderr[-3000:]
    probe = _probe(tmp_path / "mix.wav")
    assert abs(float(probe["format"]["duration"]) - 10.0) < 0.05
    audio = next(s for s in probe["streams"] if s.get("codec_type") == "audio")
    assert int(audio["sample_rate"]) == 48000

    # 无人声段：BGM 在，人声在混音里不该被 amix 二次衰减（-16dB≈0.158，
    # 880Hz 人声本身是 -3dB 左右，混合后应接近人声电平而非再降 ~6dB）
    mixed_db = _mean_volume_db(tmp_path / "mix.wav", start=6.0, end=7.0)
    voice_db = _mean_volume_db(voice, start=6.0, end=7.0)
    assert abs(mixed_db - voice_db) <= 1.5, (
        f"amix 把主音轨压低了：mix={mixed_db} voice={voice_db}")


# ---------------------------------------------------------------------------
# 接线：关闭时零改动，开启时追加输入与分支
# ---------------------------------------------------------------------------
def test_disabled_bgm_leaves_graph_untouched():
    parts = ["[0:a]anull[na0]"]
    label, inputs = bgm.apply_bgm_to_graph(
        parts=parts, audio_label="[na0]", inputs=(), timeline=None,
        config=None, total_duration=10.0)
    assert label == "[na0]"
    assert inputs == ()
    assert parts == ["[0:a]anull[na0]"]


def test_enabled_bgm_appends_input_and_branch(tmp_path):
    path = _sine(tmp_path / "bgm.wav", seconds=2, freq=440)
    cfg = bgm.BgmConfig(path=str(path))
    parts: list[str] = []
    timeline = SimpleNamespace(windows=[SimpleNamespace(start_sec=0.0, end_sec=8.0)],
                               audio_tracks=[SimpleNamespace(kind="shot_tts",
                                                             clip_index=0)])
    label, inputs = bgm.apply_bgm_to_graph(
        parts=parts, audio_label="[ax0]", inputs=(), timeline=timeline,
        config=cfg, total_duration=8.0)
    assert label == "[outabgm]"
    assert len(inputs) == 1 and inputs[0].role == "bgm"
    assert inputs[0].extra_args == ("-stream_loop", "-1")
    joined = ";".join(parts)
    assert "volume='" in joined and "amix=inputs=2:duration=first:normalize=0" in joined


def test_resolve_local_sfx_is_pure(tmp_path):
    sfx = tmp_path / "sfx"
    sfx.mkdir()
    (sfx / "thunder.wav").write_bytes(b"RIFF")
    assert bgm.resolve_local_sfx("thunder", sfx)["path"] == str(sfx / "thunder.wav")
    assert bgm.resolve_local_sfx({"sfx": "rain"}, sfx)["reason"] == "not_found"
    assert bgm.resolve_local_sfx("thunder", None)["reason"] == "sfx_dir_unset"
    assert bgm.resolve_local_sfx("", sfx)["reason"] == "cue_empty"
    assert bgm.resolve_local_sfx("../../etc/passwd", sfx)["reason"] == "not_found"
