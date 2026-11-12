# -*- coding: utf-8 -*-
"""S04：视频执行要求、片段映射、反馈路由与桩响应贯通（全离线）."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from _video_gen.models import VideoGenerationRequest
from shared import enhancement_io as eio
from shared.candidate_inputs import resolve_candidate_inputs
from step02_video_gen import prompt_builder as pb
from step02_video_gen import submit_poll as sp
from step02_video_gen.execution_requirements import (
    SCHEMA,
    build_requirements,
    classify_failure,
    merge_into_frozen,
    validate_requirements,
)
from step02_video_gen.request_plan import FrozenVideoRequest, build_frozen_request
from tests.enhancements.video_stub_provider import StubVideoAdapter, StubVideoService

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "shared" / "fixtures" / "execution_contract"


def _family() -> dict:
    return json.loads((FIXTURES / "voice_timing.json").read_text(encoding="utf-8"))


def _shot() -> dict:
    return _family()["baseline"]["script"]["shots"][0]


def _script() -> dict:
    return _family()["baseline"]["script"]


def _reqs(**overrides) -> dict:
    shot = dict(_shot())
    shot.update(overrides.pop("shot", {}))
    return build_requirements(shot, _script(), reference_mode="image",
                              frame_paths=[], model_id="seedance-1.0-pro", **overrides)


def _frozen(requirements: dict | None = None) -> FrozenVideoRequest:
    req = VideoGenerationRequest(prompt="阿遥抱琴说话", clip_index=3, duration=5)
    frozen = build_frozen_request(
        req, shot=_shot(), scope={"chapter": 7}, script_revision="rev-s04",
        provider="stub", model_id="stub-model-1")
    if requirements is not None:
        merge_into_frozen(frozen, requirements)
    return frozen


def test_requirements_capture_semantics_and_audio_identity():
    reqs = _reqs()
    assert reqs["schema"] == SCHEMA
    assert reqs["semantic_shot"]["shot_index"] == 3
    assert reqs["semantic_shot"]["action_order"]
    assert reqs["semantic_shot"]["start_end_state"]["start"]["desc"]
    assert reqs["semantic_shot"]["start_end_state"]["end"]["desc"]
    assert reqs["audio"]["retained_voice_identity"][0]["unit_id"] == "d01"
    assert reqs["audio_artifacts"]["digest"]
    assert reqs["audio_artifacts"]["units"] == reqs["audio"]["retained_voice_identity"]
    assert "visual_plan_not_supplied" in reqs["unverified"]


def test_frozen_request_carries_requirements_and_key_changes():
    frozen = _frozen()
    base_key = frozen.request_key
    reqs = _reqs()
    merge_into_frozen(frozen, reqs)
    assert frozen.requirements["execution"]["schema"] == SCHEMA
    assert frozen.source["semantic_shot"]["shot_index"] == 3
    assert frozen.source["execution_requirements_schema"] == SCHEMA
    assert frozen.request_key != base_key
    assert validate_requirements(frozen, reqs) == []


def test_fragment_mapping_detects_event_loss_and_order():
    shot = dict(_shot())
    shot["production_fragments"] = [
        {"fragment_id": "f1", "events": ["低头拨弦"], "start_state": "抬头",
         "end_state": "低头", "duration_sec": 2},
        {"fragment_id": "f2", "events": ["阿遥抬头说话"], "start_state": "抱琴",
         "end_state": "抬头", "duration_sec": 3},
    ]
    reqs = build_requirements(shot, _script(), reference_mode="image",
                              model_id="seedance-1.0-pro")
    codes = {i["code"] for i in reqs["issues"]}
    assert "event_order_mismatch" in codes
    shot["production_fragments"] = [
        {"fragment_id": "f1", "events": ["阿遥抬头说话"], "duration_sec": 2},
    ]
    reqs = build_requirements(shot, _script(), reference_mode="image",
                              model_id="seedance-1.0-pro")
    assert "fragment_event_loss" in {i["code"] for i in reqs["issues"]}
    assert reqs["audio"]["continuity"]["same_semantic_shot"] is True
    assert reqs["audio"]["continuity"]["fragment_ids"]


def test_failure_routing_is_four_way():
    assert classify_failure("role_unsupported")["route"] == "video_execution_prepare"
    assert classify_failure("action_missing")["route"] == "candidate_failed"
    assert classify_failure("causal_gap")["route"] == "step01"
    assert classify_failure("submission_unknown")["route"] == "task_resume"
    assert classify_failure("timeout")["resume_same_task"] is True
    assert classify_failure("unknown_thing")["route"] == "video_execution_prepare"


@pytest.fixture()
def _stub_env(monkeypatch, tmp_path):
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    monkeypatch.setenv("VIDEO_PROVIDER", "stub")
    monkeypatch.setenv("VIDEO_CREDENTIAL_PROFILE_ID", "profile-ci")
    monkeypatch.setenv("VIDEO_CREDENTIAL_REVISION", "r1")
    monkeypatch.setenv("NOVEL_CHAPTER", "1")
    monkeypatch.setattr(sp, "INITIAL_POLL_DELAY", 0)
    monkeypatch.setattr(sp, "POLL_BASE_INTERVAL", 0)
    monkeypatch.setattr(sp, "POLL_MAX_INTERVAL", 0)
    monkeypatch.setattr(sp, "POLL_JITTER", 0)


def _context(experiment_id: str) -> eio.CandidateContext:
    import uuid
    ctx = eio.CandidateContext(
        experiment_id=f"{experiment_id}{uuid.uuid4().hex[:8]}", package="video",
        attempt_id="a-s04",
        chapter=1, scope={"chapter": 1}, script_revision="rev-s04",
        source="video_s04_stub")
    ctx.prepare()
    return ctx


def _frames(root: Path, index: int = 3) -> list:
    d = root / str(index)
    d.mkdir(parents=True, exist_ok=True)
    (d / "first_frame.png").write_bytes(b"ff-s04" + b"\0" * 32)
    (d / "last_frame.png").write_bytes(b"lf-s04" + b"\0" * 32)
    return [str(d / "first_frame.png"), str(d / "last_frame.png")]


def test_stub_response_round_trip_preserves_execution_facts(_stub_env, tmp_path,
                                                           monkeypatch):
    # 本机 WorkBuddy safe-delete shim 在 attempt 锁释放时无法创建模板锁目录；
    # 这是环境限制，不是候选流程行为。测试只跳过锁的物理清理（tmp 目录自回收），
    # 不跳过任何身份/摘要/采纳校验。
    monkeypatch.setattr(eio.AttemptLock, "acquire", lambda self: self)
    monkeypatch.setattr(eio.AttemptLock, "release", lambda self: None)
    ctx = _context("s04stub")
    shot = _shot()
    script = _script()
    frames = _frames(tmp_path / "frames")
    service = StubVideoService()
    reqs = build_requirements(shot, script, reference_mode="image",
                              frame_paths=frames, model_id="stub-model-1")

    async def _go():
        task = await sp._submit_video_candidate(
            shot=shot, shot_dir=ctx.shot_dir(3), video_path=ctx.shot_dir(3) / "video.mp4",
            frame_paths=frames, shot_hash="h-s04", video_adapter=StubVideoAdapter(service),
            poll_semaphore=asyncio.Semaphore(1), script=script, idx=3,
            prompt=str(shot.get("prompt") or ""), characters=[], continuity_ref_image=None,
            mode="A", candidate_context=ctx, logger=None, make_request=pb._make_request,
            execution_requirements=reqs)
        return await task if task is not None else None

    assert asyncio.run(_go()) is True
    assert len(service.posts) == 1
    frozen_saved = eio.read_inputs(ctx.attempt_dir)["frozen_request"]
    assert frozen_saved["requirements"]["execution"]["schema"] == SCHEMA
    assert frozen_saved["source"]["semantic_shot"]["shot_index"] == 3
    assert eio.read_accepted(ctx.attempt_dir) is not None

    # 重启后可恢复：accepted 不被重复生成，执行事实仍来自同一 manifest。
    from step02_video_gen.submit_poll import resume_candidate_attempt
    before = len(service.posts)
    resumed = resume_candidate_attempt(ctx.attempt_dir, service=service)
    assert resumed.get("status") == "accepted"
    assert len(service.posts) == before

    # 消费侧按 accepted 指针 + unit_id 解析，不按路径/位置猜身份。
    resolved = resolve_candidate_inputs(
        ctx.attempt_dir / "manifest.json", expect_scope={"chapter": 1},
        expect_script_revision="rev-s04", required_checks=())
    units = resolved["units"]
    assert units
    unit = next(iter(units.values()))
    assert unit["video_path"] and Path(unit["video_path"]).is_file()
