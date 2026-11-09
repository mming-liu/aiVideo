# -*- coding: utf-8 -*-
"""M4 Step03：精准分轨（期望集合）与脏轨清理（默认 dry-run / apply 隔离）."""
from __future__ import annotations

import json

from step03_tts_audio import track_cleanup


def test_expected_tracks_uses_production_predicates():
    script = {
        "shots": [
            {"index": 1, "narration_segment": "甲：你好。"},
            {"index": 2, "narration_segment": "风吹过水面。"},
        ],
        "chapter_outline": {},
    }
    expected = track_cleanup.expected_tracks(script)
    assert expected["dialogue"] == {"shot_1.mp3": 1}
    assert expected["narration"] == {}


def test_plan_cleanup_separates_orphan_media_and_sidecars(tmp_path):
    (tmp_path / "shot_1.mp3").write_bytes(b"keep")
    (tmp_path / "shot_1.mp3.identity.json").write_text("{}", encoding="utf-8")
    (tmp_path / "shot_9.mp3").write_bytes(b"orphan")
    (tmp_path / "shot_9.mp3.voice").write_text("{}", encoding="utf-8")
    (tmp_path / "narration_07.mp3").write_bytes(b"orphan2")

    plan = track_cleanup.plan_cleanup(
        tmp_path, {"dialogue": {"shot_1.mp3": 1}, "narration": {}})

    assert plan["keep"] == ["shot_1.mp3"]
    names = {d["name"] for d in plan["dirty"]}
    assert names == {"shot_9.mp3", "narration_07.mp3"}
    orphan = next(d for d in plan["dirty"] if d["name"] == "shot_9.mp3")
    assert any(path.endswith("shot_9.mp3.voice") for path in orphan["sidecars"])
    assert plan["dry_run"] is True
    assert sorted(plan["missing"]) == []


def test_dry_run_does_not_move_files(tmp_path):
    (tmp_path / "shot_9.mp3").write_bytes(b"orphan")
    result = track_cleanup.run({"shots": [], "chapter_outline": {}},
                               directory=tmp_path)
    assert result["dry_run"] is True
    assert (tmp_path / "shot_9.mp3").exists()


def test_apply_moves_dirty_track_to_quarantine_and_records_manifest(tmp_path):
    audio = tmp_path / "audio"
    audio.mkdir()
    (audio / "shot_1.mp3").write_bytes(b"keep")
    (audio / "shot_9.mp3").write_bytes(b"orphan")
    (audio / "shot_9.mp3.identity.json").write_text("{}", encoding="utf-8")
    expected = {"dialogue": {"shot_1.mp3": 1}, "narration": {}}
    plan = track_cleanup.plan_cleanup(audio, expected)
    quarantine = tmp_path / "quarantine"

    manifest = track_cleanup.apply_cleanup(plan, quarantine)

    assert (audio / "shot_1.mp3").exists()
    assert not (audio / "shot_9.mp3").exists()
    assert (quarantine / "shot_9.mp3").exists()
    assert (quarantine / "shot_9.mp3.identity.json").exists()
    assert len(manifest["moved"]) == 2
    on_disk = json.loads((quarantine / "cleanup_manifest.json").read_text(encoding="utf-8"))
    assert on_disk["schema"] == track_cleanup.SCHEMA
    assert on_disk["source"] == str(audio)


def test_orphan_sidecar_without_media_is_reported(tmp_path):
    (tmp_path / "shot_9.mp3.voice").write_text("{}", encoding="utf-8")
    plan = track_cleanup.plan_cleanup(tmp_path, {"dialogue": {}, "narration": {}})
    assert any(d["reason"] == "orphan_sidecar" for d in plan["dirty"])


def test_missing_expected_track_is_reported_not_synthesized(tmp_path):
    plan = track_cleanup.plan_cleanup(
        tmp_path, {"dialogue": {"shot_1.mp3": 1}, "narration": {}})
    assert plan["missing"] == ["shot_1.mp3"]
    assert not (tmp_path / "shot_1.mp3").exists()
