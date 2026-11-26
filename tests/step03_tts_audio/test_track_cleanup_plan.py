# -*- coding: utf-8 -*-
"""S44/P1：分轨清理必须消费同一份有效计划，4 条旁白不得被当 orphan."""
from __future__ import annotations

from step03_tts_audio import track_cleanup


def test_expected_tracks_with_plan_keeps_all_narrations(tmp_path):
    plan = {"narration_lines": [{"text": "旁白%d" % i} for i in range(1, 5)]}
    script = {"shots": [{"index": 1, "narration_segment": ""}]}
    for i in range(1, 5):
        (tmp_path / ("narration_%02d.mp3" % i)).write_bytes(b"x")
    expected = track_cleanup.expected_tracks(script, plan=plan)
    assert len(expected["narration"]) == 4
    report = track_cleanup.run(script, directory=tmp_path, plan=plan, apply=True)
    assert report["dirty"] == []
    assert len(report["keep"]) == 4
    assert all((tmp_path / ("narration_%02d.mp3" % i)).exists() for i in range(1, 5))


def test_expected_tracks_without_plan_would_orphan_narrations(tmp_path):
    script = {"shots": [{"index": 1, "narration_segment": ""}]}
    for i in range(1, 5):
        (tmp_path / ("narration_%02d.mp3" % i)).write_bytes(b"x")
    expected = track_cleanup.expected_tracks(script)
    assert expected["narration"] == {}
    report = track_cleanup.run(script, directory=tmp_path, apply=False)
    assert len(report["dirty"]) == 4
    assert all(item["reason"] == "orphan_track" for item in report["dirty"])
