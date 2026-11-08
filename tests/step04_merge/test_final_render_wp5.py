# -*- coding: utf-8 -*-
"""WP5：13 段桩片的**真实合并**（ffmpeg 真合成，零模型调用）.

走生产入口：step04_merge.timeline.MergeTimeline -> backends.ffmpeg.render
（内部 validate -> compile_graph -> ffmpeg），产出 1440x2560、约 75.25s 的 mp4。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(FFMPEG is None or FFPROBE is None,
                                reason="需要 ffmpeg/ffprobe")

WIDTH, HEIGHT, FPS = 1440, 2560, 24


def _formal_script() -> dict:
    env = (os.getenv("WP4_FORMAL_SCRIPT") or "").strip()
    if env:
        p = Path(env)
        if not p.is_file():
            pytest.skip("WP4_FORMAL_SCRIPT 指向的文件不存在: %s" % p)
        return json.loads(p.read_text(encoding="utf-8"))
    hits = sorted((_ROOT / "output").glob("*/part_*/chapter_*/script_result.json"))
    if len(hits) != 1:
        pytest.skip("正式稿输入不唯一或缺失: 命中 %d 份" % len(hits))
    return json.loads(hits[0].read_text(encoding="utf-8"))


def _probe(path: Path) -> dict:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _clip(path: Path, frames: int, color: str) -> None:
    """生成**精确帧数**的片段：时间线按帧量化，片段多一帧就会让成片偏长."""
    seconds = frames / FPS
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=%s:s=%dx%d:r=%d:d=%.4f"
         % (color, WIDTH, HEIGHT, FPS, seconds),
         "-frames:v", str(frames), "-t", "%.4f" % seconds,
         "-pix_fmt", "yuv420p", "-c:v", "libx264",
         "-preset", "ultrafast", "-c:a", "aac", str(path)],
        check=True)

def test_thirteen_clips_render_to_final_mp4(tmp_path):
    from step04_merge.backends.ffmpeg import render
    from step04_merge.timeline import ClipEntry, MergeTimeline

    script = _formal_script()
    shots = [s for s in script.get("shots") or [] if isinstance(s, dict)]
    assert len(shots) == 13

    # 逐镜取整会产生累计误差（13 镜可差 3-4 帧，渲染端直接判时长偏离）；
    # 用最大余数法把总帧数钉到 round(总时长*fps)，误差分摊到余数最大的镜。
    durs = [float(s["duration_sec"]) for s in shots]
    target_frames = int(round(sum(durs) * FPS))
    base = [int(d * FPS) for d in durs]
    rem = sorted(range(len(durs)), key=lambda i: (durs[i] * FPS) - base[i],
                 reverse=True)
    frames_per_shot = list(base)
    for i in rem[:max(0, target_frames - sum(base))]:
        frames_per_shot[i] += 1

    clips = []
    for n, shot in enumerate(shots):
        idx = int(shot["index"])
        dur = float(shot["duration_sec"])
        path = tmp_path / "clips" / ("clip_%d.mp4" % idx)
        frames = frames_per_shot[n]
        _clip(path, frames, "0x%06x" % (((n * 37) % 200) + 20 << 8))
        real = float(_probe(path)["format"]["duration"])
        # 生产口径：时长按帧量化（duration_sec = frames/fps），不是原始小数秒；
        # 否则渲染端会判「实际时长偏离时间线」。
        clips.append(ClipEntry(shot_index=idx, kind="shot",
                               stable_id="shot-%d" % idx, path=path,
                               duration_sec=frames / FPS, raw_duration_sec=real,
                               frames=frames, has_audio=False))

    timeline = MergeTimeline(
        scope="chapter_001",
        script_revision=str(script.get("script_revision") or "rev"),
        width=WIDTH, height=HEIGHT, fps=FPS,
        clips=tuple(clips),
        boundary_transitions=(),   # 13 段全部硬切（无转场）
    )
    # 13 镜 75.25s 经 24fps 量化后为 75.29s（逐镜取整累计）
    assert abs(timeline.total_duration_sec - 75.25) < 0.15

    out = tmp_path / "final.mp4"
    render(timeline, out, tmp_path / "work")

    assert out.is_file() and out.stat().st_size > 0
    info = _probe(out)
    streams = {s["codec_type"] for s in info["streams"]}
    assert {"video", "audio"} <= streams, streams
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (int(video["width"]), int(video["height"])) == (WIDTH, HEIGHT)
    dur = float(info["format"]["duration"])
    assert abs(dur - timeline.total_duration_sec) < 0.2, "成片时长 %.2f" % dur
    assert abs(dur - 75.25) < 0.2