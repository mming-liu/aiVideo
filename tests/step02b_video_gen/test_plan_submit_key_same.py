# -*- coding: utf-8 -*-
"""S02-C1：plan-only 与提交入口的 request_key 必须同源（2026-11-17）.

两个入口分别取 key：
- 规划：``step02b_video_gen.main.build_plan``（plan-only 的生产函数）；
- 提交：``step02_video_gen.submit_poll._submit_video_candidate``（正式提交入口），
  用预算账本把 POST 挡在发送前，读取 attempt 里冻结的 ``frozen_request``。

除 key 外还核对 ``source.scope``、``script_revision`` 与执行要求；差异必须定位到
具体字段，不能只比一个 hash。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from shared import enhancement_io as eio
from shared.paths import chapter_dir
from step02_video_gen import prompt_builder as pb
from step02_video_gen import submit_poll as sp
from step02b_video_gen import main as m

SHOT_INDEX = 1
MODEL = "doubao-seedance-1-0-pro-250528"
SCOPE = {"chapter": 1, "book_index": 1, "book_name": ""}


def _shot() -> dict:
    return {"index": SHOT_INDEX, "prompt": "[中景] 甲站在船头",
            "ff_desc": "甲站在船头", "lf_desc": "甲转身离开",
            "motion_desc": "甲转身", "duration_sec": 5.0,
            "subtitle": "", "narration_segment": "", "keyframes": []}


def _script(shot: dict) -> dict:
    return {"characters": [], "shots": [shot]}


def _visual_plan(shot: dict) -> dict:
    return {"shots": [{"shot_index": SHOT_INDEX, "status": "ready", "frames": [],
                       "visible_characters": [], "offscreen_speakers": [],
                       "execution": {"unit": shot}}]}


def _frames(shots_dir: Path) -> list[str]:
    d = shots_dir / str(SHOT_INDEX)
    d.mkdir(parents=True)
    ff, lf = d / "first_frame.png", d / "last_frame.png"
    Image.new("RGB", (64, 64), (12, 24, 36)).save(ff)
    Image.new("RGB", (64, 64), (200, 30, 40)).save(lf)
    return [str(ff), str(lf)]


def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path / "out"))
    monkeypatch.setenv("NOVEL_NAME", "")
    monkeypatch.setenv("NOVEL_CHAPTER", "1")
    monkeypatch.setenv("BOOK_INDEX", "1")
    monkeypatch.setenv("BOOK_NAME", "")
    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    monkeypatch.setenv("VIDEO_MODEL_VOICE", "1")
    monkeypatch.setenv("MERGE_AUDIO_MODE", "native")
    monkeypatch.setenv("VIDEO_MEDIA_POLICY", "auto")
    monkeypatch.setenv("VOLCENGINE_I2V_MODEL", MODEL)
    monkeypatch.setenv("STEP02B_VIDEO_BUDGET_TOTAL", "1")
    monkeypatch.setattr(sp, "INITIAL_POLL_DELAY", 0)
    monkeypatch.setattr(sp, "POLL_BASE_INTERVAL", 0)
    monkeypatch.setattr(sp, "POLL_MAX_INTERVAL", 0)
    monkeypatch.setattr(sp, "POLL_JITTER", 0)


def test_plan_and_submit_request_key_same(tmp_path, monkeypatch):
    _env(monkeypatch, tmp_path)
    shots_dir = tmp_path / "shots"
    frame_paths = _frames(shots_dir)
    shot, script = _shot(), _script(_shot())
    rev = sp._script_revision(script)
    visual = _visual_plan(shot)
    reqs = m._execution_requirements(shot, script, visual, frame_paths, MODEL)

    plan = m.build_plan(script, shots_dir, only_indices={SHOT_INDEX}, chapter=1,
                        provider="volcengine", model_id=MODEL, visual_plan=visual,
                        scope=SCOPE)
    entry = plan["shots"][0]
    plan_key = entry.get("request_key")
    assert plan_key, entry

    # 提交入口：预算账本 used=limit ⇒ create_prepared_task 在 POST 前 fail-closed。
    ledger = tmp_path / "video_budget.json"
    ledger.write_text(json.dumps({"kind": "video", "used": 1, "limit": 1}),
                      encoding="utf-8")
    monkeypatch.setenv("STEP02B_VIDEO_BUDGET_FILE", str(ledger))

    ctx = eio.CandidateContext(experiment_id="keytest", package="video",
                               attempt_id="a-keytest", chapter=1, scope=SCOPE,
                               script_revision=rev, source="test")
    ctx.prepare()
    shot_dir = ctx.shot_dir(SHOT_INDEX)
    shot_dir.mkdir(parents=True, exist_ok=True)
    adapter = SimpleNamespace(service=__import__("_video_gen").create_service("volcengine"))
    logger = SimpleNamespace(verbose=lambda *a, **k: None,
                             info=lambda *a, **k: None,
                             warn=lambda *a, **k: None,
                             error=lambda *a, **k: None)

    async def _go():
        try:
            await sp._submit_video_candidate(
                shot=shot, shot_dir=shot_dir, video_path=shot_dir / "video.mp4",
                frame_paths=frame_paths, shot_hash="h-key", video_adapter=adapter,
                poll_semaphore=asyncio.Semaphore(1), script=script, idx=SHOT_INDEX,
                prompt=pb._build_prompt(shot, script), characters=[],
                continuity_ref_image=None, mode="A", candidate_context=ctx,
                logger=logger, make_request=pb._make_request,
                execution_requirements=reqs)
        except Exception as exc:  # noqa: BLE001 - 预算阻断是预期边界
            return exc
        return None

    exc = asyncio.run(_go())
    inputs = eio.read_inputs(ctx.attempt_dir) or {}
    records = sp._failed_videos.get(SHOT_INDEX) or []
    assert any("budget" in str(r.get("code", "")).lower()
               or "预算" in str(r.get("error", "")) for r in records), {
        "failed": records, "exc": repr(exc), "inputs": inputs}
    frozen = inputs.get("frozen_request") or {}
    submit_key = frozen.get("request_key")
    assert submit_key, inputs

    assert submit_key == plan_key, {
        "plan": plan_key, "submit": submit_key,
        "plan_scope": (entry.get("request") or {}).get("source", {}).get("scope"),
        "submit_scope": frozen.get("source", {}).get("scope"),
        "plan_revision": (entry.get("request") or {}).get("source", {}).get("script_revision"),
        "submit_revision": frozen.get("source", {}).get("script_revision"),
    }
    assert frozen["source"]["scope"] == SCOPE
    assert frozen["source"]["script_revision"] == rev
