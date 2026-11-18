# -*- coding: utf-8 -*-
"""S05：就绪待生成 / 缺帧阻断 / 实际缓存命中（2026-11-18）."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools import cache_bill as cb


def _shot(idx: int, duration: float = 5.0) -> dict:
    return {"index": idx, "duration_sec": duration, "prompt": "[中景] 甲转身",
            "ff_desc": "甲站立", "lf_desc": "甲转身", "motion_desc": "甲转身",
            "main_subject": "甲", "narration_segment": "",
            "frame_layouts": {}, "keyframes": []}


def _plan(shot: int, key: str, policy: str, duration: float) -> dict:
    return {"shots": [{
        "shot_index": shot, "verdict": "ready", "request_key": key,
        "generations": 1,
        "request": {"media_mode": "first" if policy == "first_only" else "first_last",
                    "media_selection": {"policy": policy},
                    "parameters": {"duration": duration},
                    "references": [{"role": "first_frame"}]},
    }]}


def _fixture(tmp_path, monkeypatch):
    ch = tmp_path / "chapter_001"
    (ch / "vimax_working" / "shots" / "1").mkdir(parents=True)
    (ch / "vimax_working" / "shots" / "2").mkdir(parents=True)
    (ch / "vimax_working" / "shots" / "3").mkdir(parents=True)
    script = {"chapter_index": 1, "book_index": 1, "book_name": "book",
              "characters": [], "shots": [_shot(1), _shot(2), _shot(3)]}
    (ch / "script_result.json").write_text(
        json.dumps(script, ensure_ascii=False), encoding="utf-8")
    p1 = tmp_path / "p1.json"; p2 = tmp_path / "p2.json"
    p1.write_text(json.dumps(_plan(1, "k1", "auto", 5.0)), encoding="utf-8")
    p2.write_text(json.dumps(_plan(2, "k2", "first_only", 7.0)), encoding="utf-8")
    table = tmp_path / "table.json"
    table.write_text(json.dumps({"rows": [
        {"shot": 1, "status": "present"},
        {"shot": 2, "status": "present"},
        {"shot": 3, "status": "missing"},
    ]}), encoding="utf-8")
    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    monkeypatch.setenv("VOLCENGINE_I2V_MODEL", "doubao-seedance-1-0-pro-250528")
    monkeypatch.setenv("VIDEO_MEDIA_POLICY", "auto")
    return ch, p1, p2, table, script


def test_bill_splits_ready_pending_and_blocked(tmp_path, monkeypatch):
    ch, p1, p2, table, _script = _fixture(tmp_path, monkeypatch)
    bill = cb.build_bill(chapter=ch, global_dir=tmp_path / "global",
                         shot1_plan=p1, shot2_plan=p2, frame_table=table)
    assert bill["cache"]["ready_pending"] == [1, 2]
    assert bill["cache"]["blocked_missing_frames"] == [3]
    assert bill["cache"]["hits"] == 0
    assert bill["ready_generations"] == 2
    assert bill["ready_duration_sec"] == 12
    assert bill["policies"] == {"1": "auto", "2": "first_only"}
    assert bill["cache"]["decisions"]["1"]["relation"] == "absent"


def test_bill_counts_real_cache_hit(tmp_path, monkeypatch):
    from step02_video_gen.submit_poll import _get_shot_hash

    ch, p1, p2, table, script = _fixture(tmp_path, monkeypatch)
    shot_dir = ch / "vimax_working" / "shots" / "1"
    (shot_dir / "video.mp4").write_bytes(b"\0" * (21 * 1024))
    h = _get_shot_hash(script["shots"][0], script, shot_dir=shot_dir,
                       media_policy="auto")
    (shot_dir / "prompt_hash.txt").write_text(h, encoding="utf-8")
    (shot_dir / "frozen_request.json").write_text(
        json.dumps({"request_key": "k1"}), encoding="utf-8")
    bill = cb.build_bill(chapter=ch, global_dir=tmp_path / "global",
                         shot1_plan=p1, shot2_plan=p2, frame_table=table)
    assert bill["cache"]["hits"] == 1
    assert bill["cache"]["hit_shots"] == [1]
    assert bill["cache"]["ready_pending"] == [2]
    assert bill["ready_generations"] == 1
    assert bill["cache"]["decisions"]["1"]["relation"] == "exact"
