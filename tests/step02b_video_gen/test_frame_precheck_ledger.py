# -*- coding: utf-8 -*-
"""M4 Step02b：首尾帧 SHA-256 / 差异度预检与持久化账本."""
from __future__ import annotations

import json

from PIL import Image

from shared import frame_precheck


def _png(path, color, size=(32, 32)):
    Image.new("RGB", size, color).save(path)
    return path


def test_identical_first_last_frames_are_blocked(tmp_path):
    ff = _png(tmp_path / "first_frame.png", "white")
    lf = _png(tmp_path / "last_frame.png", "white")

    result = frame_precheck.precheck_pair(ff, lf)

    assert result["verdict"] == "blocked"
    assert result["code"] == "frames_identical"
    assert result["ff"]["sha256"] == result["lf"]["sha256"]


def test_different_frames_are_ready_with_hashes_and_diff(tmp_path):
    ff = _png(tmp_path / "first_frame.png", "black")
    lf = _png(tmp_path / "last_frame.png", "white")

    result = frame_precheck.precheck_pair(ff, lf)

    assert result["verdict"] == "ready"
    assert result["code"] == "ok"
    assert result["ff"]["sha256"] != result["lf"]["sha256"]
    assert result["difference"]["pixel_diff"] > frame_precheck.LOW_DIFF_THRESHOLD


def test_low_difference_is_warning_not_block(tmp_path):
    ff = _png(tmp_path / "first_frame.png", "black")
    base = Image.new("RGB", (32, 32), "black")
    base.putpixel((0, 0), (24, 24, 24))
    lf = tmp_path / "last_frame.png"
    base.save(lf)

    result = frame_precheck.precheck_pair(ff, lf)

    assert result["verdict"] == "ready"
    assert "frames_low_difference" in [w["code"] for w in result["warnings"]]


def test_missing_frame_is_blocked(tmp_path):
    ff = _png(tmp_path / "first_frame.png", "white")
    result = frame_precheck.precheck_pair(ff, tmp_path / "nope.png")
    assert result["verdict"] == "blocked"
    assert result["code"] == "lf_missing"


def test_ledger_appends_attempts_and_caps_history(tmp_path):
    ledger = tmp_path / "step02b_frame_precheck.json"
    ff = _png(tmp_path / "first_frame.png", "black")
    lf = _png(tmp_path / "last_frame.png", "white")
    for i in range(frame_precheck.MAX_ATTEMPTS_PER_SHOT + 3):
        result = frame_precheck.precheck_shot({"index": 1}, [str(ff), str(lf)])
        result["attempt_marker"] = i
        frame_precheck.record(ledger, result)

    data = json.loads(ledger.read_text(encoding="utf-8"))
    assert data["schema"] == frame_precheck.SCHEMA
    attempts = data["shots"]["1"]["attempts"]
    assert len(attempts) == frame_precheck.MAX_ATTEMPTS_PER_SHOT
    assert attempts[-1]["attempt_marker"] == frame_precheck.MAX_ATTEMPTS_PER_SHOT + 2
    assert data["shots"]["1"]["latest"]["verdict"] == "ready"


def test_ledger_identity_and_shape_are_enforced(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "other", "shots": {}}), encoding="utf-8")
    try:
        frame_precheck.load_ledger(bad)
    except ValueError as exc:
        assert "身份不符" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("身份不符的账本必须拒收")
