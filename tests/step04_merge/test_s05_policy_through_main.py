# -*- coding: utf-8 -*-
"""S05/M1 策略必须经 **Step04 主入口** 贯通（不能只测纯函数/后端）.

2026-11-27 复核 P1：补齐曾被重复计长（2s 视频 + 2.8s 音频被拉成 3.58s），
加速则被验收按原始时长误判 `audio_window_overflow`。本文件直接从 CLI 入口跑：

- `MERGE_AUDIO_OVERFLOW_POLICY=pad_video_tail`：成片 = 原视频 + 超窗时长（一次）；
- `MERGE_AUDIO_OVERFLOW_POLICY=gentle_atempo`：验收按 atempo 后的**有效**时长比对，
  成片时长保持镜头时长，音轨被加速但完整。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.step04_merge.test_step04_shots_subset_e2e import _prepare, _run

REPO = Path(__file__).resolve().parents[2]
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(FFMPEG is None or FFPROBE is None,
                                reason="需要本机 ffmpeg/ffprobe")


def _mp3(path: Path, seconds: float, freq: int = 520) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=%d:duration=%.3f:sample_rate=24000"
                    % (freq, seconds), "-ac", "1", "-c:a", "libmp3lame", "-b:a", "96k",
                    str(path)], check=True)
    return path


def _register_shot_audio(ch: Path, revision: str, seconds: float) -> Path:
    """把 shot 1 的对白音轨写进声音状态（否则正式合成门禁按缺轨拒收）."""
    from shared import voice_state as vs

    audio = _mp3(ch / "audio" / "shot_1.mp3", seconds)
    state_path = ch / vs.FILENAME
    state = json.loads(state_path.read_text(encoding="utf-8"))
    tracks = [{"unit_id": u.get("unit_id"), "kind": u.get("kind") or "tts_dialogue",
               "shot_index": u.get("shot_index"), "path": u.get("path")}
              for u in (state.get("units") or []) if u.get("path")]
    tracks.append({"unit_id": "shot-1", "kind": "tts_dialogue",
                   "shot_index": 1, "path": str(audio)})
    report = vs.build_voice_state({"script_revision": revision}, tracks=tracks,
                                 script_revision=revision, source="test_stub")
    assert report["state"] == "confirmed", report.get("blocking")
    state_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    return audio


def _duration(path: Path) -> float:
    proc = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                           "format=duration", "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    return float((proc.stdout or "0").strip() or 0)


def test_pad_policy_through_main_counts_pad_once(tmp_path):
    ch, env = _prepare(tmp_path)
    revision = json.loads((ch / "script_result.json").read_text(
        encoding="utf-8")).get("script_revision") or ""
    _register_shot_audio(ch, revision, 2.8)
    env["MERGE_AUDIO_OVERFLOW_POLICY"] = "pad_video_tail"
    proc = _run([sys.executable, str(REPO / "step04_merge" / "main.py"),
                 "--shots", "1"], env=env)
    assert proc.returncode == 0, (proc.stdout[-2000:], proc.stderr[-2000:])
    final = ch / "final_shots_1.mp4"
    quality = json.loads((ch / "step04_shots_1_quality.json").read_text(encoding="utf-8"))
    assert quality["audio_fit"]["policy"] == "pad_video_tail"
    assert len(quality["audio_fit"]["pads"]) == 1
    # 2.0s 视频 + 0.8s 补齐 ⇒ ≈2.8s；重复计长会得到 ≈3.58s
    duration = _duration(final)
    assert duration == pytest.approx(2.8, abs=0.15), duration
    assert quality["outputs"]["final"]["duration_sec"] == pytest.approx(duration, abs=0.2)
    # 人声完整保留：音频流不短于源音轨
    assert _duration(final) >= 2.7


def test_atempo_policy_through_main_passes_acceptance(tmp_path):
    ch, env = _prepare(tmp_path)
    revision = json.loads((ch / "script_result.json").read_text(
        encoding="utf-8")).get("script_revision") or ""
    _register_shot_audio(ch, revision, 2.2)          # 2.2/2.0 = 1.1x ≤ 1.12 上限
    env["MERGE_AUDIO_OVERFLOW_POLICY"] = "gentle_atempo"
    proc = _run([sys.executable, str(REPO / "step04_merge" / "main.py"),
                 "--shots", "1"], env=env)
    assert proc.returncode == 0, (proc.stdout[-2500:], proc.stderr[-2500:])
    final = ch / "final_shots_1.mp4"
    quality = json.loads((ch / "step04_shots_1_quality.json").read_text(encoding="utf-8"))
    assert quality["audio_fit"]["policy"] == "gentle_atempo"
    assert quality["audio_fit"]["atempo"], quality["audio_fit"]
    # 成片仍是 2.0s（加速吸收超窗），不是被拉长或失败
    assert _duration(final) == pytest.approx(2.0, abs=0.15)
