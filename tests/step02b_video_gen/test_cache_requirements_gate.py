# -*- coding: utf-8 -*-
"""F3：执行要求有问题时，缓存命中不得绕过 `validate_requirements`.

此前 step02b 先 `cache_decision` 命中就返回 None，`validate_requirements` 只在
提交路径执行；于是「计划已 blocked + 旧缓存命中」会复用本应被拦的视频。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from step02_video_gen import submit_poll as sp
from step02b_video_gen import main as m
from tests.enhancements.video_stub_provider import StubVideoAdapter, StubVideoService


def _shot() -> dict:
    return {
        "index": 1, "duration_sec": 5,
        "ff_desc": "甲站在船头", "lf_desc": "甲转身离开",
        "motion_desc": "甲转身", "prompt": "甲转身离开",
    }


def test_blocked_visual_plan_skips_cache_and_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "text")
    monkeypatch.setenv("VIDEO_PROVIDER", "stub")
    monkeypatch.setenv("VIDEO_CREDENTIAL_PROFILE_ID", "profile-ci")
    monkeypatch.setenv("VIDEO_CREDENTIAL_REVISION", "r1")
    monkeypatch.setenv("NOVEL_CHAPTER", "1")

    shots_dir = tmp_path / "shots"
    shot_dir = shots_dir / "1"
    shot_dir.mkdir(parents=True)
    shot = _shot()
    script = {"characters": [], "shots": [shot]}

    # 画面计划明确 blocked 本镜 → _reqs.issues 含 visual_plan_blocked
    (tmp_path / "visual_plan.json").write_text(json.dumps({
        "shots": [{"shot_index": 1, "status": "blocked",
                   "frames": [], "execution": {"unit": shot}}],
    }, ensure_ascii=False), encoding="utf-8")

    # 旧缓存：视频 + prompt_hash 都在，但没有冻结快照（legacy 可命中）
    video_path = shot_dir / "video.mp4"
    video_path.write_bytes(b"x" * (21 * 1024))
    prompt_hash = sp._get_shot_hash(shot, script, shot_dir=shot_dir)
    (shot_dir / "prompt_hash.txt").write_text(prompt_hash, encoding="utf-8")

    clip = tmp_path / "clips" / "clip_1.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"c" * (21 * 1024))

    monkeypatch.setattr(m, "chapter_dir", lambda *a, **k: tmp_path)
    monkeypatch.setattr(m, "clip_file", lambda idx: clip)
    monkeypatch.setattr("shared.paths.production_video_attempts_root",
                        lambda shot_dir: tmp_path / "attempts")
    monkeypatch.setattr(sp, "INITIAL_POLL_DELAY", 0)
    monkeypatch.setattr(sp, "POLL_BASE_INTERVAL", 0)
    monkeypatch.setattr(sp, "POLL_MAX_INTERVAL", 0)
    monkeypatch.setattr(sp, "POLL_JITTER", 0)
    sp._failed_videos.clear()

    cache_calls: list = []
    real_cache_decision = m.cache_decision

    def _spy_cache(*args, **kwargs):
        cache_calls.append((args, kwargs))
        return real_cache_decision(*args, **kwargs)

    monkeypatch.setattr(m, "cache_decision", _spy_cache)

    service = StubVideoService()
    adapter = StubVideoAdapter(service)
    result = asyncio.run(m._process_one_shot_skip_frames(
        shot, shots_dir, adapter, None, script,
        poll_semaphore=asyncio.Semaphore(1), force=False,
    ))

    assert result is None, "执行要求阻塞时不得返回可轮询任务"
    assert cache_calls == [], "执行要求有问题时不得先走缓存判定"
    assert service.posts == [], "validate_requirements 必须在零 POST 前拒收"
    records = sp._failed_videos.get(1) or []
    assert records, "拒收必须留下失败记录"
    assert any(r.get("code") == "visual_plan_blocked" for r in records), records
