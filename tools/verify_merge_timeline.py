# -*- coding: utf-8 -*-
"""tools/verify_merge_timeline.py - Step04 合并时间线的**独立**验收工具（计划 §S3）.

用法::

    python tools/verify_merge_timeline.py --fixture FILE --out-dir DIR
    python tools/verify_merge_timeline.py --emit-sample-fixture spec.json   # 生成样例 fixture 规格

做法（不调用任何生成模型，全部本地 FFmpeg + 标准库）：

1. 按 fixture 规格**冻结**素材（缺素材时用 lavfi 合成 clip/音轨、用标准库写 RGBA PNG）；
2. 走真实新入口：``MergeTimeline`` → ``step04_merge.backends.ffmpeg.render``（真实编码/解码）；
3. **独立** golden 断言（期望来自 fixture，不来自渲染结果）：
   - 总时长与 ``Σd − Σtdur`` 偏差 ≤ 1 帧（+ 一 AAC 帧的容器粒度）；
   - 声音落点：按采样率/编码延迟给出容差（不超过一视频帧与一 AAC 帧的较大者），
     用 Goertzel 单频能量在**解码后的 PCM** 上测量；
   - 字幕：盘上 SRT 独立解析后与 fixture 授权 cue 逐条一致；指定帧出现字幕、窗口外不出现；
   - overlay：指定帧出现（含 straight alpha 的半透明混合），窗口外消失、结束不重复最后一帧；
4. 旧实现对照：在**隔离 OUTPUT_BASE** 里以子进程跑旧入口（含 vimax ``shots/<i>/video.mp4``
   与 ``clips/clip_0.mp4`` 的素材布局），保存 rc/命令/日志/产物；跑不通就保留 **failure 证据**，
   正确性仍以独立 golden 为准。新契约与旧实现在末端淡出上**有意不同**，逐条声明为允许差异。

退出码沿用计划 §15「F退出码范围」的新增工具约定（0/2/3/4）。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import struct
import subprocess
import sys
import time
import wave
import zlib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from shared.media_contract import MERGE_FPS  # noqa: E402
from shared.subtitle_contract import (  # noqa: E402
    SubtitleCue, SubtitlePlan, SubtitleStyle, ffmpeg_subtitle_filter, load_font_spec,
    read_srt_text, write_srt,
)
from step04_merge.backends import ffmpeg as backend  # noqa: E402
from step04_merge.srt_gen import build_srt_plan  # noqa: E402
from step04_merge.timeline import (  # noqa: E402
    BoundaryTransition, ClipEntry, EndpointEffect, MergeTimeline, OverlayEntry,
    SCHEMA_MERGE_TIMELINE, TimelineAudioTrack, TimelineError, stable_shot_id,
    validate_timeline,
)

FIXTURE_SCHEMA = "merge_fixture.v1"
REPORT_SCHEMA = "merge_verify_report.v1"

EXIT_OK = 0
EXIT_INVALID_INPUT = 2
EXIT_TOOL_FAIL = 3
EXIT_GOLDEN_FAILED = 4

AAC_FRAME_SAMPLES = 1024
AUDIO_SAMPLE_RATE = 44100


class GoldenFailure(Exception):
    pass


# ------------------------------------------------------------------ 基础工具

def _run(cmd, **kw):
    return subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                          stderr=subprocess.PIPE, text=True, encoding="utf-8",
                          errors="replace", **kw)


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise SystemExit("找不到 ffmpeg")
    return exe


def _sha256(path: Path) -> str:
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blob in iter(lambda: f.read(1 << 20), b""):
            h.update(blob)
    return h.hexdigest()


def write_rgba_png(path: Path, *, width: int = 96, height: int = 54,
                   thirds: tuple = ((255, 0, 0, 255), (255, 0, 0, 128), (0, 0, 0, 0))) -> Path:
    """标准库写一张三段式 RGBA PNG（不透明 / 半透明 / 全透明）——straight alpha golden 用."""
    rows = b""
    for _y in range(height):
        row = b"\x00"
        for x in range(width):
            row += bytes(thirds[min(2, (x * 3) // width)])
        rows += row

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(rows))
           + chunk(b"IEND", b""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)
    return path


def _mk_clip(path: Path, *, dur: float, freq: int = 440, color: str = "navy",
             size: str = "192x108", fps: int = MERGE_FPS) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _run([_ffmpeg(), "-y", "-v", "error",
          "-f", "lavfi", "-i", f"color=c={color}:s={size}:d={dur}:r={fps}",
          "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}:sample_rate={AUDIO_SAMPLE_RATE}",
          "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "128k", str(path)])
    return path


def _mk_tone(path: Path, *, dur: float, freq: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _run([_ffmpeg(), "-y", "-v", "error",
          "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={dur}:sample_rate={AUDIO_SAMPLE_RATE}",
          "-c:a", "libmp3lame", "-b:a", "128k", str(path)])
    return path


def _to_pcm(media: Path, start: float, dur: float, tmp: Path, rate: int = 16000) -> Path:
    wav = tmp / f"probe_{int(start * 1000)}_{int(dur * 1000)}.wav"
    _run([_ffmpeg(), "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}",
          "-i", str(media), "-ac", "1", "-ar", str(rate), "-f", "wav", str(wav)])
    return wav


def tone_energy(media: Path, freq: float, start: float, dur: float, tmp: Path) -> float:
    """区间内某频率的**相对能量**（Goertzel；纯 stdlib，与渲染实现无关）."""
    wav = _to_pcm(media, start, dur, tmp)
    with wave.open(str(wav)) as w:
        n = w.getnframes()
        raw = w.readframes(n)
        rate = w.getframerate()
    count = len(raw) // 2
    samples = struct.unpack(f"<{count}h", raw[:count * 2])
    if not samples:
        return 0.0
    k = freq / rate
    w0 = 2.0 * math.pi * k
    cos_w, sin_w = math.cos(w0), math.sin(w0)
    coeff = 2.0 * cos_w
    s1 = s2 = 0.0
    for value in samples:
        s0 = value + coeff * s1 - s2
        s2, s1 = s1, s0
    power = s1 * s1 + s2 * s2 - coeff * s1 * s2
    return math.sqrt(max(power, 0.0)) / (len(samples) or 1)


def frame_rgb(media: Path, t_sec: float, size: tuple, tmp: Path) -> bytes:
    w, h = size
    out = tmp / f"frame_{int(t_sec * 1000)}.raw"
    _run([_ffmpeg(), "-y", "-v", "error", "-ss", f"{t_sec:.4f}", "-i", str(media),
          "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", str(out)])
    data = out.read_bytes()
    if len(data) < w * h * 3:
        raise GoldenFailure(f"取帧失败（t={t_sec}s）: {len(data)} < {w * h * 3}")
    return data


def pixel(data: bytes, size: tuple, x: int, y: int) -> tuple:
    w, _h = size
    i = (y * w + x) * 3
    return data[i], data[i + 1], data[i + 2]


def parse_srt_text(text: str) -> list:
    """独立 SRT 解析（golden 侧实现，不复用被测代码）."""
    import re
    blocks = [b for b in text.replace("\r\n", "\n").split("\n\n") if b.strip()]
    cues = []
    for block in blocks:
        lines = block.split("\n")
        m = re.match(r"(\d\d):(\d\d):(\d\d),(\d\d\d) --> (\d\d):(\d\d):(\d\d),(\d\d\d)", lines[1])
        if not m:
            raise GoldenFailure(f"SRT 时间行无法解析: {lines[:2]}")
        start = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000
        end = int(m.group(5)) * 3600 + int(m.group(6)) * 60 + int(m.group(7)) + int(m.group(8)) / 1000
        cues.append({"index": int(lines[0]), "text": "\n".join(lines[2:]).rstrip("\n"),
                     "start_sec": start, "end_sec": end})
    return cues


# ------------------------------------------------------------------ fixture 冻结

def freeze_fixture(spec: dict, root: Path) -> dict:
    """按规格准备素材（缺就走 lavfi/标准库合成）；返回解析后的素材路径表."""
    inputs_dir = root / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    clips = []
    for item in spec["clips"]:
        path = Path(item["path"]) if item.get("path") else \
            inputs_dir / f"clip_{item['shot_index']}.mp4"
        if not path.is_file():
            _mk_clip(path, dur=float(item.get("duration_sec", 2.0)),
                     freq=int(item.get("tone_hz", 440)),
                     color=str(item.get("color", "navy")),
                     size=str(item.get("size", f"{spec.get('width', 320)}x{spec.get('height', 180)}")),
                     fps=int(spec.get("fps", MERGE_FPS)))
        clips.append({**item, "path": str(path)})
    audio_tracks = []
    for item in spec.get("audio_tracks", []):
        path = Path(item["path"]) if item.get("path") else \
            inputs_dir / f"track_{item['stable_id']}.mp3"
        if not path.is_file():
            _mk_tone(path, dur=float(item.get("duration_sec", 2.0)),
                     freq=int(item.get("tone_hz", 1200)))
        audio_tracks.append({**item, "path": str(path)})
    overlays = []
    for item in spec.get("overlays", []):
        path = Path(item["path"]) if item.get("path") else \
            inputs_dir / f"{item['overlay_id']}.png"
        if item.get("kind", "image") == "image" and not path.is_file():
            write_rgba_png(path)
        overlays.append({**item, "path": str(path)})
    return {"clips": clips, "audio_tracks": audio_tracks, "overlays": overlays,
            "inputs_dir": str(inputs_dir)}


def build_timeline(spec: dict, frozen: dict, root: Path):
    """fixture → MergeTimeline（+ 授权字幕计划）；返回 (timeline, subtitle_plan, srt_path)."""
    fps = int(spec.get("fps", MERGE_FPS))
    width, height = int(spec.get("width", 320)), int(spec.get("height", 180))
    scope = str(spec.get("scope", "fixture"))
    revision = str(spec.get("script_revision", "fixture-rev"))
    clips = []
    for item in frozen["clips"]:
        kind = str(item.get("kind", "shot"))
        clips.append(ClipEntry(
            shot_index=int(item["shot_index"]), kind=kind,
            stable_id=stable_shot_id(scope, revision, kind, int(item["shot_index"])),
            path=Path(item["path"]), duration_sec=0.0,
            raw_duration_sec=float(backend.probe_media(item["path"]).duration_sec),
            frames=0, has_audio=True))
    boundaries = tuple(BoundaryTransition(int(i["index"]), str(i["kind"]),
                                          float(i.get("duration_sec", 0.0)))
                       for i in spec.get("boundary_transitions", []))
    endpoints = tuple(EndpointEffect(str(e["kind"]), float(e.get("duration_sec", 0.5)),
                                     int(e.get("clip_index", 0)))
                      for e in spec.get("endpoint_effects", []))
    tracks = []
    for item in frozen["audio_tracks"]:
        kind = str(item.get("kind", "narration"))
        tracks.append(TimelineAudioTrack(
            kind=kind, stable_id=str(item.get("stable_id", f"{kind}-{item.get('index', 1)}")),
            path=Path(item["path"]),
            clip_index=(int(item["clip_index"]) if item.get("clip_index") is not None else None),
            start_sec=(float(item["start_sec"]) if item.get("start_sec") is not None else None),
            end_sec=(float(item["end_sec"]) if item.get("end_sec") is not None else None),
            gain=float(item.get("gain", 1.0))))
    overlay_entries = []
    for item in frozen["overlays"]:
        index = int(item["shot_index"])
        clip = next(c for c in clips if c.shot_index == index)
        overlay_entries.append(OverlayEntry(
            overlay_id=str(item["overlay_id"]), stable_shot_id=clip.stable_id,
            shot_index=index, kind=str(item.get("kind", "image")), path=Path(item["path"]),
            start_sec=float(item["start_sec"]), end_sec=float(item["end_sec"]),
            layer=int(item.get("layer", 0))))

    # 字幕：fixture 里**授权**的 cue 直接进 plan（独立于渲染结果）
    subtitle_plan = None
    srt_path = root / "candidate" / "subtitles.srt"
    cues_spec = (spec.get("subtitles") or {}).get("cues") or []
    if cues_spec:
        cues = tuple(SubtitleCue(cue_id=str(c["cue_id"]), text=str(c["text"]),
                                 start_sec=float(c["start_sec"]), end_sec=float(c["end_sec"]),
                                 shot_index=c.get("shot_index")) for c in cues_spec)
        subtitle_plan = SubtitlePlan(cues=cues, source="fixture")
        write_srt(subtitle_plan, srt_path)

    timeline = MergeTimeline(
        scope=scope, script_revision=revision, width=width, height=height, fps=fps,
        clips=tuple(clips), boundary_transitions=boundaries, endpoint_effects=endpoints,
        audio_mode=str(spec.get("audio_mode", "native")), audio_tracks=tuple(tracks),
        subtitle_plan=subtitle_plan, overlays=tuple(overlay_entries),
        source={"fixture_schema": FIXTURE_SCHEMA})
    return validate_timeline(timeline), subtitle_plan, srt_path


# ------------------------------------------------------------------ golden 断言

def assert_duration_golden(result, expected_total: float, fps: int) -> dict:
    tolerance = 1.0 / fps + max(1.0 / fps, AAC_FRAME_SAMPLES / AUDIO_SAMPLE_RATE)
    delta = abs(result.duration_sec - expected_total)
    if delta > tolerance:
        raise GoldenFailure(
            f"总时长偏差 {delta:.4f}s 超过容差 {tolerance:.4f}s"
            f"（实际 {result.duration_sec:.4f}s / golden {expected_total:.4f}s）")
    return {"check": "duration", "expected": expected_total, "actual": result.duration_sec,
            "delta_sec": round(delta, 4), "tolerance_sec": round(tolerance, 4),
            "status": "pass"}


def assert_media_golden(result, spec: dict) -> dict:
    problems = []
    if (result.width, result.height) != (int(spec["width"]), int(spec["height"])):
        problems.append(f"尺寸 {result.width}x{result.height}")
    if abs(result.fps - float(spec.get("fps", MERGE_FPS))) > 0.01:
        problems.append(f"帧率 {result.fps}")
    if not result.has_audio:
        problems.append("无音轨")
    if problems:
        raise GoldenFailure("产物的实测媒体与 golden 不符: " + "、".join(problems))
    return {"check": "media", "width": result.width, "height": result.height,
            "fps": result.fps, "has_audio": result.has_audio, "status": "pass"}


def assert_audio_golden(result, spec: dict, tmp: Path) -> dict:
    """声音落点：源音在应该出现的位置能测到、在旁白落点之前测不到旁白频率."""
    tolerance = max(1.0 / int(spec.get("fps", MERGE_FPS)), AAC_FRAME_SAMPLES / AUDIO_SAMPLE_RATE)
    checks = []
    for item in spec.get("audio_golden", []):
        freq = float(item["freq_hz"])
        present = item.get("present", [])
        absent = item.get("absent", [])
        for window in present:
            t0 = float(window[0]) + tolerance
            dur = max(0.08, float(window[1]) - t0)
            energy = tone_energy(result.path, freq, t0, dur, tmp)
            baseline = tone_energy(result.path, freq, 0.0, 0.08, tmp)
            if energy < 0.25 * max(baseline, 1e-9):
                raise GoldenFailure(
                    f"频率 {freq:g}Hz 在 {t0:.2f}-{t0 + dur:.2f}s 的能量 {energy:.5f} "
                    f"低于底噪比例（baseline {baseline:.5f}）")
            checks.append({"freq_hz": freq, "window": [t0, t0 + dur], "energy": energy,
                           "mode": "present"})
        for window in absent:
            t0, t1 = float(window[0]) + tolerance, float(window[1])
            if t1 - t0 <= 0.05:
                continue
            energy = tone_energy(result.path, freq, t0, t1 - t0, tmp)
            reference = tone_energy(result.path, freq,
                                    float(item.get("reference_window", [1.0, 1.3])[0]),
                                    float(item.get("reference_window", [1.0, 1.3])[1])
                                    - float(item.get("reference_window", [1.0, 1.3])[0]), tmp)
            if reference > 0 and energy > 0.25 * reference:
                raise GoldenFailure(
                    f"频率 {freq:g}Hz 不应出现在 {t0:.2f}-{t1:.2f}s，实测能量 {energy:.5f}"
                    f"（参考段 {reference:.5f}）")
            checks.append({"freq_hz": freq, "window": [t0, t1], "energy": energy,
                           "mode": "absent"})
    return {"check": "audio_onsets", "items": checks, "tolerance_sec": round(tolerance, 4),
            "status": "pass"}


def assert_subtitle_golden(spec: dict, srt_path: Path, result, tmp: Path) -> dict:
    """字幕：盘上 SRT 与授权 cue 逐条一致（ID/text/start/end），并核指定帧的字幕墨迹."""
    authored = (spec.get("subtitles") or {}).get("cues") or []
    if not authored:
        return {"check": "subtitles", "status": "n/a", "items": []}
    parsed = parse_srt_text(read_srt_text(srt_path))
    if len(parsed) != len(authored):
        raise GoldenFailure(f"字幕条数不符: {len(parsed)} != {len(authored)}")
    for got, want in zip(parsed, authored):
        if got["text"] != want["text"] \
                or abs(got["start_sec"] - float(want["start_sec"])) > 0.001 \
                or abs(got["end_sec"] - float(want["end_sec"])) > 0.001:
            raise GoldenFailure(f"字幕 cue 与授权值不符: {got} != {want}")
    size = (result.width, result.height)
    band = int(result.height * 0.7)
    items = []
    for want in authored:
        mid = (float(want["start_sec"]) + float(want["end_sec"])) / 2.0
        data = frame_rgb(result.path, mid, size, tmp)
        ink = sum(1 for x in range(0, result.width, 2) for y in range(band, result.height, 2)
                  if sum(pixel(data, size, x, y)) > 600)
        if ink < 8:
            raise GoldenFailure(f"字幕在 {mid:.2f}s 没有出现（墨迹像素 {ink}）")
        items.append({"at_sec": mid, "ink": ink})
    # 授权窗口之外不应有字幕墨迹
    gap = (spec.get("subtitles") or {}).get("gap_sec")
    if gap is not None:
        data = frame_rgb(result.path, float(gap), size, tmp)
        ink = sum(1 for x in range(0, result.width, 2) for y in range(band, result.height, 2)
                  if sum(pixel(data, size, x, y)) > 600)
        if ink >= 8:
            raise GoldenFailure(f"字幕窗口外（{gap}s）仍有墨迹 {ink}")
        items.append({"at_sec": float(gap), "ink": ink, "expect": "blank"})
    return {"check": "subtitles", "status": "pass", "items": items}


def assert_overlay_golden(spec: dict, result, tmp: Path) -> dict:
    """overlay：窗口内出现（含 straight alpha 半透明混合），窗口外消失."""
    items = []
    cover = spec.get("overlay_golden")
    if not cover:
        return {"check": "overlay", "status": "n/a", "items": []}
    size = (result.width, result.height)
    inside = frame_rgb(result.path, float(cover["at_sec"]), size, tmp)
    x_opaque = int(result.width * 0.15)
    x_half = int(result.width * 0.5)
    x_clear = int(result.width * 0.85)
    y = int(result.height * 0.5)
    got_opaque = pixel(inside, size, x_opaque, y)
    got_half = pixel(inside, size, x_half, y)
    got_clear = pixel(inside, size, x_clear, y)
    if not (got_opaque[0] > 120 and got_opaque[1] < 90):
        raise GoldenFailure(f"不透明区没有 overlay 颜色: {got_opaque}")
    if not (got_clear[2] > 60 and got_clear[0] < 90):
        raise GoldenFailure(f"透明区没有保留底图（straight alpha）: {got_clear}")
    # straight alpha 的判别性 golden：半透明区 = α·overlay + (1-α)·底图，
    # 于是红通道 ≈ α·不透明区、蓝通道 ≈ (1-α)·透明区（α=0.5 时两者都在 0.4~0.75）。
    # 若是 premultiplied（乘了两次 α），红通道比例会掉到 ~0.25、蓝通道升到 ~0.75——
    # 落在区间外即失败。
    r_ratio = got_half[0] / max(got_opaque[0], 1)
    b_ratio = got_half[2] / max(got_clear[2], 1)
    if not (0.4 <= r_ratio <= 0.75 and 0.35 <= b_ratio <= 0.75):
        raise GoldenFailure(
            f"半透明区不是 straight-alpha 混合结果: {got_half}"
            f"（底 {cover.get('base_rgb')}，不透明 {got_opaque}，透明 {got_clear}；"
            f"r_ratio={r_ratio:.3f} b_ratio={b_ratio:.3f}）")
    items.append({"at_sec": cover["at_sec"], "opaque": got_opaque, "half": got_half,
                  "clear": got_clear, "r_ratio": round(r_ratio, 3),
                  "b_ratio": round(b_ratio, 3)})
    outside = frame_rgb(result.path, float(cover["outside_sec"]), size, tmp)
    got_out = pixel(outside, size, x_opaque, y)
    if got_out[0] > 120 and got_out[1] < 90:
        raise GoldenFailure(f"overlay 窗口外仍然出现: {got_out}")
    items.append({"at_sec": cover["outside_sec"], "opaque": got_out, "expect": "absent"})
    if cover.get("after_sequence_sec") is not None and cover.get("sequence_checked"):
        after = frame_rgb(result.path, float(cover["after_sequence_sec"]), size, tmp)
        got_after = pixel(after, size, x_opaque, y)
        if got_after[0] > 120 and got_after[1] < 90:
            raise GoldenFailure(f"PNG 序列结束后重复了最后一帧: {got_after}")
        items.append({"at_sec": cover["after_sequence_sec"], "opaque": got_after,
                      "expect": "absent_after_sequence_end"})
    return {"check": "overlay", "status": "pass", "items": items}


# ------------------------------------------------------------------ 旧实现对照

def legacy_layout(chapter: int, book_name: str, output_base: Path) -> Path:
    """隔离 OUTPUT_BASE 下旧入口要求的章目录（问 shared.paths，不自己拼层级）."""
    env = dict(os.environ)
    env["OUTPUT_BASE"] = str(output_base)
    env["NOVEL_CHAPTER"] = str(chapter)
    env["BOOK_NAME"] = book_name
    env["BOOK_INDEX"] = "1"
    # 子进程 stdout 必须是 UTF-8，否则中文目录名按 GBK 编码、这里按 UTF-8 解出乱码路径
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-c",
         "from shared.paths import chapter_dir; print(chapter_dir())"],
        cwd=str(_REPO_ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"取隔离章目录失败: {proc.stderr[-300:]}")
    return Path(proc.stdout.strip().splitlines()[-1])


def run_legacy_entry(spec: dict, frozen: dict, out_dir: Path, *, chapter: int = 1,
                     timeout: int = 600) -> dict:
    """在隔离 OUTPUT_BASE 里跑**旧入口**（子进程），保存 rc/命令/日志/产物。

    素材布局按旧入口的真实要求造：``CLIP_SOURCE=vimax`` 时 clip 在
    ``<chapter>/vimax_working/shots/<idx>/video.mp4``，hook 恒在 ``<chapter>/clips/clip_0.mp4``。
    旧入口要过 step01/step02b 的 gate——fixture 没有合法 gate 时**保留 failure 证据**
    （§S3：不强求产出旧样片、不造伪旧样片）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    chapter_dir = legacy_layout(chapter, "fixture", out_dir / "output_base")
    (chapter_dir / "vimax_working" / "shots").mkdir(parents=True, exist_ok=True)
    (chapter_dir / "clips").mkdir(parents=True, exist_ok=True)
    # hook（旧入口恒走 clips/clip_0.mp4，要求 >20KB 且含音轨）
    hook_src = next((c for c in frozen["clips"] if c.get("kind") == "hook"), None)
    if hook_src is not None:
        shutil.copyfile(hook_src["path"], chapter_dir / "clips" / "clip_0.mp4")
    for item in frozen["clips"]:
        if item.get("kind") == "hook":
            continue
        shot_dir = chapter_dir / "vimax_working" / "shots" / str(int(item["shot_index"]))
        shot_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item["path"], shot_dir / "video.mp4")
    script = {"chapter_index": chapter, "revision": spec.get("script_revision", "fixture-rev"),
              "characters": [], "chapter_outline": {"beats": []},
              "hook": ({"index": 0} if hook_src is not None else None),
              "shots": [{"index": int(item["shot_index"]), "clip_transition": "硬切",
                         "subtitle": "", "narration_segment": ""}
                        for item in frozen["clips"] if item.get("kind") != "hook"]}
    (chapter_dir / "script_result.json").write_text(
        json.dumps(script, ensure_ascii=False, indent=1), encoding="utf-8")

    env = dict(os.environ)
    env["OUTPUT_BASE"] = str(out_dir / "output_base")
    env["NOVEL_CHAPTER"] = str(chapter)
    env["BOOK_NAME"] = "fixture"
    env["BOOK_INDEX"] = "1"
    env["CLIP_SOURCE"] = "vimax"
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [sys.executable, str(_REPO_ROOT / "step04_merge" / "main.py")]
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=str(_REPO_ROOT), env=env, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          check=False)
    (out_dir / "legacy_stdout.log").write_text(proc.stdout or "", encoding="utf-8")
    (out_dir / "legacy_stderr.log").write_text(proc.stderr or "", encoding="utf-8")
    produced = chapter_dir / "final.mp4"
    return {
        "entry": "step04_merge/main.py",
        "command": cmd,
        "returncode": proc.returncode,
        "elapsed_sec": round(time.monotonic() - started, 2),
        "evidence_dir": str(out_dir),
        "produced_final": str(produced) if produced.exists() else None,
        "produced_sha256": _sha256(produced) if produced.exists() else None,
        "status": "ok" if proc.returncode == 0 else "failure_evidence",
        # 新契约与旧实现的**有意差异**（§15「转场表示」，golden 为准）：
        # 旧末端 fade_out 同时被当边界（回退成 xfade=fade，重叠 tdur 秒）再叠端点；
        # 新契约末端点不减总长、该边界为 cut ⇒ 总长相差一个 tdur，且少一条 xfade。
        "allowed_differences": [
            {"item": "endpoint_fade_out", "difference": "总长多出原 tdur（旧实现少一次 xfade）",
             "reason": "§15 转场表示：端点效果不减总长、该边界为 cut",
             "golden": "merge_fixture 的 duration golden 为准"},
            {"item": "filter_string", "difference": "filter 字符串不求逐字相同",
             "reason": "§S3：旧 filter 字符串测试改为行为断言"},
        ],
    }


