# -*- coding: utf-8 -*-
"""S02 (V2 + V4): Non-duplicating task recovery & Step02b HTTP metrics ledger."""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from _video_gen.models import (
    MaterialRef,
    PreparedVideoRequest,
    SUBMIT_UNKNOWN,
    VideoGenerationRequest,
    VideoGenerationResult,
    VideoSubmissionError,
)
from shared import enhancement_io as eio
from shared import request_events as req_ev
from shared import video_attempts as va
from step02_video_gen import submit_poll
from tests._content_binding import assert_clean
from tests.integration.downstream_fixtures import (
    build_anonymous_story_pack,
    install_committed_chapter,
    make_test_mp4,
    make_test_png,
)
from tools.step02b_metrics_report import summarize_step02b_events

REPO_ROOT = Path(__file__).resolve().parents[2]


class _StubService:
    provider = "volcengine"
    model_id = "doubao-seedance-1-0-pro-250528"

    def __init__(self, create_handler=None, *, allow_binding: bool = True):
        self.create_handler = create_handler
        self.allow_binding = allow_binding
        self.created_keys: list[str] = []
        self._seq = 0

    def supports_prepared(self) -> bool:
        return True

    def prepare_request(self, req: VideoGenerationRequest, context=None) -> PreparedVideoRequest:
        mats = []
        if req.image_path:
            mats.append(MaterialRef(role="first_frame", kind="image", source_path=req.image_path,
                                    sha256="a" * 64, bytes=128, ref_id="r1"))
        p = PreparedVideoRequest(
            request_id=f"req-stub-{len(self.created_keys) + 1}",
            provider=self.provider,
            model_id=self.model_id,
            mode="image" if mats else "text",
            media_mode="first" if mats else "none",
            prompt=req.prompt,
            parameters={"duration": int(req.duration or 5), "resolution": "720p", "ratio": "9:16"},
            materials=mats,
            credential_profile_ref="env:VOLCENGINE_API_KEY#slot0",
            key_slot=0,
            capability_revision="caps-test-1",
            source={"shot_index": int(req.clip_index), "media_policy": "first_only"},
        )
        p.compute_content_key()
        return p

    def create_prepared_task(self, prepared: PreparedVideoRequest, context=None) -> str:
        self._seq += 1
        self.created_keys.append(str(prepared.request_key or ""))
        if self.create_handler is not None:
            return self.create_handler(prepared, context, self._seq)
        return f"task_stub_{self._seq}"

    def restore_task_binding(self, task_id: str, binding: dict) -> bool:
        return self.allow_binding


class _StubAdapter:
    def __init__(self, service: _StubService, fetch_handler):
        self._svc = service
        self.service = service
        self.fetch_handler = fetch_handler
        self.fetched: list[str] = []

    def get_key_index(self) -> int:
        return 0

    def get_task_binding(self, task_id: str) -> dict:
        return {"status": "ok", "key_slot": 0, "provider": self._svc.provider}

    async def fetch_result(self, req: VideoGenerationRequest, task_id: str) -> VideoGenerationResult:
        self.fetched.append(task_id)
        return self.fetch_handler(req, task_id)


def _setup_chapter(tmp_path: Path, monkeypatch, *, chapter: int = 4):
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path / "output"))
    monkeypatch.setenv("NOVEL_NAME", "匿名作品")
    monkeypatch.setenv("BOOK_INDEX", "1")
    monkeypatch.setenv("BOOK_NAME", "测试卷")
    monkeypatch.setenv("NOVEL_CHAPTER", str(chapter))
    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    monkeypatch.setenv("VIDEO_MEDIA_POLICY", "first_only")
    monkeypatch.setenv("VIDEO_QC_ENABLED", "0")
    monkeypatch.setattr(submit_poll, "INITIAL_POLL_DELAY", 0.0)
    monkeypatch.setattr(submit_poll, "POLL_BASE_INTERVAL", 0.01)

    pack = build_anonymous_story_pack("compact", chapter=chapter)
    ch_dir = tmp_path / "output" / "匿名作品" / "part_01_测试卷" / f"chapter_{chapter:03d}"
    installed = install_committed_chapter(ch_dir, pack)
    ff = make_test_png(ch_dir / "shots" / "1" / "first_frame.png")
    good_mp4 = make_test_mp4(tmp_path / "valid_5s.mp4", 5.0)
    return ch_dir, installed, ff, good_mp4


