# -*- coding: utf-8 -*-
"""WP5：真实 step04_merge.main 出片（章节副本，零模型调用）."""
from __future__ import annotations

import hashlib
import json
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


def _formal_chapter() -> Path:
    hits = sorted((_ROOT / "output").glob("*/part_*/chapter_*/script_result.json"))
    if len(hits) != 1:
        pytest.skip("正式章节不唯一或缺失: %d" % len(hits))
    return hits[0].parent


def _block_network(monkeypatch):
    """隔离进程内**阻断**任何模型网络调用（含导入期与缓存缺失路径）."""
    import requests

    def _boom(*a, **k):
        raise AssertionError("隔离用例中出现了模型网络调用: %r %r" % (a[:2], k))

    monkeypatch.setattr(requests, "post", _boom)
    monkeypatch.setattr(requests, "get", _boom)
    monkeypatch.setattr(requests.Session, "request", _boom)


def _clip(path: Path, frames: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-v", "error",
         "-f", "lavfi", "-i", "color=c=gray:s=%dx%d:r=%d:d=%.4f"
         % (WIDTH, HEIGHT, FPS, frames / FPS),
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
         "-vf", "noise=alls=40:allf=t", "-frames:v", str(frames), "-shortest",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", str(path)],
        check=True)


def _tts(path: Path, seconds: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-v", "error", "-f", "lavfi",
         "-i", "sine=frequency=440:sample_rate=24000:d=%.3f" % seconds,
         "-c:a", "libmp3lame", "-b:a", "64k", str(path)],
        check=True)


def _identity(path: Path, text: str, revision: str) -> None:
    from shared.audio_identity import identity_path, sha256_text, sha256_file
    payload = {
        "schema_version": "audio_identity.v2",
        "unit_id": "shot-%s" % path.stem,
        "scope": {"chapter": 1},
        "script_revision": revision,
        "text": text,
        "text_hash": sha256_text(text),
        "text_sha256": sha256_text(text),
        "provider": "stub",
        "model_id": "stub-tts",
        "model_revision": None,
        "voice_profile_id": "stub-voice",
        "speaker": None,
        "mapping_version": "v1",
        "language": "zh",
        "rate": None,
        "rate_applied": False,
        "volume": None,
        "volume_applied": False,
        "format": "mp3",
        "sample_rate": 24000,
        "artifact_hash": sha256_file(path),
        "request_key": "stub-%s" % path.stem,
    }
    ip = identity_path(path)
    ip.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _voice(path: Path, text: str, voice_id: str) -> None:
    """旧口径签名边车 `<audio>.voice` = `音色|正文指纹`（消费侧真正读的那份）."""
    from shared.audio_identity import speech_signature
    p = path.with_suffix(".voice")
    p.write_text(speech_signature(text, {"id": voice_id}), encoding="utf-8")

def test_step04_merge_produces_final_mp4(tmp_path, monkeypatch):
    _block_network(monkeypatch)
    from shared import paths

    src = _formal_chapter()
    ch = tmp_path / "chapter_001"
    shutil.copytree(src, ch)
    monkeypatch.setattr(paths, "chapter_dir", lambda *a, **k: ch)
    script = json.loads((ch / "script_result.json").read_text(encoding="utf-8"))
    rev = str(script.get("script_revision") or "")

    # 1) 13 段桩片，落在合并真正读取的位置
    wd = ch / "vimax_working"
    for shot in script["shots"]:
        frames = int(round(float(shot["duration_sec"]) * FPS))
        _clip(wd / "shots" / str(shot["index"]) / "video.mp4", frames)

    # 2) step02b 完成报告走真实生产入口
    from step02_video_gen.video_prompt_quality import write_video_product_quality
    report = write_video_product_quality(script, wd)
    assert report["status"] == "PASS", report.get("missing")

    # 3) 按**消费入口**的判据生成对白轨 + v2 身份侧车
    from shared.dialogue import strip_speaker_prefix
    from step01_script_gen.plan_feasibility import (
        shot_needs_dialogue_track, shot_voice_parts)
    made = []
    for shot in script["shots"]:
        if not shot_needs_dialogue_track(shot, None):
            continue
        split = shot_voice_parts(shot, None)
        text = (split["dialogue"].strip() if split["entries"]
                else strip_speaker_prefix(
                    str(shot.get("narration_segment", "")).strip(),
                    script.get("characters") or []))
        if not text:
            continue
        audio = paths.audio_file(shot["index"])
        # 消费侧要求 >10240 字节，64kbps 下约需 1.3s；取 2.0s 且不超过本镜可用时长
        _tts(audio, min(2.0, max(1.5, float(shot["duration_sec"]) - 0.5)))
        _voice(audio, text, "stub-voice")
        made.append(shot["index"])
    assert made, "没有任何镜头被判需要对白轨"

    # 旁白轨：N1..N4（消费侧单独按 N 编号取，不走 shot 索引）
    from step01_script_gen.plan_feasibility import voice_units
    from tts.voice_registry import NARRATOR_VOICE
    units = voice_units(script.get("chapter_outline"), script) or []
    assert units, "正式稿没有任何旁白单元"
    for unit in units:
        n = int(unit["index"])
        audio = paths.narration_audio_file(n)
        _tts(audio, 2.5)
        _voice(audio, str(unit["text"]), str(NARRATOR_VOICE.get("id", "")))

    # 4) 真实合并入口
    import step04_merge.main as m
    m.CHAPTER_DIR = ch
    m.SCRIPT_FILE = ch / "script_result.json"
    m.CLIP_DIR = wd
    m.VIMAX_WD = wd
    m.FINAL_OUT = ch / "final.mp4"
    m.SRT_OUT = ch / "subtitles.srt"
    m.TEMP_DIR = tmp_path / "temp"
    m.main(options=None)

    out = ch / "final.mp4"
    assert out.is_file() and out.stat().st_size > 0
    info = json.loads(subprocess.run(
        [FFPROBE, "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(out)],
        capture_output=True, text=True, check=True).stdout)
    kinds = {s["codec_type"] for s in info["streams"]}
    assert {"video", "audio"} <= kinds, kinds
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (int(video["width"]), int(video["height"])) == (WIDTH, HEIGHT)
    srt = (ch / "subtitles.srt").read_text(encoding="utf-8")
    assert "00:00:" in srt and len(srt.strip().splitlines()) >= 4
    print("final:", out, "duration:", info["format"]["duration"], "tts:", made)