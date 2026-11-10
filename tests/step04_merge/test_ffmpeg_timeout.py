# -*- coding: utf-8 -*-
"""R5-1：FFmpeg 编码必须有有限超时；显式值优先，超时走 encode_timeout."""
from __future__ import annotations

import subprocess

import pytest

from step04_merge.backends import ffmpeg as f


class _Timeline:
    def __init__(self, seconds):
        self.total_duration_sec = seconds
        self.subtitle_plan = None


def test_derived_timeout_has_floor_and_ceiling():
    assert f.resolve_encode_timeout(_Timeline(1), None) == f.ENCODE_TIMEOUT_FLOOR_SEC
    assert f.resolve_encode_timeout(_Timeline(75), None) == 435.0
    assert f.resolve_encode_timeout(_Timeline(100_000), None) == \
        f.ENCODE_TIMEOUT_CEILING_SEC


def test_explicit_timeout_wins_over_settings_and_timeline():
    assert f.resolve_encode_timeout(_Timeline(75), f.EncodeSettings(timeout_sec=9), 7) == 7
    assert f.resolve_encode_timeout(_Timeline(75), f.EncodeSettings(timeout_sec=9)) == 9


def test_render_timeout_terminates_and_raises_encode_timeout(tmp_path, monkeypatch):
    calls = {"terminate": 0, "kill": 0}

    class _Proc:
        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired(["ffmpeg"], timeout)

        def terminate(self):
            calls["terminate"] += 1

        def kill(self):
            calls["kill"] += 1

        def poll(self):
            return 0

    monkeypatch.setattr(f, "validate_timeline", lambda timeline: timeline)
    monkeypatch.setattr(f, "compile_graph", lambda *a, **k: ([], "", "v", "a", {}))
    monkeypatch.setattr(f, "command_for", lambda *a, **k: ["ffmpeg", "-v", "error"])
    monkeypatch.setattr(f.subprocess, "Popen", lambda *a, **k: _Proc())

    with pytest.raises(f.RenderError) as ei:
        f.render(_Timeline(75), tmp_path / "out.mp4", tmp_path / "work",
                 settings=f.EncodeSettings(timeout_sec=0.2))
    assert ei.value.code == "encode_timeout"
    assert ei.value.stage == "encode"
    assert calls["terminate"] == 1
    assert calls["kill"] >= 1, "terminate 后 wait 仍超时必须 kill"
