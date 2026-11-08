# -*- coding: utf-8 -*-
"""WP4：Step 02b 的**零调用介质演练**——按正式稿时长生成 13 段桩视频.

外部依赖声明：本用例读取当前正式章节的 script_result.json（测试素材，不是
产品前提）。文件不存在时明确 skip，并说明缺哪份输入；不自动找"最新产物"。
生成的全部内容落在 tmp_path，不写生产目录。
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

WIDTH, HEIGHT = 1440, 2560


def _formal_script() -> dict:
    """正式稿路径：WP4_FORMAL_SCRIPT 优先，否则取未重定向的默认输出根下的唯一章节.

    测试运行期 OUTPUT_BASE 被指向 .pytest_tmp，这里显式取默认根；不按 mtime
    挑"最新"，匹配到多份就报错（说明输入不唯一）。
    """
    env = (os.getenv("WP4_FORMAL_SCRIPT") or "").strip()
    if env:
        p = Path(env)
        if not p.is_file():
            pytest.skip("WP4_FORMAL_SCRIPT 指向的文件不存在：%s" % p)
        return json.loads(p.read_text(encoding="utf-8"))

    # 正式树在仓库根的 output/（output/video 已于 11-01 退役，别按 paths 默认根取）
    root = _ROOT / "output"
    hits = sorted(root.glob("*/part_*/chapter_*/script_result.json"))
    if len(hits) != 1:
        pytest.skip("正式稿输入不唯一或缺失：%s 下命中 %d 份" % (root, len(hits)))
    return json.loads(hits[0].read_text(encoding="utf-8"))


def _duration(path: Path) -> float:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True)
    return float(out.stdout.strip())


def _make_clip(path: Path, seconds: float, color: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=%s:s=%dx%d:r=24:d=%.3f"
         % (color, WIDTH, HEIGHT, seconds),
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-shortest", "-pix_fmt", "yuv420p", "-c:v", "libx264",
         "-preset", "ultrafast", "-c:a", "aac", str(path)],
        check=True)


def test_thirteen_stub_clips_match_formal_durations(tmp_path):
    script = _formal_script()
    shots = [s for s in script.get("shots") or [] if isinstance(s, dict)]
    assert len(shots) == 13, "正式稿应为 13 镜，实际 %d" % len(shots)

    expected = []
    for shot in shots:
        idx = int(shot["index"])
        dur = float(shot["duration_sec"])
        expected.append((idx, dur))
        target = tmp_path / "vimax_working" / "shots" / str(idx) / "video.mp4"
        _make_clip(target, dur, "0x%06x" % ((idx * 40) % 0xFFFFFF + 0x101010))

    total = 0.0
    for idx, dur in expected:
        clip = tmp_path / "vimax_working" / "shots" / str(idx) / "video.mp4"
        assert clip.is_file(), "缺 %d 号片段" % idx
        got = _duration(clip)
        assert abs(got - dur) < 0.06, "shot %d 时长 %.3f ≠ %.3f" % (idx, got, dur)
        total += got

    assert abs(total - 75.25) < 0.2, "总时长 %.2f ≠ 75.25" % total
