# -*- coding: utf-8 -*-
"""S02-C1：缓存消费者与提交同源（2026-11-17）.

覆盖用户复核指出的缺口：
1. 缓存关系核对必须用与提交同一份 ``prepared`` 事实（provider/model/
   capability_revision 也取 prepared），否则 plan 与提交的 ``request_key`` 不同源；
2. prepare 失败必须**单次终局**：不得回到提交入口再 prepare 一次；
3. 「无快照的旧兼容」与「有快照但无法核对」分开：后者不得 legacy 命中；
4. 无声模型切换 ``MERGE_AUDIO_MODE`` 仍应精确命中（prompt_hash 不再随它抖动）。

测试用 21KB 字节文件满足缓存门的体量检查；这只验证缓存逻辑，媒体可播放性由
对应媒体检查负责。所有配置显式设置，不依赖开发机 ``.env.content``。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from _video_gen.models import VideoCapabilityError
from step02_video_gen import submit_poll as sp
from step02b_video_gen import main as m
from tests.enhancements.video_stub_provider import StubVideoAdapter, StubVideoService

SHOT_INDEX = 1


def _shot(prompt: str = "[中景] 甲站在船头") -> dict:
    return {
        "index": SHOT_INDEX, "prompt": prompt,
        "ff_desc": "甲站在船头", "lf_desc": "甲转身离开",
        "motion_desc": "甲转身", "duration_sec": 5.0,
        "subtitle": "", "narration_segment": "", "keyframes": [],
    }


def _script(shot: dict) -> dict:
    return {"characters": [], "shots": [shot]}


def _visual_plan(shot: dict) -> dict:
    return {"shots": [{
        "shot_index": SHOT_INDEX, "status": "ready", "frames": [],
        "visible_characters": [], "offscreen_speakers": [],
        "execution": {"unit": shot},
    }]}


def _env(monkeypatch, tmp_path, *, policy: str = "auto",
         provider: str = "stub", model: str | None = None,
         merge_mode: str = "native") -> None:
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path / "out"))
    monkeypatch.setenv("NOVEL_NAME", "")
    monkeypatch.setenv("NOVEL_CHAPTER", "1")
    monkeypatch.setenv("BOOK_INDEX", "1")
    monkeypatch.setenv("BOOK_NAME", "")
    monkeypatch.setenv("VIDEO_PROVIDER", provider)
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    monkeypatch.setenv("VIDEO_MODEL_VOICE", "1")
    monkeypatch.setenv("MERGE_AUDIO_MODE", merge_mode)
    monkeypatch.setenv("VIDEO_MEDIA_POLICY", policy)
    monkeypatch.setenv("STUB_VIDEO_MODEL", "stub-model-1")
    monkeypatch.setenv("VIDEO_CREDENTIAL_PROFILE_ID", "profile-ci")
    monkeypatch.setenv("VIDEO_CREDENTIAL_REVISION", "r1")
    if model:
        monkeypatch.setenv("VOLCENGINE_I2V_MODEL", model)
    monkeypatch.setattr(sp, "INITIAL_POLL_DELAY", 0)
    monkeypatch.setattr(sp, "POLL_BASE_INTERVAL", 0)
    monkeypatch.setattr(sp, "POLL_MAX_INTERVAL", 0)
    monkeypatch.setattr(sp, "POLL_JITTER", 0)


def _paths(tmp_path, monkeypatch) -> tuple[Path, Path, Path]:
    shots_dir = tmp_path / "shots"
    shot_dir = shots_dir / str(SHOT_INDEX)
    shot_dir.mkdir(parents=True)
    (shot_dir / "first_frame.png").write_bytes(b"ff-1" + b"\0" * 128)
    (shot_dir / "last_frame.png").write_bytes(b"lf-1" + b"\0" * 128)
    (shot_dir / "keyframe_0.png").write_bytes(b"kf-1" + b"\0" * 128)
    (shot_dir / "video.mp4").write_bytes(b"v" * (21 * 1024))
    clip = tmp_path / "clips" / "clip_1.mp4"
    clip.parent.mkdir(parents=True)
    clip.write_bytes(b"c" * (21 * 1024))
    (shots_dir.parent / "visual_plan.json").write_text(
        json.dumps(_visual_plan(_shot()), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(m, "chapter_dir", lambda *a, **k: tmp_path)
    monkeypatch.setattr("shared.paths.chapter_dir", lambda *a, **k: tmp_path)
    monkeypatch.setattr(m, "clip_file", lambda idx: clip)
    monkeypatch.setattr("shared.paths.production_video_attempts_root",
                        lambda shot_dir: tmp_path / "attempts")
    return shots_dir, shot_dir, clip


def _reqs(shot: dict, script: dict, shot_dir: Path, visual: dict | None = None):
    frame_paths = [str(shot_dir / name) for name in
                   ("first_frame.png", "last_frame.png", "keyframe_0.png")]
    if visual is None:
        plan_path = shot_dir.parent.parent / "visual_plan.json"
        if plan_path.is_file():
            visual = json.loads(plan_path.read_text(encoding="utf-8"))
    return m._execution_requirements(shot, script, visual, frame_paths, None)


def _facts(shot: dict, script: dict, shot_dir: Path, adapter, *, reqs=None):
    return sp.build_prepared_facts(
        shot=shot, script=script, video_adapter=adapter,
        frame_paths=[str(shot_dir / name) for name in
                     ("first_frame.png", "last_frame.png", "keyframe_0.png")],
        mode="A",
        scope={"chapter": 1, "book_index": 1, "book_name": ""},
        script_revision=sp._script_revision(script),
        provider=None, model_id=None,
        execution_requirements=reqs if reqs is not None else _reqs(shot, script, shot_dir),
    )


def _write_snapshot(shot: dict, script: dict, shot_dir: Path, adapter, *,
                    reqs=None) -> dict:
    facts = _facts(shot, script, shot_dir, adapter, reqs=reqs)
    assert facts.status == "prepared", facts.error
    sp.write_frozen_snapshot(shot_dir, facts.frozen)
    (shot_dir / "prompt_hash.txt").write_text(
        sp._get_shot_hash(shot, script, shot_dir=shot_dir), encoding="utf-8")
    return facts.frozen.to_dict()


async def _run(shot: dict, script: dict, shots_dir: Path, adapter):
    return await m._process_one_shot_skip_frames(
        shot, shots_dir, adapter, None, script,
        poll_semaphore=asyncio.Semaphore(1), force=False)


@pytest.fixture(autouse=True)
def _clean_failures():
    sp._failed_videos.clear()
    yield
    sp._failed_videos.clear()


def _spy_cache(monkeypatch) -> list:
    seen = []
    real = m.cache_decision

    def _spy(*args, **kwargs):
        result = real(*args, **kwargs)
        seen.append(result)
        return result

    monkeypatch.setattr(m, "cache_decision", _spy)
    return seen


def test_second_run_hits_exact_without_post(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    shots_dir, shot_dir, _ = _paths(tmp_path, monkeypatch)
    shot, script = _shot(), _script(_shot())
    adapter = StubVideoAdapter(StubVideoService())
    _write_snapshot(shot, script, shot_dir, adapter)

    seen = _spy_cache(monkeypatch)
    submits = []

    async def _fake_submit(*args, **kwargs):
        submits.append(kwargs)
        return None

    monkeypatch.setattr(m, "_submit_video", _fake_submit)
    assert asyncio.run(_run(shot, script, shots_dir, adapter)) is None

    assert seen and seen[-1]["relation"] == "exact", seen
    assert submits == [], "精确命中不得进入提交"
    assert adapter.service.posts == []


def test_prompt_change_is_mismatch_and_submits(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    shots_dir, shot_dir, _ = _paths(tmp_path, monkeypatch)
    shot, script = _shot(), _script(_shot())
    adapter = StubVideoAdapter(StubVideoService())
    _write_snapshot(shot, script, shot_dir, adapter)

    shot["prompt"] = "[中景] 甲跃上船头"
    script = _script(shot)
    (shot_dir / "prompt_hash.txt").write_text(
        sp._get_shot_hash(shot, script, shot_dir=shot_dir), encoding="utf-8")
    seen = _spy_cache(monkeypatch)
    submits = []

    async def _fake_submit(*args, **kwargs):
        submits.append(kwargs)
        return None

    monkeypatch.setattr(m, "_submit_video", _fake_submit)
    assert asyncio.run(_run(shot, script, shots_dir, adapter)) is None

    assert seen and seen[-1]["relation"] == "mismatch", seen
    assert len(submits) == 1


def test_used_frame_change_is_mismatch(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    shots_dir, shot_dir, _ = _paths(tmp_path, monkeypatch)
    shot, script = _shot(), _script(_shot())
    adapter = StubVideoAdapter(StubVideoService())
    _write_snapshot(shot, script, shot_dir, adapter)

    (shot_dir / "first_frame.png").write_bytes(b"ff-2" + b"\0" * 128)
    (shot_dir / "prompt_hash.txt").write_text(
        sp._get_shot_hash(shot, script, shot_dir=shot_dir), encoding="utf-8")
    seen = _spy_cache(monkeypatch)
    monkeypatch.setattr(m, "_submit_video", _fake_submit_none())
    asyncio.run(_run(shot, script, shots_dir, adapter))
    assert seen and seen[-1]["relation"] == "mismatch", seen


def test_execution_requirement_change_is_mismatch(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    shots_dir, shot_dir, _ = _paths(tmp_path, monkeypatch)
    shot, script = _shot(), _script(_shot())
    adapter = StubVideoAdapter(StubVideoService())
    _write_snapshot(shot, script, shot_dir, adapter)

    # 只改画面计划摘要（prompt_hash 不含 visual_plan）；执行要求变 ⇒ key 必须变。
    plan = _visual_plan(shot)
    plan["shots"][0]["frames"] = [{"frame_id": "ff", "role": "first_frame"}]
    (shots_dir.parent / "visual_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    seen = _spy_cache(monkeypatch)
    monkeypatch.setattr(m, "_submit_video", _fake_submit_none())
    asyncio.run(_run(shot, script, shots_dir, adapter))
    assert seen and seen[-1]["relation"] == "mismatch", seen


def test_silent_model_mix_switch_still_exact(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path, provider="volcengine",
         model="doubao-seedance-1-0-pro-250528", merge_mode="native")
    shots_dir, shot_dir, _ = _paths(tmp_path, monkeypatch)
    shot, script = _shot(), _script(_shot())
    adapter = StubVideoAdapter(StubVideoService())
    _write_snapshot(shot, script, shot_dir, adapter)
    before = json.loads((shot_dir / sp.FROZEN_SNAPSHOT_FILENAME).read_text(encoding="utf-8"))
    assert before["requirements"]["execution"]["audio"]["policy"]["reason"]

    monkeypatch.setenv("MERGE_AUDIO_MODE", "tts_mix")
    seen = _spy_cache(monkeypatch)
    monkeypatch.setattr(m, "_submit_video", _fake_submit_none())
    asyncio.run(_run(shot, script, shots_dir, adapter))

    assert seen and seen[-1]["relation"] == "exact", seen
    after = json.loads((shot_dir / sp.FROZEN_SNAPSHOT_FILENAME).read_text(encoding="utf-8"))
    assert after["requirements"]["execution"]["audio"]["policy"]["reason"]


def _fake_submit_none():
    async def _fake(*args, **kwargs):
        return None
    return _fake


class _FailOnceService(StubVideoService):
    def __init__(self):
        super().__init__()
        self.prepare_calls = 0

    def prepare_request(self, req, context=None):
        self.prepare_calls += 1
        if self.prepare_calls == 1:
            raise VideoCapabilityError("stub_prepare_failed", "第一次准备失败")
        return super().prepare_request(req, context)


def test_prepare_failure_is_terminal_and_single(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    shots_dir, shot_dir, _ = _paths(tmp_path, monkeypatch)
    shot, script = _shot(), _script(_shot())
    (shot_dir / "prompt_hash.txt").write_text(
        sp._get_shot_hash(shot, script, shot_dir=shot_dir), encoding="utf-8")
    service = _FailOnceService()
    adapter = StubVideoAdapter(service)
    seen = _spy_cache(monkeypatch)

    assert asyncio.run(_run(shot, script, shots_dir, adapter)) is None
    assert service.prepare_calls == 1, "首败后不得再 prepare 第二次"
    assert service.posts == []
    assert seen == [], "prepare 失败时不得先走缓存判定"
    records = sp._failed_videos.get(SHOT_INDEX) or []
    assert records and records[-1]["code"] == "stub_prepare_failed", records


def test_unsupported_with_snapshot_does_not_legacy_hit(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    shots_dir, shot_dir, _ = _paths(tmp_path, monkeypatch)
    shot, script = _shot(), _script(_shot())
    adapter = StubVideoAdapter(StubVideoService())
    _write_snapshot(shot, script, shot_dir, adapter)

    bare = SimpleNamespace(service=object())
    seen = _spy_cache(monkeypatch)
    assert asyncio.run(_run(shot, script, shots_dir, bare)) is None
    assert seen == [], "有快照但无法核对时不得按 legacy 命中"
    records = sp._failed_videos.get(SHOT_INDEX) or []
    assert records and records[-1]["code"] == "prepared_unsupported", records


def test_unsupported_without_snapshot_allows_legacy(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    shots_dir, shot_dir, _ = _paths(tmp_path, monkeypatch)
    shot, script = _shot(), _script(_shot())
    (shot_dir / "prompt_hash.txt").write_text(
        sp._get_shot_hash(shot, script, shot_dir=shot_dir), encoding="utf-8")

    bare = SimpleNamespace(service=object())
    seen = _spy_cache(monkeypatch)
    submits = []

    async def _fake_submit(*args, **kwargs):
        submits.append(kwargs)
        return None

    monkeypatch.setattr(m, "_submit_video", _fake_submit)
    assert asyncio.run(_run(shot, script, shots_dir, bare)) is None
    assert seen and seen[-1]["relation"] == "legacy", seen
    assert submits == []
