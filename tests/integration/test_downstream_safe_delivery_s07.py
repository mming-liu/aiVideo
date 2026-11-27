# -*- coding: utf-8 -*-
"""S07：下游全链路离线交付（Step03 配音 → Step02b 生视频 → Step04 合并）.

**0 次外部模型调用**：TTS 用本地桩 provider（写真实 WAV），视频用本地桩 provider
（造真实 MP4），Step04 用真实 FFmpeg 后端。三阶段都以各自**真实入口**在独立子进程里
运行（各自的 import 期路径/配置与生产一致），最后核：

- 成片可播放、`step04_quality.json` 媒体终检 PASS；
- **声音完整**：每条对白音轨实测时长都进了成片，`step03_duration_audit` 无超窗；
- **不重付**：Step02b 第二次运行命中缓存，产物字节不变、不再提交；
- **失败不覆盖基线**：缺片段重跑 Step04 明确失败，既有 `final.mp4` 与质量报告原样保留。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.downstream_fixtures import (
    build_anonymous_story_pack,
    install_committed_chapter,
    install_step02a_acceptance,
)

REPO = Path(__file__).resolve().parents[2]
FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(FFMPEG is None or FFPROBE is None,
                                reason="需要本机 ffmpeg/ffprobe")

CHAPTER = 21


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _probe_duration(path: Path) -> float:
    proc = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                           "-of", "csv=p=0", str(path)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    return float((proc.stdout or "0").strip() or 0)


def _mean_volume_db(path: Path, *, start: float, duration: float, freq: int) -> float:
    """在成片指定窗口内测某频率附近的能量（bandpass + volumedetect，确定性）."""
    proc = subprocess.run(
        [FFMPEG, "-v", "info", "-ss", "%.3f" % start, "-t", "%.3f" % duration,
         "-i", str(path), "-vn",
         "-af", "bandpass=f=%d:width_type=h:w=60,volumedetect" % freq,
         "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    match = re.search(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB", proc.stderr or "")
    assert match, proc.stderr[-800:]
    return float(match.group(1))


def _chapter(tmp_path: Path) -> dict:
    pack = build_anonymous_story_pack("compact", chapter=CHAPTER)
    ch = (tmp_path / "output" / "匿名样本作品" / "part_01_匿名测试卷"
          / ("chapter_%03d" % CHAPTER))
    installed = install_committed_chapter(ch, pack)
    install_step02a_acceptance(ch, installed["script"], shot_policies={1: "auto", 2: "auto"})
    # 与 step02a 生产一致：抽帧之外的候选计划（Step02b 读它做执行要求）
    return {"ch": ch, "script": installed["script"], "outline": pack["outline"],
            "root": tmp_path / "output"}


def _env(ctx: dict, tmp_path: Path, **extra) -> dict:
    # 从**当前进程环境**派生：Windows 上缺 SYSTEMROOT/PATH 会让子进程 import
    # `_overlapped`/socket 初始化失败（WinError 10106），这与业务无关。
    env = dict(os.environ)
    env.update({
        "OUTPUT_BASE": str(ctx["root"]),
        "NOVEL_NAME": "匿名样本作品",
        "BOOK_INDEX": "1",
        "BOOK_NAME": "匿名测试卷",
        "NOVEL_CHAPTER": str(CHAPTER),
        "PYTHONPATH": str(REPO),
        "PYTHONIOENCODING": "utf-8",
        "VOLCENGINE_API_KEY": "test-volc-key",
        "VOLCENGINE_T2V_MODEL": "doubao-seedance-1-0-pro-250528",
        "VOLCENGINE_I2V_MODEL": "doubao-seedance-1-0-pro-250528",
        "VIDEO_PROVIDER": "volcengine",
        "VIDEO_REFERENCE_MODE": "image",
        "VIDEO_MEDIA_POLICY": "auto",
        "VIDEO_QC_ENABLED": "0",
        "TTS_PROVIDER": "step03_stub_provider",
        "TTS_AUTO_CONFIRM": "1",
        "TTS_VOICE_REGISTRY_PATH": str(tmp_path / "voice_registry.json"),
        "MERGE_AUDIO_MODE": "tts_mix",
        "MERGE_AUDIO_LOUDNORM": "1",
        "STEP02B_VIDEO_BUDGET_TOTAL": "10",
    })
    env.update({str(k): str(v) for k, v in extra.items()})
    return env


def _run_stage(script: str, env: dict, tmp_path: Path, name: str) -> subprocess.CompletedProcess:
    path = tmp_path / ("stage_%s.py" % name)
    path.write_text(script, encoding="utf-8")
    proc = subprocess.run([sys.executable, str(path)], cwd=str(REPO), env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=900)
    assert proc.returncode == 0, (name, proc.stdout[-2500:], proc.stderr[-2500:])
    return proc


_STEP03_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, %(repo)r)
from tts import config as tts_config, service as tts_service
from tests.step03_tts_audio._tts_stub import StubProvider
# 桩音频要**长到**能被下游当有效音轨（>10KB）且仍落在 5s 镜头窗内
tts_service.register("step03_stub_provider",
                     lambda: StubProvider(dur_per_char=0.34, max_dur=4.0))
tts_config.PROVIDER = "step03_stub_provider"
from step03_tts_audio import cli as stage_cli
from step03_tts_audio import main as stage_main
stage_main.main(stage_cli.StageOptions())
# 供测试核对"成片里真的含有各镜声音"：单元文本 → 桩音色频率（与桩同式）
ledger = os.environ.get("TTS_UNIT_LEDGER")
if ledger:
    from step03_tts_audio.shot_tts import build_speech_units
    from shared.paths import script_file
    script = json.loads(script_file(%(chapter)d).read_text(encoding="utf-8"))
    units = build_speech_units(script, scope={"chapter": %(chapter)d},
                               script_revision=str(script.get("script_revision") or ""),
                               provider_name="step03_stub_provider")
    rows = [{"unit_id": u.unit_id, "kind": u.kind,
             "shot_index": getattr(u, "shot_index", None) or getattr(u, "source_index", None),
             "text": u.text, "tone_hz": 400 + 30 * (sum(ord(c) for c in u.text) %% 9)}
            for u in units if u.kind == "dialogue"]
    open(ledger, "w", encoding="utf-8").write(json.dumps(rows, ensure_ascii=False))
"""