def _make_cand_ctx(chapter: int, aid: str, scope: dict, revision: str):
    return eio.CandidateContext(
        experiment_id="exp_s02",
        package="video",
        attempt_id=aid,
        chapter=chapter,
        scope=scope,
        script_revision=revision,
        source="test_s02",
    )


def test_s02_interrupt_during_poll_recovers_same_task_zero_new_posts(tmp_path, monkeypatch):
    ch_dir, installed, ff, good_mp4 = _setup_chapter(tmp_path, monkeypatch, chapter=4)
    script = installed["script"]
    shot = script["shots"][0]
    scope = {"chapter": 4, "book_index": 1, "work_id": "匿名作品"}
    submit_poll._failed_videos.clear()

    svc = _StubService()
    mode_state = {"phase": "crash"}

    def _fetch(req, tid):
        if mode_state["phase"] == "crash":
            raise RuntimeError("simulated restart during poll")
        out = Path(req.candidate_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(good_mp4.read_bytes())
        return VideoGenerationResult(clip_index=req.clip_index, success=True, save_path=str(out))

    adapter = _StubAdapter(svc, _fetch)
    ev_file = ch_dir / "_enhancement" / "exp_s02" / "video" / "logs" / "step02b_events.jsonl"
    monkeypatch.setenv("STEP02B_EVENTS_FILE", str(ev_file))

    async def _run_once(aid: str):
        ctx = _make_cand_ctx(4, aid, scope, installed["revision"]).prepare()
        shot_dir = ctx.shot_dir(1)
        video_path = shot_dir / "video.mp4"
        task = await submit_poll._submit_video(
            shot, shot_dir, video_path, [str(ff)], "hash1", adapter,
            asyncio.Semaphore(1), script, 1, candidate_context=ctx,
        )
        if task is not None:
            return await task
        return False

    # Run 1: submits once, poll crashes cleanly, attempt preserved in non-terminal state with task_id
    res1 = asyncio.run(_run_once("shot01-run1"))
    assert res1 is False
    pkg_root = eio.CandidateContext("exp_s02", "video", "shot01-run1", chapter=4).package_root
    assert len(svc.created_keys) == 1
    assert va.submit_post_count(pkg_root) == 1

    # Run 2: new attempt ID triggers automatic recovery WITHOUT emitting any second POST
    submit_poll._failed_videos.clear()
    mode_state["phase"] = "ok"
    res2 = asyncio.run(_run_once("shot01-run2"))
    assert res2 is True
    assert len(svc.created_keys) == 1
    assert va.submit_post_count(pkg_root) == 1
    gens = va.generations(pkg_root)
    assert len(gens) == 1
    only_gen = next(iter(gens.values()))
    assert only_gen["submit_posts"] == 1
    assert "shot01-run1" in only_gen["attempts"] and "shot01-run2" in only_gen["attempts"]
    assert va.current_state(pkg_root / "attempts" / "shot01-run2") == va.STATE_ACCEPTED

    # Run 3: after STATE_ACCEPTED is completed, explicit re-run starts a distinct generation
    submit_poll._failed_videos.clear()
    res3 = asyncio.run(_run_once("shot01-run3"))
    assert res3 is True
    assert len(svc.created_keys) == 2
    assert va.submit_post_count(pkg_root) == 2


def test_s02_submission_unknown_without_id_blocks_and_late_id_recovers(tmp_path, monkeypatch):
    ch_dir, installed, ff, good_mp4 = _setup_chapter(tmp_path, monkeypatch, chapter=5)
    script = installed["script"]
    shot = script["shots"][0]
    scope = {"chapter": 5, "book_index": 1, "work_id": "匿名作品"}
    ev_file = ch_dir / "_enhancement" / "exp_s02" / "video" / "logs" / "step02b_events.jsonl"
    monkeypatch.setenv("STEP02B_EVENTS_FILE", str(ev_file))

    def _submit_unknown(prepared, ctx, seq):
        raise VideoSubmissionError(SUBMIT_UNKNOWN, "network timeout after connect")

    svc = _StubService(_submit_unknown)

    def _fetch(req, tid):
        out = Path(req.candidate_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(good_mp4.read_bytes())
        return VideoGenerationResult(clip_index=req.clip_index, success=True, save_path=str(out))

    adapter = _StubAdapter(svc, _fetch)
    submit_poll._failed_videos.clear()

    async def _run_once(aid: str):
        ctx = _make_cand_ctx(5, aid, scope, installed["revision"]).prepare()
        shot_dir = ctx.shot_dir(1)
        video_path = shot_dir / "video.mp4"
        t = await submit_poll._submit_video(
            shot, shot_dir, video_path, [str(ff)], "hash1", adapter,
            asyncio.Semaphore(1), script, 1, candidate_context=ctx,
        )
        return await t if t is not None else None

    # 1) Network uncertainty leaves submission_unknown WITHOUT task_id
    assert asyncio.run(_run_once("shot01-unk1")) is None
    assert len(svc.created_keys) == 1

    # 2) Second normal run MUST stay submission_unknown and send 0 new POSTs
    submit_poll._failed_videos.clear()
    svc.create_handler = None
    assert asyncio.run(_run_once("shot01-unk2")) is None
    assert len(svc.created_keys) == 1
    assert any(it.get("code") == "submission_unknown" for it in submit_poll._failed_videos.get(1, []))

    # 3) Late callback writes late_task_id onto unk1 -> third run recovers WITHOUT new POST
    ctx1 = _make_cand_ctx(5, "shot01-unk1", scope, installed["revision"])
    eio.append_event(ctx1.attempt_dir, "late_task_id", task_id="task_late_55",
                     generation_id=(eio.read_inputs(ctx1.attempt_dir) or {}).get("generation_id"))
    submit_poll._failed_videos.clear()
    assert asyncio.run(_run_once("shot01-unk3")) is True
    assert len(svc.created_keys) == 1
    assert "task_late_55" in adapter.fetched


def test_s02_production_mode_persist_failure_and_recovery(tmp_path, monkeypatch):
    ch_dir, installed, ff, good_mp4 = _setup_chapter(tmp_path, monkeypatch, chapter=6)
    script = installed["script"]
    shot = script["shots"][0]
    shot_dir = ch_dir / "shots" / "1"
    video_path = shot_dir / "video.mp4"
    monkeypatch.setenv("STEP02B_EVENTS_FILE", str(ch_dir / "step02b_events.jsonl"))

    svc = _StubService()

    def _fetch(req, tid):
        p = Path(str(req.candidate_path or video_path))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(good_mp4.read_bytes())
        return VideoGenerationResult(clip_index=req.clip_index, success=True, save_path=str(p))

    adapter = _StubAdapter(svc, _fetch)
    submit_poll._failed_videos.clear()

    # Helper that calls _submit_video and awaits any returned poll Task within the SAME event loop.
    async def _submit_and_wait():
        t = await submit_poll._submit_video(
            shot, shot_dir, video_path, [str(ff)], "h1", adapter,
            asyncio.Semaphore(1), script, 1,
        )
        if t is not None:
            return await t
        return None

    # Simulate DB/disk write error right when persisting STATE_SUBMITTED after POST succeeded
    real_transition = va.transition

    def _broken_transition(attempt, to_state, **kwargs):
        if to_state == va.STATE_SUBMITTED:
            raise OSError("disk full saving submitted state")
        return real_transition(attempt, to_state, **kwargs)

    monkeypatch.setattr(va, "transition", _broken_transition)
    res1 = asyncio.run(_submit_and_wait())
    assert res1 is None
    assert len(svc.created_keys) == 1

    # Now restore transition: _persist_late captured task_stub_1 via late_task_id even
    # when STATE_SUBMITTED write failed -> next run automatically recovers task_stub_1!
    monkeypatch.setattr(va, "transition", real_transition)
    submit_poll._failed_videos.clear()
    assert asyncio.run(_submit_and_wait()) is True
    assert len(svc.created_keys) == 1
    assert (ch_dir / "clips" / "clip_1.mp4").is_file()

    # Also prove crash in STATE_SUBMITTING WITHOUT any saved task_id blocks duplicate POST:
    prod_root = ch_dir / "shots" / "1" / "video_attempts"
    from step02_video_gen.prompt_builder import _build_prompt, _make_request
    _req_crash = _make_request(adapter, _build_prompt(shot, script, mode="A"), [str(ff)], shot,
                               characters=script.get("characters", []), mode="A")
    _prep_crash = svc.prepare_request(_req_crash)
    _frozen_crash, _ = submit_poll.build_frozen_for_shot(
        shot=shot, script=script, video_adapter=adapter, frame_paths=[str(ff)],
        mode="A", provider="volcengine", model_id=svc.model_id, prepared=_prep_crash,
    )
    att_no_id = va.open_attempt(
        prod_root,
        va.new_attempt_id("prod_crash"),
        request_key=_frozen_crash.request_key,
        scope=submit_poll._scope_identity_safe(),
        script_revision=installed["revision"],
        provider="volcengine",
        model_id=svc.model_id,
        generation_id="gen-crash-noid",
    )
    eio.freeze_inputs(att_no_id, {**(eio.read_inputs(att_no_id) or {}), "shot_index": 1})
    va.transition(att_no_id, va.STATE_READY)
    va.transition(att_no_id, va.STATE_SUBMITTING)
    submit_poll._failed_videos.clear()
    res3 = asyncio.run(_submit_and_wait())
    assert res3 is None
    assert len(svc.created_keys) == 1
    assert any(it.get("code") == "submission_unknown" for it in submit_poll._failed_videos.get(1, []))


def test_s02_credential_binding_unavailable_blocks_without_new_post(tmp_path, monkeypatch):
    ch_dir, installed, ff, _ = _setup_chapter(tmp_path, monkeypatch, chapter=7)
    script = installed["script"]
    shot = script["shots"][0]
    scope = {"chapter": 7, "book_index": 1, "work_id": "匿名作品"}
    monkeypatch.setenv("STEP02B_EVENTS_FILE", str(ch_dir / "events.jsonl"))

    svc1 = _StubService()
    adapter1 = _StubAdapter(svc1, lambda req, tid: (_ for _ in ()).throw(RuntimeError("crash")))
    submit_poll._failed_videos.clear()

    async def _run(svc_inst, adp_inst, aid):
        ctx = _make_cand_ctx(7, aid, scope, installed["revision"]).prepare()
        t = await submit_poll._submit_video(
            shot, ctx.shot_dir(1), ctx.shot_dir(1) / "video.mp4", [str(ff)], "h1",
            adp_inst, asyncio.Semaphore(1), script, 1, candidate_context=ctx,
        )
        return await t if t is not None else None

    assert asyncio.run(_run(svc1, adapter1, "shot01-b1")) is False

    svc2 = _StubService(allow_binding=False)
    adapter2 = _StubAdapter(svc2, lambda req, tid: VideoGenerationResult(1, True, save_path=str(ff)))
    submit_poll._failed_videos.clear()
    assert asyncio.run(_run(svc2, adapter2, "shot01-b2")) is None
    assert len(svc2.created_keys) == 0
    assert any(it.get("code") == "submission_unknown" for it in submit_poll._failed_videos.get(1, []))


def test_s02_provider_http_attempt_and_metrics_report_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path / "out"))
    monkeypatch.setenv("NOVEL_NAME", "匿名作品")
    monkeypatch.setenv("VOLCENGINE_API_KEY", "test-volc-key")
    monkeypatch.setenv("AGENT_PLAN_API_KEY", "test-ap-key")

    from _video_gen import agent_plan_provider, volcengine_provider
    from _video_gen.agent_plan_provider import AgentPlanVideoService
    from _video_gen.volcengine_provider import VolcengineVideoService

    monkeypatch.setattr(volcengine_provider, "VOLCENGINE_API_KEY", "test-volc-key")
    monkeypatch.setattr(agent_plan_provider, "AGENT_PLAN_API_KEY", "test-ap-key")
    monkeypatch.setattr(volcengine_provider.config, "validate", lambda: True)
    monkeypatch.setattr(agent_plan_provider.config, "validate", lambda: True)

    ev_path = tmp_path / "cand_logs" / "step02b_events.jsonl"
    monkeypatch.setenv("STEP02B_EVENTS_FILE", str(ev_path))

    volc = VolcengineVideoService()
    ap = AgentPlanVideoService()
    prep_v = PreparedVideoRequest(
        request_id="req-v-1", provider="volcengine", model_id="doubao-seedance-1-0-pro-250528",
        mode="text", media_mode="none", prompt="test", parameters={"duration": 5},
        request_key="rk-v1", content_key="ck-v1",
        source={"generation_id": "gen-v1", "shot_index": 1, "media_policy": "auto",
                "scope": {"work_id": "匿名作品", "chapter_scope": "part_01/chapter_001"},
                "script_revision": "rev1"},
    )
    prep_a = PreparedVideoRequest(
        request_id="req-a-1", provider="agent_plan", model_id="doubao-seedance-1-5-pro-251215",
        mode="text", media_mode="first", prompt="test2", parameters={"duration": 5},
        request_key="rk-a1", content_key="ck-a1",
        source={"generation_id": "gen-a1", "shot_index": 2, "media_policy": "first_only",
                "scope": {"work_id": "匿名作品", "chapter_scope": "part_01/chapter_001"},
                "script_revision": "rev1"},
    )
    ok_resp = SimpleNamespace(status_code=200, text='{"id":"t-1"}', json=lambda: {"id": "t-1"})
    with mock.patch("_video_gen.volcengine_provider.requests.post", return_value=ok_resp), \
            mock.patch("_video_gen.agent_plan_provider.requests.post", return_value=ok_resp):
        assert volc.create_prepared_task(prep_v) == "t-1"
        assert ap.create_prepared_task(prep_a) == "t-1"

    req_ev.emit_video_decision_ledger(
        work_id="匿名作品", chapter_scope="part_01/chapter_001", script_revision="rev1",
        shot_index=1, provider="volcengine", model_id="doubao-seedance-1-0-pro-250528",
        media_policy="auto", generation_id="gen-v1", task_id="t-1", attempt_id="att-1",
        action_type="recover", outcome="accepted", recovered=True, submit_posts=1,
    )
    req_ev.emit_video_decision_ledger(
        work_id="匿名作品", chapter_scope="part_01/chapter_001", script_revision="rev1",
        shot_index=2, provider="agent_plan", model_id="doubao-seedance-1-5-pro-251215",
        media_policy="first_only", generation_id="gen-a1", task_id="t-2", attempt_id="att-2",
        action_type="generate", outcome="submission_unknown", error_code="submission_unknown",
        submit_posts=1,
    )
    req_ev.emit_video_decision_ledger(
        work_id="匿名作品", chapter_scope="part_01/chapter_001", script_revision="rev1",
        shot_index=3, provider="volcengine", model_id="doubao-seedance-1-0-pro-250528",
        media_policy="auto", generation_id="gen-v3", task_id="t-3", attempt_id="att-3",
        action_type="generate", outcome="provider_failed", error_code="provider_failed",
        submit_posts=1,
    )

    events = req_ev.read_events(ev_path)
    summary = summarize_step02b_events(events)
    assert summary["http_attempts_total"] == 2
    assert summary["usable_clips_total"] == 1
    assert summary["http_attempts_per_usable_clip"] == 2.0
    assert summary["recovered_shots_total"] == 1
    assert summary["task_recovery_rate"] == 1.0
    assert summary["failed_count"] == 1
    assert summary["unfinished_count"] == 1
    assert summary["failed_shots"][0]["shot_key"] == ["匿名作品", "part_01/chapter_001", "rev1", 3]
    assert summary["unfinished_shots"][0]["shot_key"] == ["匿名作品", "part_01/chapter_001", "rev1", 2]

    proc = subprocess.run(
        [sys.executable, "tools/step02b_metrics_report.py", str(ev_path), "--json"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0
    cli_data = json.loads(proc.stdout)
    assert cli_data["http_attempts_total"] == 2
    assert cli_data["unfinished_count"] == 1


def test_s02_files_content_agnostic():
    for rel in (
        "tools/step02b_metrics_report.py",
        "tests/step02b_video_gen/test_s02_recovery_and_metrics.py",
    ):
        assert_clean((REPO_ROOT / rel).read_text(encoding="utf-8"), rel)
