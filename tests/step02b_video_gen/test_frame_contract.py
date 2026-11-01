# -*- coding: utf-8 -*-
"""Image mode must fail when per-shot first/last frames are missing."""
from __future__ import annotations

import asyncio

import pytest


def test_image_mode_missing_first_frame_fails_fast(tmp_path, monkeypatch):
    import step02b_video_gen.main as m

    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    monkeypatch.setattr(m, "_get_shot_hash", lambda shot, script, **kwargs: "hash")

    with pytest.raises(FileNotFoundError, match="缺少首/尾帧"):
        asyncio.run(m._process_one_shot_skip_frames(
            {"index": 2, "prompt": "p"}, tmp_path, object(), None, {},
            asyncio.Semaphore(1),
        ))


def test_shot_hash_changes_when_frame_bytes_change(tmp_path, monkeypatch):
    from step02_video_gen import submit_poll
    import step02_video_gen.prompt_builder as pb

    monkeypatch.setenv("VIDEO_PROVIDER", "bailian")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    monkeypatch.setattr(pb, "_build_prompt", lambda shot, script, mode="A": "same prompt")

    shot = {"index": 2, "prompt": "p", "ff_desc": "ff", "lf_desc": "lf",
            "narration_segment": "", "keyframes": []}
    script = {"characters": []}
    shot_dir = tmp_path / "2"
    shot_dir.mkdir()
    ff = shot_dir / "first_frame.png"
    ff.write_bytes(b"frame-a")
    h1 = submit_poll._get_shot_hash(shot, script, shot_dir=shot_dir)
    ff.write_bytes(b"frame-b")
    h2 = submit_poll._get_shot_hash(shot, script, shot_dir=shot_dir)
    assert h1 != h2


def test_r2v_reference_bytes_invalidate_hash(tmp_path, monkeypatch):
    from step02_video_gen import submit_poll
    import step02_video_gen.prompt_builder as pb

    monkeypatch.setenv("VIDEO_PROVIDER", "bailian")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "r2v")
    monkeypatch.setattr(pb, "_build_prompt", lambda shot, script, mode="A": "same prompt")
    ref = tmp_path / "portrait.png"
    ref.write_bytes(b"portrait-a")
    monkeypatch.setattr(pb, "_collect_r2v_references", lambda *a, **k: [str(ref)])
    monkeypatch.setattr(pb, "_load_character_registry", lambda: {})

    shot = {"index": 2, "prompt": "p", "ff_desc": "ff", "lf_desc": "lf",
            "narration_segment": "", "keyframes": []}
    script = {"characters": []}
    h1 = submit_poll._get_shot_hash(shot, script)
    ref.write_bytes(b"portrait-b")
    h2 = submit_poll._get_shot_hash(shot, script)
    assert h1 != h2


def test_r2v_missing_references_fails_instead_of_text_fallback(monkeypatch):
    import pytest
    import step02_video_gen.prompt_builder as pb

    monkeypatch.setattr(pb, "_REFERENCE_MODE", "r2v")
    monkeypatch.setattr(pb, "_USE_TEXT_TO_VIDEO", False)
    monkeypatch.setattr(pb, "_collect_r2v_references", lambda *a, **k: [])

    class _Adapter:
        pass

    shot = {"index": 2, "ff_desc": "x", "lf_desc": "x", "motion_desc": "x"}
    with pytest.raises(RuntimeError, match="r2v"):
        pb._make_request(_Adapter(), "prompt", [], shot)


def test_shot_hash_changes_when_duration_changes(monkeypatch):
    from step02_video_gen import submit_poll
    import step02_video_gen.prompt_builder as pb

    monkeypatch.setenv("VIDEO_PROVIDER", "bailian")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    monkeypatch.setattr(pb, "_build_prompt", lambda shot, script, mode="A": "same prompt")
    shot = {"index": 2, "prompt": "p", "ff_desc": "ff", "lf_desc": "lf",
            "narration_segment": "", "keyframes": [], "duration_sec": 5.0}
    script = {"characters": []}
    h1 = submit_poll._get_shot_hash(shot, script)
    shot["duration_sec"] = 8.0
    h2 = submit_poll._get_shot_hash(shot, script)
    assert h1 != h2
