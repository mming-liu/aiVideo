"""Offline production-entry journal checks; no model or media quality claims."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from _video_gen.models import PreparedVideoRequest, VideoGenerationRequest, VideoSubmissionError, SUBMIT_UNKNOWN
from shared import enhancement_io as eio
from shared import video_attempts as va
from step02_video_gen import prompt_builder as pb, submit_poll as sp


@pytest.fixture(autouse=True)
def offline():
    from tools.probe_isolation import block_external_transport
    with block_external_transport():
        yield


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path / "output"))
    monkeypatch.setenv("NOVEL_NAME", "fixture")
    monkeypatch.setenv("BOOK_NAME", "")
    monkeypatch.setattr(sp, "INITIAL_POLL_DELAY", 0)
    monkeypatch.setattr(sp, "VIDEO_SUBMIT_RETRIES", 1)
    monkeypatch.setattr(sp, "_probe_video_duration", lambda p: 6)
    monkeypatch.setattr(sp, "VIDEO_QC_ENABLED", False)
    monkeypatch.setattr(pb, "_build_prompt", lambda *a, **k: "A traveler opens a door")
    monkeypatch.setattr(pb, "_make_request", lambda *a, **k: VideoGenerationRequest(
        prompt="A traveler opens a door", clip_index=17, duration=6, seed=123))
    sp._failed_videos.clear()
    source = tmp_path / "download.mp4"
    source.write_bytes(b"v" * 30000)

    class Service:
        posts = 0
        error = None
        def supports_prepared(self):
            return True
        def prepare_request(self, req, context=None):
            p = PreparedVideoRequest(request_id="fixture", provider="stub", model_id="stub-v1",
                mode="text", prompt=req.prompt, parameters={"duration": 6})
            p.compute_content_key()
            return p
        def create_prepared_task(self, prepared, context=None):
            self.posts += 1
            if self.error:
                raise self.error
            return "task-17"

    service = Service()
    class Adapter:
        def __init__(self):
            self.service = service
        async def submit_task(self, req):
            raise AssertionError("legacy submit must not run")
        def get_task_binding(self, tid):
            return {"status": "binding_unavailable"}
        async def fetch_result(self, req, tid):
            assert tid == "task-17"
            return SimpleNamespace(success=True, save_path=source, error=None)
    shot_dir = tmp_path / "shot"
    async def run():
        task = await sp._submit_video({"index": 17}, shot_dir, shot_dir / "video.mp4", [],
            "hash", Adapter(), asyncio.Semaphore(1), {"characters": []}, 17)
        return await task if task else False
    return SimpleNamespace(run=run, service=service, shot_dir=shot_dir, adapter=Adapter())


def records(case):
    attempts = list((case.shot_dir / "video_attempts" / "attempts").iterdir())
    assert len(attempts) == 1
    events, tail = eio.read_events_strict(attempts[0])
    assert tail is None
    return eio.read_inputs(attempts[0]), events


def test_success_persists_actual_request_and_task(case):
    assert asyncio.run(case.run())
    inputs, events = records(case)
    assert case.service.posts == 1
    assert not sp._failed_videos
    assert "seed" not in inputs["frozen_request"]["parameters"]
    assert inputs["requested_seed"] == 123
    assert any(e.get("task_id") == "task-17" for e in events)
    assert any(e["kind"] == "video_check" for e in events)
    assert events[-1]["kind"] == "production_completed"
    # R2：生产成功必须闭合到终态 accepted（此前停在 checked，非终态）。
    attempt = next((case.shot_dir / "video_attempts" / "attempts").iterdir())
    assert va.current_state(attempt) == va.STATE_ACCEPTED


@pytest.mark.parametrize("error", [VideoSubmissionError(SUBMIT_UNKNOWN, "lost response"), RuntimeError("lost response")])
def test_submit_unknown_never_retries(case, error):
    case.service.error = error
    assert not asyncio.run(case.run())
    _, events = records(case)
    assert case.service.posts == 1
    assert any(e.get("classification") == SUBMIT_UNKNOWN for e in events)


def test_journal_failure_prevents_submit(case, monkeypatch):
    def fail(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(eio, "freeze_inputs", fail)
    assert not asyncio.run(case.run())
    assert case.service.posts == 0


def test_qc_failure_is_recorded_without_resubmit(case, monkeypatch):
    monkeypatch.setattr(sp, "_probe_video_duration", lambda p: 1)
    assert not asyncio.run(case.run())
    _, events = records(case)
    assert case.service.posts == 1
    assert any(e["kind"] == "video_check" and e["qc"]["status"] == "failed" for e in events)
    rejected = next(e["artifact"] for e in events if e["kind"] == "rejected_artifact")
    assert rejected["sha256"]


def test_unsupported_provider_stops_without_legacy_fallback(case, monkeypatch):
    monkeypatch.setattr(case.service, "supports_prepared", lambda: False)
    assert not asyncio.run(case.run())
    _, events = records(case)
    assert case.service.posts == 0
    assert any(e["kind"] == "prepared_unsupported" for e in events)


def test_repeated_runs_have_distinct_attempts(case):
    assert asyncio.run(case.run())
    assert asyncio.run(case.run())
    attempts = list((case.shot_dir / "video_attempts" / "attempts").iterdir())
    inputs = [eio.read_inputs(p) for p in attempts]
    assert len(inputs) == 2
    assert len({i["generation_id"] for i in inputs}) == 2
    assert len({i["request_key"] for i in inputs}) == 1


def test_task_record_write_failure_does_not_resubmit(case, monkeypatch):
    from shared import video_attempts as va
    transition = va.transition
    def fail_submitted(attempt, state, **fields):
        if state == va.STATE_SUBMITTED:
            raise OSError("task record write failed")
        return transition(attempt, state, **fields)
    monkeypatch.setattr(va, "transition", fail_submitted)
    assert not asyncio.run(case.run())
    assert case.service.posts == 1
    # The shared submission worker retains a late ID if the main writer failed.
    import time
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        _, events = records(case)
        if any(e["kind"] == "late_task_id" for e in events):
            break
        time.sleep(0.05)
    assert any(e["kind"] == "late_task_id" and e["task_id"] == "task-17" for e in events)


def test_step02b_entry_keeps_success_and_cache_semantics(case, monkeypatch):
    from step02b_video_gen.main import _process_one_shot_skip_frames
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "text")
    shot = {"index": 17, "duration_sec": 6,
            "ff_desc": "A traveler stands before a closed door",
            "lf_desc": "The traveler steps through the open door",
            "motion_desc": "A traveler opens a door"}
    script = {"characters": [], "shots": [shot]}
    async def run():
        args = (shot, case.shot_dir, case.adapter, None, script, asyncio.Semaphore(1))
        task = await _process_one_shot_skip_frames(*args)
        assert task is not None
        assert await task
        assert await _process_one_shot_skip_frames(*args) is None
    asyncio.run(run())
    assert case.service.posts == 1
    assert not sp._failed_videos