# ------------------------------------------------------------------ 主流程

SAMPLE_FIXTURE = {
    "schema_version": FIXTURE_SCHEMA, "scope": "fixture", "script_revision": "fixture-rev",
    "fps": MERGE_FPS, "width": 320, "height": 180,
    "clips": [{"shot_index": 1, "kind": "shot", "duration_sec": 2.0, "tone_hz": 440},
              {"shot_index": 2, "kind": "shot", "duration_sec": 3.0, "tone_hz": 660}],
    "boundary_transitions": [{"index": 1, "kind": "fade", "duration_sec": 0.5}],
    "endpoint_effects": [{"kind": "fade_in", "duration_sec": 0.5, "clip_index": 0},
                         {"kind": "fade_out", "duration_sec": 0.5, "clip_index": 1}],
    "audio_mode": "native",
    "audio_tracks": [],
    "overlays": [],
    "subtitles": {"cues": [], "gap_sec": None},
    "audio_golden": [],
    "overlay_golden": None,
}


def verify(spec: dict, out_dir: Path, *, run_legacy: bool = True) -> dict:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    frozen = freeze_fixture(spec, out_dir)
    timeline, subtitle_plan, srt_path = build_timeline(spec, frozen, out_dir)

    font = None
    if (spec.get("font") or {}).get("path"):
        font = load_font_spec(spec["font"]["path"], (spec["font"] or {}).get("sha256"))
    settings = backend.EncodeSettings(
        width=timeline.width, height=timeline.height, crf=str(spec.get("crf", "20")),
        preset=str(spec.get("preset", "veryfast")), loudnorm=False,
        subtitle_style=SubtitleStyle(font=font, font_size=int(spec.get("font_size", 22))),
        require_explicit_font=bool(spec.get("require_explicit_font", bool(font))))
    subtitle_filter = ""
    if subtitle_plan is not None and subtitle_plan.cues:
        subtitle_filter = ffmpeg_subtitle_filter(srt_path, subtitle_plan, settings.subtitle_style)

    candidate = out_dir / "candidate" / "candidate.mp4"
    checks = []
    result = backend.render(timeline, candidate, out_dir / "work", settings=settings,
                            subtitle_filter=subtitle_filter, srt_path=srt_path,
                            evidence_dir=candidate.parent)
    expected_total = timeline.total_duration_sec
    checks.append(assert_duration_golden(result, expected_total, timeline.fps))
    checks.append(assert_media_golden(result, spec))
    checks.append(assert_audio_golden(result, spec, tmp))
    checks.append(assert_subtitle_golden(spec, srt_path, result, tmp))
    checks.append(assert_overlay_golden(spec, result, tmp))

    report = {
        "schema_version": REPORT_SCHEMA,
        "scope": timeline.scope, "script_revision": timeline.script_revision,
        "fixture_schema": spec.get("schema_version"),
        "timeline": {"schema_version": SCHEMA_MERGE_TIMELINE,
                     "clips": len(timeline.clips),
                     "windows": [(w.start_sec, w.end_sec) for w in timeline.windows],
                     "total_duration_sec": timeline.total_duration_sec,
                     "boundary_transitions": [{"index": b.index, "kind": b.kind,
                                               "duration_sec": b.duration_sec}
                                              for b in timeline.boundary_transitions],
                     "endpoint_effects": [{"kind": e.kind, "duration_sec": e.duration_sec,
                                           "clip_index": e.clip_index}
                                          for e in timeline.endpoint_effects]},
        "decode_evidence": str(candidate.parent / "media_decode_evidence.json")
        if (candidate.parent / "media_decode_evidence.json").is_file() else None,
        "candidate": {"path": str(result.path), "sha256": result.sha256,
                      "bytes": result.bytes, "duration_sec": result.duration_sec,
                      "width": result.width, "height": result.height, "fps": result.fps,
                      "has_audio": result.has_audio, "ffmpeg_version": result.ffmpeg_version,
                      "elapsed_sec": result.elapsed_sec, "stages": list(result.stages),
                      "command": list(result.command)},
        "detector_parameters": {"black": {"min_duration_sec": max(
            1.0 / max(int(timeline.fps), 1),
            round(min([float(e.duration_sec) for e in timeline.endpoint_effects],
                      default=0.4) / 2.0, 3))}},
        "checks": checks,
        "verdict": "pass",
    }
    if run_legacy:
        report["legacy_entry"] = run_legacy_entry(spec, frozen, out_dir / "legacy")
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                         encoding="utf-8")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="验证 Step04 合并时间线（冻结输入 + 独立 golden）")
    parser.add_argument("--fixture", type=Path, default=None, metavar="FILE",
                        help="merge_fixture.v1 规格（缺素材时按规格本地合成）")
    parser.add_argument("--out-dir", type=Path, default=None, metavar="DIR")
    parser.add_argument("--emit-sample-fixture", type=Path, default=None, metavar="FILE",
                        help="写一份样例 fixture 规格后退出")
    parser.add_argument("--skip-legacy-entry", action="store_true",
                        help="不跑旧入口对照（默认在隔离 OUTPUT_BASE 子进程里跑并留证）")
    args = parser.parse_args(argv)
    if args.emit_sample_fixture is not None:
        args.emit_sample_fixture.parent.mkdir(parents=True, exist_ok=True)
        args.emit_sample_fixture.write_text(json.dumps(SAMPLE_FIXTURE, ensure_ascii=False,
                                                       indent=1), encoding="utf-8")
        print(f"[OK] 样例 fixture: {args.emit_sample_fixture}")
        return EXIT_OK
    if args.fixture is None or args.out_dir is None:
        parser.error("--fixture 与 --out-dir 必须一起给出")
    try:
        spec = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[ERROR] fixture 解析失败: {exc}")
        return EXIT_INVALID_INPUT
    if spec.get("schema_version") != FIXTURE_SCHEMA:
        print(f"[ERROR] 未知 fixture schema: {spec.get('schema_version')!r}"
              f"（期望 {FIXTURE_SCHEMA}）")
        return EXIT_INVALID_INPUT
    try:
        report = verify(spec, args.out_dir, run_legacy=not args.skip_legacy_entry)
    except GoldenFailure as exc:
        print(f"[FAIL] golden 不符: {exc}")
        return EXIT_GOLDEN_FAILED
    except TimelineError as exc:
        print(f"[FAIL] 时间线拒收（{exc.code}）: {exc}")
        return EXIT_INVALID_INPUT
    except backend.RenderError as exc:
        print(f"[FAIL] 渲染失败（phase={exc.stage}）: {exc}")
        return EXIT_TOOL_FAIL
    except Exception as exc:  # 不吞错：如实报失败
        print(f"[ERROR] {type(exc).__name__}: {exc}")
        return EXIT_TOOL_FAIL
    print(f"[OK] 合并时间线验收通过: {report['candidate']['path']}"
          f"（{report['candidate']['duration_sec']:.3f}s, "
          f"golden {report['timeline']['total_duration_sec']:.3f}s）")
    legacy = report.get("legacy_entry") or {}
    if legacy:
        print(f"[INFO] 旧入口对照: rc={legacy['returncode']} status={legacy['status']}"
              f"（证据: {legacy['evidence_dir']}；允许差异已在报告里逐条声明）")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
