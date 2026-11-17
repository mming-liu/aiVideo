# -*- coding: utf-8 -*-
"""O1：缓存哈希与执行要求只纳管策略后实际下发的帧（2026-11-18）.

`first_only` 下改 last_frame / keyframe 的字节不得让 `prompt_hash` 或
`requirements.references` 变化；改 first_frame 必须变化。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from shared.video_media_plan import filter_frame_paths_by_policy
from step02_video_gen import execution_requirements as er
from step02_video_gen import submit_poll as sp

SHOT_INDEX = 1


def _shot() -> dict:
    return {"index": SHOT_INDEX, "prompt": "[中景] 甲站在船头",
            "ff_desc": "甲站在船头", "lf_desc": "甲转身离开",
            "motion_desc": "甲转身", "duration_sec": 5.0,
            "subtitle": "", "narration_segment": "", "keyframes": []}


def _script() -> dict:
    return {"characters": [], "shots": [_shot()]}


def _frames(tmp_path: Path) -> Path:
    d = tmp_path / "shots" / str(SHOT_INDEX)
    d.mkdir(parents=True)
    (d / "first_frame.png").write_bytes(b"ff-1" + b"\0" * 64)
    (d / "last_frame.png").write_bytes(b"lf-1" + b"\0" * 64)
    (d / "keyframe_0.png").write_bytes(b"kf-1" + b"\0" * 64)
    return d


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    monkeypatch.setenv("VOLCENGINE_I2V_MODEL", "doubao-seedance-1-0-pro-250528")
    monkeypatch.setenv("VIDEO_MODEL_VOICE", "1")
    monkeypatch.setenv("VIDEO_MEDIA_POLICY", "first_only")


def test_first_only_hash_ignores_dropped_frames(tmp_path, env):
    shot_dir = _frames(tmp_path)
    base = sp._get_shot_hash(_shot(), _script(), shot_dir=shot_dir)

    (shot_dir / "last_frame.png").write_bytes(b"lf-2" + b"\0" * 64)
    (shot_dir / "keyframe_0.png").write_bytes(b"kf-2" + b"\0" * 64)
    assert sp._get_shot_hash(_shot(), _script(), shot_dir=shot_dir) == base

    (shot_dir / "first_frame.png").write_bytes(b"ff-2" + b"\0" * 64)
    assert sp._get_shot_hash(_shot(), _script(), shot_dir=shot_dir) != base


def test_first_only_requirements_only_first_frame(tmp_path, env):
    shot_dir = _frames(tmp_path)
    paths = [str(shot_dir / n) for n in
             ("first_frame.png", "last_frame.png", "keyframe_0.png")]
    facts = er._reference_facts(paths, "image")
    assert [m["role"] for m in facts["materials"]] == ["first_frame"]
    assert facts["materials"][0]["sha256"]


def test_filter_helper_policies(tmp_path, env):
    paths = [str(tmp_path / n) for n in
             ("first_frame.png", "last_frame.png", "keyframe_0.png")]
    assert [Path(p).name for p in filter_frame_paths_by_policy(paths, "first_only")] == [
        "first_frame.png"]
    assert [Path(p).name for p in filter_frame_paths_by_policy(paths, "first_last")] == [
        "first_frame.png", "last_frame.png"]
    assert [Path(p).name for p in filter_frame_paths_by_policy(paths, "reference")] == [
        "keyframe_0.png"]
