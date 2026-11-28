"""Step02b 留痕边界缺口（R1/R2/R3）的离线回归.

对应 2026-10-31 复核报告：
- R1 远端明确失败被误记 io_failed/unverified → 现按报错来源分流；
- R2 生产成功 attempt 停在 checked（非终态）→ 现闭合 accepted；
- R3 仅 clips 副本缺失时误发付费生成 POST → 现本地补拷，零提交。

全部为离线单测：不发网络请求、不调用模型。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from shared import video_attempts as va
from step02_video_gen import submit_poll as sp


@pytest.fixture(autouse=True)
def offline():
    from tools.probe_isolation import block_external_transport
    with block_external_transport():
        yield


@pytest.mark.parametrize(
    "err,state,remote",
    [
        ("生成失败: TaskFailed", va.STATE_PROVIDER_FAILED, "failed"),
        ("轮询超时 (600s): last", va.STATE_PROVIDER_FAILED, "failed"),
        ("下载失败: connection reset", va.STATE_IO_FAILED, "succeeded"),
        ("something else", va.STATE_IO_FAILED, "unverified"),
    ],
)
def test_classify_poll_failure(err, state, remote):
    assert sp._classify_poll_failure(err) == (state, remote)


def test_repair_missing_clip_copies_without_network(tmp_path):
    video = tmp_path / "shot" / "video.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"v" * 40000)
    clip = tmp_path / "clips" / "clip_5.mp4"

    assert sp.repair_missing_clip(tmp_path / "shot", video, clip) is True
    assert clip.exists() and clip.stat().st_size == video.stat().st_size


def test_repair_missing_clip_rejects_tiny_video(tmp_path):
    video = tmp_path / "shot" / "video.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"v" * 100)
    clip = tmp_path / "clips" / "clip_5.mp4"

    assert sp.repair_missing_clip(tmp_path / "shot", video, clip) is False
    assert not clip.exists()


def test_clip_missing_repairs_locally_without_submit(tmp_path, monkeypatch):
    """R3 入口级：视频与 hash 完好、仅 clip 缺失 ⇒ 补拷成功且 0 次提交。"""
    from shared.paths import clip_file

    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path / "output"))
    monkeypatch.setenv("NOVEL_NAME", "fixture")
    monkeypatch.setenv("BOOK_NAME", "")
    sp._failed_videos.clear()

    shot = {"index": 5, "duration_sec": 6}
    script = {"characters": [], "shots": [shot]}
    shots_dir = tmp_path / "shots"
    shot_dir = shots_dir / "5"
    shot_dir.mkdir(parents=True)
    (shot_dir / "video.mp4").write_bytes(b"v" * 40000)
    (shot_dir / "prompt_hash.txt").write_text(
        sp._get_shot_hash(shot, script, shot_dir=shot_dir), encoding="utf-8")

    clip = clip_file(5)
    clip.unlink(missing_ok=True)

    def _boom(*a, **k):
        raise AssertionError("clip 缺失不应触发远端提交")

    monkeypatch.setattr(sp, "_submit_video", _boom)

    task = asyncio.run(sp._process_one_shot(
        shot, shots_dir, None, None, None, False, script, asyncio.Semaphore(1)))
    assert task is None
    assert clip.exists() and clip.stat().st_size > 20 * 1024
    assert not sp._failed_videos
