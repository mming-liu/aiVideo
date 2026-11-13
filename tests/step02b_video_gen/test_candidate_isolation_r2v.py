# -*- coding: utf-8 -*-
"""F2：候选运行的 r2v 拼表落 attempt，正式 shots/ 前后 hash 不变.

`_submit_video_candidate` 现在把 attempt/refs 作为 `_make_request` 的
`ref_sheet_dir`；此前 `fit_refs` 固定写生产 `shots/<idx>/`，候选跑完才在
production snapshot 里发现污染。
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from _video_gen.models import VideoGenerationRequest
from shared import enhancement_io as eio
from step02_video_gen import submit_poll as sp
from tests.enhancements.video_stub_provider import (
    StubVideoAdapter,
    StubVideoService,
    make_video,
)


def _shot() -> dict:
    return {
        "index": 1, "duration_sec": 5,
        "ff_desc": "甲站在船头", "lf_desc": "甲转身离开",
        "motion_desc": "甲转身", "prompt": "甲转身离开",
    }


def _production_tree(tmp_path: Path) -> Path:
    prod = tmp_path / "production"
    shot_dir = prod / "shots" / "1"
    shot_dir.mkdir(parents=True)
    (shot_dir / "first_frame.png").write_bytes(b"production-ff")
    (prod / "clips").mkdir()
    (prod / "clips" / "clip_1.mp4").write_bytes(b"production-clip")
    return prod


def _snapshot(root: Path) -> dict:
    return {str(p): p.read_bytes().hex()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_candidate_r2v_compose_writes_only_attempt_refs(tmp_path, monkeypatch):
    prod = _production_tree(tmp_path)
    before = _snapshot(prod)

    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "r2v")
    monkeypatch.setenv("VIDEO_PROVIDER", "stub")
    monkeypatch.setenv("VIDEO_CREDENTIAL_PROFILE_ID", "profile-ci")
    monkeypatch.setenv("VIDEO_CREDENTIAL_REVISION", "r1")
    monkeypatch.setenv("NOVEL_CHAPTER", "1")
    monkeypatch.setattr(sp, "INITIAL_POLL_DELAY", 0)
    monkeypatch.setattr(sp, "POLL_BASE_INTERVAL", 0)
    monkeypatch.setattr(sp, "POLL_MAX_INTERVAL", 0)
    monkeypatch.setattr(sp, "POLL_JITTER", 0)
    monkeypatch.setattr(eio.AttemptLock, "acquire", lambda self: self)
    monkeypatch.setattr(eio.AttemptLock, "release", lambda self: None)

    attempt = tmp_path / "candidate_pkg" / "attempts" / "a-f2"
    attempt.mkdir(parents=True)
    ctx = SimpleNamespace(
        attempt_dir=attempt,
        package_root=tmp_path / "candidate_pkg",
        outputs_dir=attempt / "outputs",
        scope={"chapter": 1},
        script_revision="rev-f2",
        source="test_f2_candidate",
        attempt_id="a-f2",
        candidate_path=None,
        package="video",
        chapter=1,
    )

    seen: list[Path] = []

    def _make_request(adapter, prompt, frame_paths, shot, **kwargs):
        d = Path(kwargs["ref_sheet_dir"])
        d.mkdir(parents=True, exist_ok=True)
        sheet = d / "ref_sheet_stub.png"
        sheet.write_bytes(b"composed-sheet")
        seen.append(d)
        return VideoGenerationRequest(
            prompt=prompt, clip_index=shot["index"], duration=5,
            reference_paths=[str(sheet)])

    video_source = make_video(tmp_path / "stub_source.mp4", seconds=5.0)
    service = StubVideoService(video_source=str(video_source))

    async def _go():
        task = await sp._submit_video_candidate(
            shot=_shot(), shot_dir=attempt / "shot", video_path=attempt / "shot" / "video.mp4",
            frame_paths=[], shot_hash="h-f2",
            video_adapter=StubVideoAdapter(service),
            poll_semaphore=asyncio.Semaphore(1), script={"characters": []}, idx=1,
            prompt="甲转身离开", characters=[], continuity_ref_image=None, mode="A",
            candidate_context=ctx, logger=None, make_request=_make_request)
        return await task if task is not None else None

    assert asyncio.run(_go()) is True
    assert seen and all(attempt in d.parents for d in seen), seen
    assert (attempt / "refs" / "ref_sheet_stub.png").is_file()
    assert _snapshot(prod) == before, "候选运行不得修改正式 shots/clips"