_STEP02B_SCRIPT = r"""
import json, os, sys
sys.path.insert(0, %(repo)r)
from step02b_video_gen import cli as stage_cli
from step02b_video_gen import main as stage_main
from tests.enhancements.video_stub_provider import StubVideoAdapter, StubVideoService
svc = StubVideoService()
stage_main.VideoGeneratorAiVideoAdapter = lambda: StubVideoAdapter(svc)
argv = %(argv)r
code = stage_main.main(stage_cli.parse_args(argv))
ledger = os.environ.get("STUB_POST_LEDGER")
if ledger:
    open(ledger, "w", encoding="utf-8").write(
        json.dumps({"posts": len(svc.posts), "queries": len(svc.queries)}))
raise SystemExit(code)
"""

_STEP04_SCRIPT = r"""
import sys
sys.path.insert(0, %(repo)r)
from step04_merge import main as stage_main
raise SystemExit(stage_main.main())
"""


def test_downstream_offline_pipeline_delivers_playable_final(tmp_path):
    ctx = _chapter(tmp_path)
    ch = ctx["ch"]
    env = _env(ctx, tmp_path)
    unit_ledger = tmp_path / "tts_units.json"
    post_ledger = tmp_path / "ledger.json"
    env["TTS_UNIT_LEDGER"] = str(unit_ledger)
    env["STUB_POST_LEDGER"] = str(post_ledger)

    # ---- 1. Step03 配音（真实入口；桩 provider 写真实 WAV） ----
    _run_stage(_STEP03_SCRIPT % {"repo": str(REPO), "chapter": CHAPTER},
               env, tmp_path, "step03")
    audio1, audio2 = ch / "audio" / "shot_1.mp3", ch / "audio" / "shot_2.mp3"
    assert audio1.is_file() and audio2.is_file()
    assert (ch / "voice_state.json").is_file()
    quality3 = json.loads((ch / "step03_quality.json").read_text(encoding="utf-8"))
    assert quality3["status"] == "PASS", quality3
    audit = json.loads((ch / "step03_duration_audit.json").read_text(encoding="utf-8"))
    assert audit["schema"] == "step03_duration_audit.v1"
    assert audit["summary"]["measured_units"] >= 2
    assert audit["summary"]["overflow_shot_count"] == 0, audit["shots"]
    planned_need = {k: v["audio_need_sec"] for k, v in audit["shots"].items()}
    assert all(need is not None for need in planned_need.values()), planned_need

    # ---- 2. Step02b 生视频（真实入口；桩 provider 造真实 MP4，1 POST/镜） ----
    _run_stage(_STEP02B_SCRIPT % {"repo": str(REPO), "argv": ["--force"]},
               env, tmp_path, "step02b")
    first_posts = json.loads(post_ledger.read_text(encoding="utf-8"))["posts"]
    assert first_posts == 2, first_posts
    clip1 = ch / "vimax_working" / "shots" / "1" / "video.mp4"
    clip2 = ch / "vimax_working" / "shots" / "2" / "video.mp4"
    assert clip1.is_file() and clip2.is_file()
    quality2b = json.loads((ch / "step02b_quality.json").read_text(encoding="utf-8"))
    assert quality2b["status"] == "PASS", quality2b
    clip_sha = {str(p): _sha(p) for p in (clip1, clip2)}

    # ---- 2b. 第二次运行命中缓存：**零新增 POST**、产物不变（不重付） ----
    _run_stage(_STEP02B_SCRIPT % {"repo": str(REPO), "argv": []},
               env, tmp_path, "step02b_again")
    second_posts = json.loads(post_ledger.read_text(encoding="utf-8"))["posts"]
    assert second_posts == 0, second_posts
    assert {str(p): _sha(p) for p in (clip1, clip2)} == clip_sha

    # ---- 3. Step04 合并（真实入口 + 真实 FFmpeg 后端） ----
    _run_stage(_STEP04_SCRIPT % {"repo": str(REPO)}, env, tmp_path, "step04")
    final = ch / "final.mp4"
    assert final.is_file() and final.stat().st_size > 20 * 1024
    final_sha = _sha(final)
    quality4 = json.loads((ch / "step04_quality.json").read_text(encoding="utf-8"))
    assert quality4["status"] == "PASS", quality4
    assert quality4["media_check"]["status"] == "PASS", quality4["media_check"]["defects"]
    assert quality4["audio_fit"]["policy"] == "fail"
    assert quality4["audio_fit"]["adjustments"] == []
    assert (ch / "subtitles.srt").is_file()

    # 成片时长 ≈ 两镜之和（无转场声明时）
    expected = float(quality4["outputs"]["final"]["duration_sec"])
    assert _probe_duration(final) == pytest.approx(expected, abs=0.3)

    # 声音完整（一）：每条对白音轨的实测时长与审计一致
    for idx, need in planned_need.items():
        shot_audio = ch / "audio" / ("shot_%s.mp3" % idx)
        assert shot_audio.is_file()
        assert _probe_duration(shot_audio) == pytest.approx(need, abs=0.2)

    # 声音完整（二）：**成片里真的含有各镜声音**——按镜窗口实测该镜桩频率能量，
    # 且显著高于无关频率（不是只看源文件长度）。
    units = json.loads(unit_ledger.read_text(encoding="utf-8"))
    starts: dict = {}
    cursor = 0.0
    for shot in ctx["script"]["shots"]:
        starts[int(shot["index"])] = cursor
        cursor += float(shot.get("duration_sec") or 0.0)
    checked = 0
    for unit in units:
        idx = int(unit.get("shot_index") or 0)
        if idx not in starts:
            continue
        start = starts[idx] + 0.3
        duration = max(0.8, float(planned_need.get(str(idx)) or 1.0) - 0.5)
        on_freq = _mean_volume_db(final, start=start, duration=duration,
                                  freq=int(unit["tone_hz"]))
        off_freq = _mean_volume_db(final, start=start, duration=duration,
                                   freq=int(unit["tone_hz"]) + 600)
        assert on_freq > off_freq + 6, (unit, on_freq, off_freq)
        checked += 1
    assert checked >= 2

    # ---- 4a. 编码前缺片段：失败且既有成片/质量报告原样保留 ----
    removed = clip2
    backup = removed.with_suffix(".bak")
    removed.replace(backup)
    try:
        path = tmp_path / "stage_step04_fail.py"
        path.write_text(_STEP04_SCRIPT % {"repo": str(REPO)}, encoding="utf-8")
        failed = subprocess.run([sys.executable, str(path)], cwd=str(REPO), env=env,
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=900)
        assert failed.returncode != 0
        combined = (failed.stdout or "") + (failed.stderr or "")
        assert "缺少" in combined or "missing" in combined.lower(), combined[-1200:]
        assert _sha(final) == final_sha                      # 既有成片原样保留
        after = json.loads((ch / "step04_quality.json").read_text(encoding="utf-8"))
        assert after == quality4                             # 质量报告未被失败轮覆盖
    finally:
        backup.replace(removed)

    # ---- 4b. **编码后拒收**：终检 BLOCK 时旧成片与旧质量报告也必须原样保留 ----
    strict_env = dict(env)
    strict_env["MERGE_QUALITY_EXPECT_VIDEO_CODEC"] = "h265"
    path = tmp_path / "stage_step04_block.py"
    path.write_text(_STEP04_SCRIPT % {"repo": str(REPO)}, encoding="utf-8")
    blocked = subprocess.run([sys.executable, str(path)], cwd=str(REPO),
                             env=strict_env, capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=900)
    assert blocked.returncode != 0
    combined = (blocked.stdout or "") + (blocked.stderr or "")
    assert "final_quality_blocked" in combined or "最终" in combined, combined[-1500:]
    assert _sha(final) == final_sha                          # 旧成片未被覆盖
    assert json.loads((ch / "step04_quality.json").read_text(encoding="utf-8")) == quality4
    media = json.loads((ch / "step04_media_quality.json").read_text(encoding="utf-8"))
    assert media["status"] == "BLOCK"
    assert any(d.get("code") == "video_codec_unsupported" for d in media["defects"])
