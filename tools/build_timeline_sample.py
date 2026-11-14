# -*- coding: utf-8 -*-
"""用本地 ffmpeg 造有效离线媒体，构建/验收/渲染一条可播放时间轴样片（S06）.

零模型、零网络：片段、旁白与字幕都是本机确定性素材。产物写到调用方显式给出的
目录，默认 ``.pytest_tmp/s06``。运行：

    python tools/build_timeline_sample.py --out .pytest_tmp/s06

输出的 ``timeline_acceptance.json`` 同时记录合成前验收与合成后复核读数。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step04_merge.timeline import (
    BoundaryTransition,
    ClipEntry,
    MergeTimeline,
    TimelineAudioTrack,
    build_timeline_acceptance,
    normalize_duration,
    stable_shot_id,
    validate_timeline,
    verify_rendered_output,
)
from shared.media_contract import BOUNDARY_CUT
from shared.subtitle_contract import SubtitleCue, SubtitlePlan


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("本机没有 ffmpeg，无法造离线媒体样片")
    return exe


def _run(args: list) -> None:
    proc = subprocess.run(args, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg 失败: %s"
                           % proc.stderr.decode("utf-8", "replace")[-2000:])


def make_clip(path: Path, seconds: float, color: str, *, with_audio: bool = True,
              fps: int = 24, size: str = "320x240") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    args = [_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i",
            f"color=c={color}:s={size}:r={fps}:d={seconds:.3f}"]
    if with_audio:
        args += ["-f", "lavfi", "-i",
                 f"sine=frequency=330:sample_rate=16000:duration={seconds:.3f}",
                 "-c:a", "aac", "-shortest"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    _run(args)
    return path


def make_wav(path: Path, seconds: float, *, freq: int = 440) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    _run([_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i",
          f"sine=frequency={freq}:sample_rate=16000:duration={seconds:.3f}",
          "-c:a", "pcm_s16le", str(path)])
    return path


def probe_duration(path: Path) -> float:
    from shared.media_inspect import probe_media
    info = probe_media(str(path))
    duration = info.get("duration_sec")
    if duration is None:
        duration = (info.get("format") or {}).get("duration")
    return float(duration)


def build_sample_timeline(root: Path) -> tuple[MergeTimeline, dict]:
    scope = "demo:chapter_1"
    revision = "s06-demo-revision"
    hook = make_clip(root / "clips" / "hook.mp4", 2.0, "navy")
    shot1 = make_clip(root / "clips" / "shot_1.mp4", 3.0, "darkgreen")
    shot2 = make_clip(root / "clips" / "shot_2.mp4", 2.0, "maroon")
    narration = make_wav(root / "audio" / "narration.wav", 1.5)
    tts = make_wav(root / "audio" / "shot_1_tts.wav", 2.0, freq=520)

    clips = []
    for kind, index, path in (("hook", 0, hook), ("shot", 1, shot1),
                              ("shot", 2, shot2)):
        raw = probe_duration(path)
        frames, normalized = normalize_duration(raw)
        clips.append(ClipEntry(
            shot_index=index, kind=kind, path=path,
            stable_id=stable_shot_id(scope, revision, kind, index),
            duration_sec=normalized, raw_duration_sec=raw, frames=frames,
            has_audio=True, source="offline_sample"))
    timeline = MergeTimeline(
        scope=scope, script_revision=revision, width=320, height=240, fps=24,
        clips=tuple(clips),
        boundary_transitions=(
            BoundaryTransition(1, "fade", 0.4),
            BoundaryTransition(2, "fade", 0.4)),
        audio_mode="tts_mix",
        audio_tracks=(
            TimelineAudioTrack(kind="shot_tts", stable_id="shot-1:tts",
                               path=tts, clip_index=1, sha256=None,
                               source="offline_sample"),
            TimelineAudioTrack(kind="narration", stable_id="N1:narration",
                               path=narration, start_sec=3.0, end_sec=4.5,
                               source="offline_sample"),
        ),
        subtitle_plan=SubtitlePlan(cues=(
            SubtitleCue(cue_id="c1", text="离线样片字幕", start_sec=1.0,
                        end_sec=2.0, shot_index=1),
        ), source="offline_sample"),
        source={"kind": "offline_sample", "scope": scope, "revision": revision},
    )
    timeline = validate_timeline(timeline)
    acceptance = build_timeline_acceptance(
        timeline, expected_body_sec=5.0, expected_hook_sec=2.0)
    timeline = MergeTimeline(**{**timeline.__dict__, "acceptance": acceptance})
    return timeline, acceptance


def render_timeline(timeline: MergeTimeline, out_path: Path) -> Path:
    """按 fade 边界 + 旁白/对白混音渲染；不做模型调用。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    clips = list(timeline.clips)
    by_index = {int(b.index): b for b in timeline.boundary_transitions}
    args = [_ffmpeg(), "-y", "-v", "error"]
    for clip in clips:
        args += ["-i", str(clip.path)]
    audio_inputs = []
    for track in timeline.audio_tracks:
        args += ["-i", str(track.path)]
        audio_inputs.append(len(clips) + len(audio_inputs))
    filters = []
    for i in range(len(clips)):
        filters.append(f"[{i}:v]settb=AVTB,setpts=PTS-STARTPTS[v{i}]")
    last = "v0"
    offset = 0.0
    for i in range(1, len(clips)):
        boundary = by_index.get(i)
        tdur = float(boundary.duration_sec) if boundary else 0.0
        offset += float(clips[i - 1].duration_sec) - tdur
        kind = boundary.kind if boundary else BOUNDARY_CUT
        if tdur > 0 and kind != BOUNDARY_CUT:
            out = f"vx{i}"
            filters.append(
                f"[{last}][v{i}]xfade=transition=fade:duration={tdur:.3f}:"
                f"offset={offset:.3f}[{out}]")
            last = out
        else:
            out = f"vx{i}"
            filters.append(f"[{last}][v{i}]concat=n=2:v=1:a=0[{out}]")
            last = out
    # 音频：片段测试音 + 逐镜 TTS + 旁白，统一 delay 后混合。
    audio_labels = []
    for i, clip in enumerate(clips):
        filters.append(f"[{i}:a]adelay=0|0[a{i}]")
        audio_labels.append(f"[a{i}]")
    for track, input_idx in zip(timeline.audio_tracks, audio_inputs):
        if track.kind == "shot_tts" and track.clip_index is not None:
            start = timeline.windows[int(track.clip_index)].start_sec
        else:
            start = float(track.start_sec or 0.0)
        delay_ms = int(round(start * 1000))
        label = f"ax{input_idx}"
        filters.append(f"[{input_idx}:a]adelay={delay_ms}|{delay_ms}[{label}]")
        audio_labels.append(f"[{label}]")
    filters.append("".join(audio_labels) + f"amix=inputs={len(audio_labels)}:"
                   "duration=longest:normalize=0,apad[aout]")
    graph = ";".join(filters)
    args += ["-filter_complex", graph, "-map", f"[{last}]", "-map", "[aout]",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast",
             "-c:a", "aac", "-t", f"{timeline.total_duration_sec:.3f}", str(out_path)]
    _run(args)
    return out_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="离线时间轴样片")
    parser.add_argument("--out", default=".pytest_tmp/s06")
    args = parser.parse_args(argv)
    root = Path(args.out)
    timeline, acceptance = build_sample_timeline(root)
    rendered = render_timeline(timeline, root / "sample.mp4")
    rendered_check = verify_rendered_output(rendered, timeline, acceptance)
    report = {
        "schema": "s06_sample.v1",
        "timeline": timeline.to_dict(),
        "acceptance": acceptance,
        "rendered": rendered_check,
        "sample_path": str(rendered.resolve()),
    }
    target = root / "timeline_acceptance.json"
    from shared.json_io import atomic_dump_json
    atomic_dump_json(target, report, ensure_ascii=False, indent=2)
    print(json.dumps({"sample": str(rendered), "acceptance_status": acceptance["status"],
                      "rendered_status": rendered_check["status"],
                      "total_sec": acceptance["duration"]["total_sec"],
                      "rendered_sec": rendered_check["actual_sec"],
                      "report": str(target)}, ensure_ascii=False))
    return 0 if acceptance["status"] == "pass" and rendered_check["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
