# -*- coding: utf-8 -*-
"""M8.2 本地帧差预筛：纯哈希分析 + FFmpeg 帧采样（无视觉模型）."""
from __future__ import annotations

import shutil
import subprocess

import pytest

from shared.media_precheck import (analyze_hashes, available, duration_check,
                                   precheck_video)


def _h(*values):
    return ["%064x" % v for v in values]


def test_static_video_all_hashes_equal():
    result = analyze_hashes(_h(1, 1, 1, 1))
    assert result["code"] == "static"
    assert result["frozen_ratio"] == 1.0
    assert result["warnings"][0]["code"] == "static_video"


def test_single_flash_frame_is_detected():
    result = analyze_hashes(_h(1, 1, 9, 1, 1))
    assert result["code"] == "flash"
    assert result["flash_frame_indices"] == [2]
    assert any(w["code"] == "flash_frame" for w in result["warnings"])


def test_moving_video_is_ok():
    result = analyze_hashes(_h(1, 2, 3, 4, 5))
    assert result["code"] == "ok"
    assert result["warnings"] == []
    assert result["verdict"] == "ok"


def test_no_frames_is_blocked():
    result = analyze_hashes([])
    assert result["verdict"] == "blocked"
    assert result["code"] == "no_frames"


def test_duration_deviation_within_and_over_tolerance():
    ok = duration_check(4.2, 4.0)
    assert ok["checked"] is True and ok["verdict"] == "ok"

    bad = duration_check(6.0, 4.0)
    assert bad["verdict"] == "warning"
    assert bad["code"] == "duration_deviation"
    assert bad["deviation_sec"] == 2.0


def test_duration_unknown_is_not_faked():
    result = duration_check(None, 4.0)
    assert result["checked"] is False
    assert result["code"] == "duration_unknown"


@pytest.mark.skipif(not available(), reason="ffmpeg 不可用")
def test_ffmpeg_sampling_detects_static_clip(tmp_path):
    clip = tmp_path / "static.mp4"
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
           "-i", "color=c=black:s=64x64:d=2:r=8", "-pix_fmt", "yuv420p", str(clip)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr

    result = precheck_video(clip, expected_duration_sec=2.0, fps=4.0)

    assert result["verdict"] == "ok"
    assert result["network_calls"] == 0
    assert result["vision_model_calls"] == 0
    assert result["frames"]["frame_count"] >= 2
    assert result["frames"]["code"] == "static"
    assert result["duration"]["checked"] is True
