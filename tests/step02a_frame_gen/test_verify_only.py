# -*- coding: utf-8 -*-
"""O2：零生成产品验收重建 + 逐镜策略门禁（2026-11-18）."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from step02a_frame_gen import verify_acceptance as va
from step02b_video_gen import main as m
from shared.quality_gate import QualityGateError


def _shot(idx: int, *, narration: str = "") -> dict:
    return {"index": idx, "duration_sec": 5.0, "main_subject": "甲",
            "ff_desc": "甲站立", "lf_desc": "甲转身", "motion_desc": "甲转身",
            "prompt": "[中景] 甲转身", "narration_segment": narration,
            "scene": "院内", "location": "院中", "clip_transition": "硬切",
            "context_bridge": "", "frame_layouts": {}, "keyframes": []}


def _script() -> dict:
    return {"title": "t", "shots": [_shot(1), _shot(2)], "characters": [],
            "hook": None, "chapter_index": 1, "book_index": 1, "book_name": "book",
            "total_duration": 10.0,
            "chapter_outline": {"narration_lines": []}}


def _frame(path: Path, color=(10, 20, 30)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), color).save(path)


def _fixture(tmp_path, monkeypatch):
    from shared.image_qc import QcResult, build_qc_frame_meta, write_frame_request_snapshot, write_sidecar

    ch = tmp_path / "chapter_001"
    work = ch / "vimax_working"
    ch.mkdir(parents=True)
    script = _script()
    (ch / "script_result.json").write_text(
        json.dumps(script, ensure_ascii=False), encoding="utf-8")
    d1, d2 = work / "shots" / "1", work / "shots" / "2"
    _frame(d1 / "first_frame.png")
    _frame(d1 / "last_frame.png", (200, 30, 40))
    _frame(d2 / "first_frame.png", (30, 200, 40))
    for d, shot in ((d1, script["shots"][0]), (d2, script["shots"][1])):
        (d / "first_frame_qc_log.json").write_text(
            json.dumps([{"ts": "2026-11-18 00:00:00", "has_hallucination": False}]),
            encoding="utf-8")
        write_frame_request_snapshot(
            d / "first_frame.png", shot=shot, frame_kind="first_frame",
            prompt=shot.get("ff_desc", ""), refs=[],
        )
        write_sidecar(
            d / "first_frame.png",
            QcResult(has_hallucination=False, issues=[], ok=True, raw_result=""),
            frame_meta=build_qc_frame_meta(shot, "first_frame", [], frame_path=d / "first_frame.png"),
        )
    (d1 / "last_frame_qc_log.json").write_text(
        json.dumps([{"ts": "2026-11-18 00:00:00", "has_hallucination": False}]),
        encoding="utf-8")
    write_frame_request_snapshot(
        d1 / "last_frame.png", shot=script["shots"][0], frame_kind="last_frame",
        prompt=script["shots"][0].get("lf_desc", ""), refs=[],
    )
    write_sidecar(
        d1 / "last_frame.png",
        QcResult(has_hallucination=False, issues=[], ok=True, raw_result=""),
        frame_meta=build_qc_frame_meta(script["shots"][0], "last_frame", [], frame_path=d1 / "last_frame.png"),
    )
    (d1 / ".lock_frame_first_frame").write_text("", encoding="utf-8")
    (d1 / ".lock_frame_last_frame").write_text("", encoding="utf-8")
    (d2 / ".lock_frame_first_frame").write_text("", encoding="utf-8")
    monkeypatch.setattr(va, "chapter_dir", lambda *a, **k: ch)
    monkeypatch.setenv("NOVEL_CHAPTER", "1")
    monkeypatch.setenv("BOOK_INDEX", "1")
    monkeypatch.setenv("BOOK_NAME", "book")
    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    monkeypatch.setenv("VOLCENGINE_I2V_MODEL", "doubao-seedance-1-0-pro-250528")
    return ch, work


def test_verify_only_merges_per_shot_scope(tmp_path, monkeypatch):
    ch, work = _fixture(tmp_path, monkeypatch)
    code = va.run_verify_only(ch / "script_result.json", work,
                              shots={1, 2}, policies={1: "auto", 2: "first_only"})
    assert code == 0
    report = json.loads((ch / "step02a_quality.json").read_text(encoding="utf-8"))
    assert report["status"] == "PASS"
    assert report["scope"]["shots"] == [1, 2]
    assert report["scope"]["policies"] == {"1": "auto", "2": "first_only"}
    assert report["scope"]["requirements"]["2"]["frames"] == ["ff"]
    assert (work / "visual_plan.json").is_file()
    assert (ch / "_enhancement" / "verify_only").is_dir()


def test_verify_only_prunes_stale_frames_not_required_by_policy(tmp_path, monkeypatch):
    """R7.2：first_only 镜头不留在旧计划里的 shot-N:lf 帧条目."""
    ch, work = _fixture(tmp_path, monkeypatch)
    assert va.run_verify_only(ch / "script_result.json", work,
                              shots={1, 2}, policies={1: "auto", 2: "first_only"}) == 0
    plan = json.loads((work / "visual_plan.json").read_text(encoding="utf-8"))
    plan["frames"].append({
        "frame_id": "shot-2:lf", "shot_index": 2, "frame": "lf", "kind": "lf",
        "status": "planned", "prompt": "祖先计划残留", "references": []})
    (work / "visual_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False), encoding="utf-8")

    assert va.run_verify_only(ch / "script_result.json", work,
                              shots={1, 2}, policies={1: "auto", 2: "first_only"}) == 0
    merged = json.loads((work / "visual_plan.json").read_text(encoding="utf-8"))
    frame_ids = [f.get("frame_id") for f in merged.get("frames") or []]
    assert "shot-2:lf" not in frame_ids          # 锁定 first_only 的帧不得残留
    assert {"shot-1:ff", "shot-1:lf", "shot-2:ff"} <= set(frame_ids)


def test_verify_only_rejects_unlocked(tmp_path, monkeypatch):
    ch, work = _fixture(tmp_path, monkeypatch)
    (work / "shots" / "2" / ".lock_frame_first_frame").unlink()
    code = va.run_verify_only(ch / "script_result.json", work,
                              shots={1, 2}, policies={1: "auto", 2: "first_only"})
    assert code == 1
    assert not (ch / "step02a_quality.json").exists()


def test_per_shot_scope_gate(monkeypatch):
    script = _script()
    report = {"scope": {"shots": [1, 2], "media_policy": "per_shot",
                        "policies": {"1": "auto", "2": "first_only"}}}
    monkeypatch.setenv("VIDEO_MEDIA_POLICY", "first_only")
    m._assert_step02a_scope(report, {2}, script)          # shot2 放行
    monkeypatch.setenv("VIDEO_MEDIA_POLICY", "auto")
    with pytest.raises(QualityGateError):
        m._assert_step02a_scope(report, {2}, script)      # auto ≠ shot2 first_only
