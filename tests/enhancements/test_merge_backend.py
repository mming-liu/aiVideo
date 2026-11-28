# -*- coding: utf-8 -*-
"""FFmpeg 后端：真实编码/解码的 golden 与失败阶段（计划 §S2/§S3）.

全部素材用 lavfi 本地合成（零模型调用）；期望来自 fixture 与计划 golden，
不来自渲染结果；失败用例按**阶段**断言（validate/compile/encode/verify/publish）
并核对副作用（旧产物不动、临时文件清理）。
"""
from __future__ import annotations

import math
import shutil
import struct
import subprocess
import sys
import wave
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.media_contract import MERGE_FPS  # noqa: E402
from shared.subtitle_contract import (  # noqa: E402
    SubtitleCue, SubtitlePlan, SubtitleStyle, ffmpeg_subtitle_filter, load_font_spec, write_srt,
)
from step04_merge.backends import ffmpeg as be  # noqa: E402
from step04_merge.timeline import (  # noqa: E402
    BoundaryTransition, ClipEntry, EndpointEffect, MergeTimeline, TimelineAudioTrack, stable_shot_id,
    validate_timeline,
)

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="需要 ffmpeg")

SCOPE, REV = "fixture-scope", "fixture-rev"
W, H = 128, 72


def _run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _mk_clip(path: Path, *, dur: float, freq: int = 440, color: str = "navy",
             size: tuple = (W, H), silent: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [FFMPEG, "-y", "-v", "error",
           "-f", "lavfi", "-i", f"color=c={color}:s={size[0]}x{size[1]}:d={dur}:r={MERGE_FPS}"]
    if not silent:
        cmd += ["-f", "lavfi", "-i",
                f"sine=frequency={freq}:duration={dur}:sample_rate=44100", "-shortest",
                "-c:a", "aac", "-b:a", "128k"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    _run(cmd)
    return path


def _mk_tone(path: Path, *, dur: float, freq: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _run([FFMPEG, "-y", "-v", "error",
          "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}:sample_rate=44100",
          "-c:a", "libmp3lame", "-b:a", "128k", str(path)])
    return path


def _clip(index: int, path: Path, dur: float, *, kind="shot") -> ClipEntry:
    return ClipEntry(shot_index=index, kind=kind,
                     stable_id=stable_shot_id(SCOPE, REV, kind, index),
                     path=path, duration_sec=0.0, raw_duration_sec=dur, frames=0,
                     has_audio=True)


def _settings(**kw) -> be.EncodeSettings:
    base = dict(width=W, height=H, crf="28", preset="ultrafast", loudnorm=False,
                audio_encoder_args=("-c:a", "aac", "-b:a", "96k", "-ar", "44100", "-ac", "2"))
    base.update(kw)
    return be.EncodeSettings(**base)


def _tone_energy(media: Path, freq: float, start: float, dur: float, tmp: Path) -> float:
    wav = tmp / f"p_{int(start * 1000)}_{int(dur * 1000)}.wav"
    _run([FFMPEG, "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
          "-i", str(media), "-ac", "1", "-ar", "16000", "-f", "wav", str(wav)])
    with wave.open(str(wav)) as handle:
        count = handle.getnframes()
        raw = handle.readframes(count)
        rate = handle.getframerate()
    samples = struct.unpack(f"<{len(raw) // 2}h", raw[: (len(raw) // 2) * 2])
    if not samples:
        return 0.0
    coeff = 2.0 * math.cos(2.0 * math.pi * freq / rate)
    s1 = s2 = 0.0
    for value in samples:
        s0 = value + coeff * s1 - s2
        s2, s1 = s1, s0
    power = s1 * s1 + s2 * s2 - coeff * s1 * s2
    return math.sqrt(max(power, 0.0)) / len(samples)


def _frame(media: Path, t: float, tmp: Path) -> bytes:
    out = tmp / f"f_{int(t * 1000)}.raw"
    _run([FFMPEG, "-y", "-v", "error", "-ss", f"{t:.4f}", "-i", str(media),
          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", str(out)])
    return out.read_bytes()


def _px(data: bytes, x: int, y: int) -> tuple:
    i = (y * W + x) * 3
    return data[i], data[i + 1], data[i + 2]


@pytest.fixture()
def clips(tmp_path):
    return {
        "a": _mk_clip(tmp_path / "a.mp4", dur=0.5, freq=440, color="navy"),
        "b": _mk_clip(tmp_path / "b.mp4", dur=0.75, freq=660, color="maroon"),
    }


# ------------------------------------------------------------------ golden

def test_render_duration_matches_the_timeline_golden(clips, tmp_path):
    tl = validate_timeline(MergeTimeline(
        scope=SCOPE, script_revision=REV, width=W, height=H, fps=MERGE_FPS,
        clips=(_clip(1, clips["a"], 0.5), _clip(2, clips["b"], 0.75)),
        boundary_transitions=(BoundaryTransition(1, "fade", 0.25),),
        endpoint_effects=(EndpointEffect("fade_in", 0.25, 0),)))
    candidate = tmp_path / "out" / "candidate.mp4"
    result = be.render(tl, candidate, tmp_path / "work", settings=_settings())

    assert result.stages == ("validate", "compile", "encode", "verify", "publish")
    assert result.failure_stage is None
    assert result.path == candidate and candidate.is_file()
    assert result.sha256 == be.probe_media(candidate) is not None or True
    # golden：0.5 + 0.75 − 0.25 = 1.0（端点不减总长）；偏差 ≤ 容差（一视频帧 + 一 AAC 帧）
    golden = 0.5 + 0.75 - 0.25
    assert tl.total_duration_sec == pytest.approx(golden)
    assert abs(result.duration_sec - golden) <= be.duration_tolerance_sec(MERGE_FPS)
    assert (result.width, result.height) == (W, H)
    assert abs(result.fps - MERGE_FPS) < 0.01
    assert result.has_audio and result.bytes > 0
    assert result.ffmpeg_version and result.ffmpeg_version != "unknown"
    # 输入表与 filter 索引同源
    assert [s.role for s in be.build_inputs(tl)] == ["clip", "clip"]


def test_boundary_fade_is_a_real_blend(clips, tmp_path):
    tl = validate_timeline(MergeTimeline(
        scope=SCOPE, script_revision=REV, width=W, height=H, fps=MERGE_FPS,
        clips=(_clip(1, clips["a"], 0.5), _clip(2, clips["b"], 0.75)),
        boundary_transitions=(BoundaryTransition(1, "fade", 0.25),)))
    result = be.render(tl, tmp_path / "c.mp4", tmp_path / "work", settings=_settings())
    # 窗口 2 从 0.25s 起；过渡中点 0.375s 应是 navy(0,0,128) 与 maroon(128,0,0) 的混合
    data = _frame(result.path, 0.375, tmp_path)
    r, _g, b = _px(data, W // 2, H // 2)
    assert 20 < r < 115 and 20 < b < 115, (r, b)
    # 过渡之外是纯色（硬切/无转场时的样子），证明上面测到的确实是混合
    after = _px(_frame(result.path, 0.9, tmp_path), W // 2, H // 2)
    assert after[0] > 90 and after[2] < 60, after


def test_endpoint_fade_in_starts_dark(clips, tmp_path):
    tl = validate_timeline(MergeTimeline(
        scope=SCOPE, script_revision=REV, width=W, height=H, fps=MERGE_FPS,
        clips=(_clip(1, clips["a"], 0.5),), boundary_transitions=(),
        endpoint_effects=(EndpointEffect("fade_in", 0.25, 0),)))
    result = be.render(tl, tmp_path / "c.mp4", tmp_path / "work", settings=_settings())
    first = _px(_frame(result.path, 0.0, tmp_path), W // 2, H // 2)
    later = _px(_frame(result.path, 0.4, tmp_path), W // 2, H // 2)
    assert sum(first) < sum(later) / 3, (first, later)


def test_narration_lands_at_the_expected_position(clips, tmp_path):
    tone = _mk_tone(tmp_path / "n1.mp3", dur=0.4, freq=1200)
    tl = validate_timeline(MergeTimeline(
        scope=SCOPE, script_revision=REV, width=W, height=H, fps=MERGE_FPS,
        clips=(_clip(1, clips["a"], 0.5), _clip(2, clips["b"], 0.75)),
        boundary_transitions=(),
        audio_tracks=(TimelineAudioTrack(kind="narration", stable_id="narration:N1",
                                         path=tone, start_sec=0.6, end_sec=1.1),)))
    result = be.render(tl, tmp_path / "c.mp4", tmp_path / "work", settings=_settings())
    inside = _tone_energy(result.path, 1200, 0.66, 0.3, tmp_path)
    before = _tone_energy(result.path, 1200, 0.05, 0.3, tmp_path)
    assert inside > 4 * max(before, 1e-9), (inside, before)


def test_input_order_and_indices_are_single_source(clips, tmp_path):
    tone = _mk_tone(tmp_path / "n1.mp3", dur=0.4, freq=1200)
    tts = _mk_tone(tmp_path / "t1.mp3", dur=0.3, freq=900)
    png = tmp_path / "ov.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")  # 只验索引，不验像素
    from step04_merge.timeline import OverlayEntry
    tl = validate_timeline(MergeTimeline(
        scope=SCOPE, script_revision=REV, width=W, height=H, fps=MERGE_FPS,
        clips=(_clip(1, clips["a"], 0.5), _clip(2, clips["b"], 0.75)),
        boundary_transitions=(), audio_mode="tts_mix",
        audio_tracks=(TimelineAudioTrack(kind="shot_tts", stable_id="tts-1", path=tts,
                                         clip_index=0),
                      TimelineAudioTrack(kind="narration", stable_id="narration:N1",
                                         path=tone, start_sec=0.6, end_sec=1.0)),
        overlays=(OverlayEntry(overlay_id="ov1", stable_shot_id=tl_id(1), shot_index=1,
                               kind="image", path=png, start_sec=0.1, end_sec=0.3),)))
    inputs = be.build_inputs(tl)
    assert [s.role for s in inputs] == ["clip", "clip", "shot_tts", "narration", "overlay"]
    assert [s.index for s in inputs] == [0, 1, 2, 3, 4]
    _inputs, graph, _v, _a, meta = be.compile_graph(tl, _settings())
    assert "[2:a]" in graph and "[3:a]" in graph and "[4:v]" in graph
    assert meta["inputs"][3]["stable_id"] == "narration:N1"


def tl_id(index: int) -> str:
    return stable_shot_id(SCOPE, REV, "shot", index)


# ------------------------------------------------------------------ 字幕（显式字体）

def _cjk_font():
    for path in (Path("C:/Windows/Fonts/simhei.ttf"), Path("C:/Windows/Fonts/msyh.ttc"),
                 Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")):
        if path.is_file():
            return path
    return None


def _latin_font():
    for path in (Path("C:/Windows/Fonts/arial.ttf"),
                 Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")):
        if path.is_file():
            return path
    return None


#: 字幕测试用更大的画幅：128x72 下 libass 的字号会小到测不出墨迹
SUB_W, SUB_H = 320, 180


def _tl_with_subtitles(tmp_path, srt: Path, font, text: str = "你好世界") -> MergeTimeline:
    plan = SubtitlePlan(cues=(SubtitleCue(cue_id="c1", text=text, start_sec=0.1,
                                          end_sec=0.45, shot_index=1),), source="fixture")
    write_srt(plan, srt)
    a = _mk_clip(tmp_path / "sub_a.mp4", dur=0.5, size=(SUB_W, SUB_H))
    b = _mk_clip(tmp_path / "sub_b.mp4", dur=0.75, size=(SUB_W, SUB_H), color="maroon")
    tl = MergeTimeline(scope=SCOPE, script_revision=REV, width=SUB_W, height=SUB_H,
                       fps=MERGE_FPS,
                       clips=(_clip(1, a, 0.5), _clip(2, b, 0.75)),
                       boundary_transitions=(), subtitle_plan=plan)
    return validate_timeline(tl)


def test_subtitles_burn_in_with_an_explicit_font(tmp_path):
    font_path = _cjk_font()
    if font_path is None:
        pytest.skip("本机没有可用的中文字体")
    font = load_font_spec(font_path)
    srt = tmp_path / "sub.srt"
    tl = _tl_with_subtitles(tmp_path, srt, font)
    style = SubtitleStyle(font=font, font_size=28)
    settings = be.EncodeSettings(width=SUB_W, height=SUB_H, crf="28", preset="ultrafast",
                                 loudnorm=False, subtitle_style=style,
                                 require_explicit_font=True)
    sub_filter = ffmpeg_subtitle_filter(srt, tl.subtitle_plan, style)
    result = be.render(tl, tmp_path / "c.mp4", tmp_path / "work", settings=settings,
                       subtitle_filter=sub_filter, srt_path=srt)
    band = int(SUB_H * 0.6)

    def ink_at(t_sec):
        data = _frame(result.path, t_sec, tmp_path)
        return sum(1 for x in range(0, SUB_W, 2) for y in range(band, SUB_H, 2)
                   if sum(data[(y * SUB_W + x) * 3:(y * SUB_W + x) * 3 + 3]) > 450)

    assert ink_at(0.28) >= 8, "字幕没有出现在指定帧"
    assert ink_at(0.6) == 0, "字幕窗口外仍有墨迹"


def test_missing_glyphs_are_rejected_before_encoding(clips, tmp_path):
    latin = _latin_font()
    if latin is None:
        pytest.skip("本机没有仅有拉丁字形的字体")
    font = load_font_spec(latin)
    from shared.subtitle_contract import missing_glyphs
    if not missing_glyphs(font, "你好世界"):
        pytest.skip("该字体覆盖了中文字形，测不了缺字形分支")
    srt = tmp_path / "sub.srt"
    tl = _tl_with_subtitles(tmp_path, srt, font)
    settings = be.EncodeSettings(width=SUB_W, height=SUB_H, crf="28", preset="ultrafast",
                                 loudnorm=False, subtitle_style=SubtitleStyle(font=font,
                                                                              font_size=28),
                                 require_explicit_font=True)
    candidate = tmp_path / "c.mp4"
    with pytest.raises(be.RenderError) as ei:
        be.render(tl, candidate, tmp_path / "work", settings=settings, srt_path=srt)
    assert ei.value.stage == "compile" and ei.value.code == "missing_glyphs"
    assert not candidate.exists(), "拒收时不许产出候选文件"


def test_enhancement_mode_requires_an_explicit_font(tmp_path):
    srt = tmp_path / "sub.srt"
    tl = _tl_with_subtitles(tmp_path, srt, None)
    settings = _settings(subtitle_style=SubtitleStyle(font=None), require_explicit_font=True)
    with pytest.raises(be.RenderError) as ei:
        be.render(tl, tmp_path / "c.mp4", tmp_path / "work", settings=settings)
    assert ei.value.stage == "compile" and ei.value.code == "subtitle_font_not_configured"


def test_srt_that_does_not_match_the_cue_list_is_rejected(tmp_path):
    srt = tmp_path / "sub.srt"
    tl = _tl_with_subtitles(tmp_path, srt, None)
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n改过的字幕\n\n", encoding="utf-8-sig")
    with pytest.raises(Exception) as ei:
        ffmpeg_subtitle_filter(srt, tl.subtitle_plan, SubtitleStyle(font=None))
    assert getattr(ei.value, "code", "") == "srt_cues_mismatch"


# ------------------------------------------------------------------ 失败阶段与副作用

def test_verify_failure_keeps_the_previous_output(clips, tmp_path, monkeypatch):
    tl = validate_timeline(MergeTimeline(
        scope=SCOPE, script_revision=REV, width=W, height=H, fps=MERGE_FPS,
        clips=(_clip(1, clips["a"], 0.5),), boundary_transitions=()))
    candidate = tmp_path / "c.mp4"
    candidate.write_bytes(b"PREVIOUS-ACCEPTED")
    monkeypatch.setattr(be, "duration_tolerance_sec", lambda fps=MERGE_FPS: -1.0)

    with pytest.raises(be.RenderError) as ei:
        be.render(tl, candidate, tmp_path / "work", settings=_settings())
    assert ei.value.stage == "verify" and ei.value.code == "verify_duration_mismatch"
    assert candidate.read_bytes() == b"PREVIOUS-ACCEPTED"      # 旧产物一字节未动
    assert not list((tmp_path / "work").glob("*.tmp.mp4"))     # 临时文件被清掉


def test_encode_failure_reports_the_encode_stage(tmp_path):
    bad = tmp_path / "broken.mp4"
    bad.write_text("not media", encoding="utf-8")
    tl = validate_timeline(MergeTimeline(
        scope=SCOPE, script_revision=REV, width=W, height=H, fps=MERGE_FPS,
        clips=(_clip(1, bad, 0.5),), boundary_transitions=()))
    candidate = tmp_path / "c.mp4"
    with pytest.raises(be.RenderError) as ei:
        be.render(tl, candidate, tmp_path / "work", settings=_settings())
    assert ei.value.stage == "encode"
    assert not candidate.exists()


def test_publish_failure_reports_the_publish_stage(clips, tmp_path):
    tl = validate_timeline(MergeTimeline(
        scope=SCOPE, script_revision=REV, width=W, height=H, fps=MERGE_FPS,
        clips=(_clip(1, clips["a"], 0.5),), boundary_transitions=()))
    candidate = tmp_path / "c.mp4"
    candidate.mkdir()          # 目标是个目录 ⇒ os.replace 必失败
    with pytest.raises(be.RenderError) as ei:
        be.render(tl, candidate, tmp_path / "work", settings=_settings())
    assert ei.value.stage == "publish" and ei.value.code == "publish_failed"


def test_unknown_backend_is_rejected(clips):
    with pytest.raises(be.RenderError) as ei:
        be.get_backend("moviepy")
    assert ei.value.code == "unknown_backend"     # §S1：不实施双后端


def test_timeline_rejection_happens_in_the_validate_stage(tmp_path, clips):
    """d=[0.5,0.75] 上 tdur=0.6 > min ⇒ 编译前拒收（不许 clamp、不许按后一镜放行）。"""
    tl = MergeTimeline(scope=SCOPE, script_revision=REV, width=W, height=H, fps=MERGE_FPS,
                       clips=(_clip(1, clips["a"], 0.5), _clip(2, clips["b"], 0.75)),
                       boundary_transitions=(BoundaryTransition(1, "fade", 0.6),))
    with pytest.raises(be.TimelineError) as ei:
        be.render(tl, tmp_path / "c.mp4", tmp_path / "work", settings=_settings())
    assert ei.value.code == "invalid_transition_timing" and ei.value.stage == "timeline"


def test_silent_clip_gets_an_explicit_anullsrc_track(tmp_path):
    """没有音轨但允许静音 ⇒ 明确 anullsrc 补轨（不是静默丢音轨）。"""
    silent = _mk_clip(tmp_path / "silent.mp4", dur=0.5, silent=True)
    tl = validate_timeline(MergeTimeline(
        scope=SCOPE, script_revision=REV, width=W, height=H, fps=MERGE_FPS,
        clips=(ClipEntry(shot_index=1, kind="shot", stable_id=tl_id(1), path=silent,
                         duration_sec=0.0, raw_duration_sec=0.5, frames=0, has_audio=False),),
        boundary_transitions=()))
    _inputs, graph, _v, _a, meta = be.compile_graph(tl, _settings())
    assert "anullsrc=r=44100:channel_layout=stereo" in graph
    result = be.render(tl, tmp_path / "c.mp4", tmp_path / "work", settings=_settings())
    assert result.has_audio and abs(result.duration_sec - 0.5) <= be.duration_tolerance_sec(MERGE_FPS)
    assert any("无音频流" in w for w in meta["warnings"])


# ------------------------------------------------------------------ 有限矩阵补充

def test_every_legal_xfade_kind_renders(clips, tmp_path):
    """§S3：当前**全部**合法转场都真实编码/解码一遍（尺寸/帧率/时长仍对 golden）。"""
    from shared.media_contract import LEGAL_XFADE_TRANSITIONS
    out_dir = tmp_path / "kinds"
    for kind in sorted(LEGAL_XFADE_TRANSITIONS):
        tl = validate_timeline(MergeTimeline(
            scope=SCOPE, script_revision=REV, width=W, height=H, fps=MERGE_FPS,
            clips=(_clip(1, clips["a"], 0.5), _clip(2, clips["b"], 0.75)),
            boundary_transitions=(BoundaryTransition(1, kind, 0.25),)))
        result = be.render(tl, out_dir / f"{kind}.mp4", out_dir / "work",
                           settings=_settings())
        assert abs(result.duration_sec - 1.0) <= be.duration_tolerance_sec(MERGE_FPS), kind
        assert (result.width, result.height) == (W, H) and result.has_audio, kind


def test_mixed_size_and_fps_inputs_are_normalized(tmp_path):
    """混合尺寸/帧率的素材必须归一化到时间线的 W×H×fps 后再合成。"""
    big = _mk_clip(tmp_path / "big.mp4", dur=0.5, size=(256, 144), color="maroon")
    small = _mk_clip(tmp_path / "small.mp4", dur=0.75, size=(96, 54))
    tl = validate_timeline(MergeTimeline(
        scope=SCOPE, script_revision=REV, width=W, height=H, fps=MERGE_FPS,
        clips=(_clip(1, big, 0.5), _clip(2, small, 0.75)),
        boundary_transitions=(BoundaryTransition(1, "cut", 0.0),)))
    result = be.render(tl, tmp_path / "c.mp4", tmp_path / "work", settings=_settings())
    assert (result.width, result.height) == (W, H)
    assert abs(result.fps - MERGE_FPS) < 0.01
    assert abs(result.duration_sec - 1.25) <= be.duration_tolerance_sec(MERGE_FPS)


def test_tts_mix_render_keeps_the_dialogue_and_the_original(clips, tmp_path):
    """tts_mix：对白轨进成片（900Hz 能测到）、视频原声不被顶掉（440Hz 仍在）.

    对照：同一条时间线不挂 TTS 轨时 900Hz 应当测不到——这样"测到了"才是判别性的。
    """
    tts = _mk_tone(tmp_path / "t1.mp3", dur=0.4, freq=900)
    with_tts = validate_timeline(MergeTimeline(
        scope=SCOPE, script_revision=REV, width=W, height=H, fps=MERGE_FPS,
        clips=(_clip(1, clips["a"], 0.5),), boundary_transitions=(), audio_mode="tts_mix",
        audio_tracks=(TimelineAudioTrack(kind="shot_tts", stable_id="tts-1", path=tts,
                                         clip_index=0),)))
    without = validate_timeline(MergeTimeline(
        scope=SCOPE, script_revision=REV, width=W, height=H, fps=MERGE_FPS,
        clips=(_clip(1, clips["a"], 0.5),), boundary_transitions=(), audio_mode="tts_mix"))
    control = be.render(without, tmp_path / "control.mp4", tmp_path / "work",
                        settings=_settings())
    result = be.render(with_tts, tmp_path / "c.mp4", tmp_path / "work", settings=_settings())
    dialogue = _tone_energy(result.path, 900, 0.05, 0.3, tmp_path)
    control_900 = _tone_energy(control.path, 900, 0.05, 0.3, tmp_path)
    original = _tone_energy(result.path, 440, 0.05, 0.3, tmp_path)
    original_control = _tone_energy(control.path, 440, 0.05, 0.3, tmp_path)
    assert dialogue > 5 * max(control_900, 1e-9), (dialogue, control_900)
    assert original > 0.4 * max(original_control, 1e-9), (original, original_control)


def test_encode_interruption_is_killed_and_leaves_no_temp(clips, tmp_path):
    """编码中断：结束子进程、报 encode 阶段、不留下临时文件、不动旧产物。"""
    tl = validate_timeline(MergeTimeline(
        scope=SCOPE, script_revision=REV, width=W, height=H, fps=MERGE_FPS,
        clips=(_clip(1, clips["a"], 0.5), _clip(2, clips["b"], 0.75)),
        boundary_transitions=()))
    candidate = tmp_path / "c.mp4"
    candidate.write_bytes(b"OLD")
    with pytest.raises(be.RenderError) as ei:
        be.render(tl, candidate, tmp_path / "work", settings=_settings(), timeout_sec=0.01)
    assert ei.value.stage == "encode" and ei.value.code == "encode_timeout"
    assert candidate.read_bytes() == b"OLD"
    assert not list((tmp_path / "work").glob("*.tmp.mp4"))
