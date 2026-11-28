# -*- coding: utf-8 -*-
"""百炼视频画幅推导（2026-11-28 O1）.

背景：`BAILIAN_VIDEO_RATIO` 的代码默认此前是写死的 `"9:16"`，`.env.content` 里也
钉着同一个值。若把 `.env.content` 留空而 provider 只做 `ratio or BAILIAN_VIDEO_RATIO`，
`os.getenv` 会返回空串并原样透传给 API（空画幅）。本用例锁住「留空 = 按画幅推导」。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

pytest.importorskip("dashscope")

from _video_gen import bailian_provider as bp
from _video_gen.models import VideoGenerationRequest


def _req(size: str = "720x1280") -> VideoGenerationRequest:
    return VideoGenerationRequest(prompt="p", clip_index=1, duration=6, size=size)


@pytest.fixture(autouse=True)
def _no_ambient_override(monkeypatch):
    """本文件只测推导：显式清掉 provider 级 override（环境与模块常量两处）."""
    monkeypatch.delenv("BAILIAN_VIDEO_RATIO", raising=False)
    monkeypatch.setattr(bp, "BAILIAN_VIDEO_RATIO", "", raising=True)


@pytest.mark.parametrize("size,expected", [
    ("1440*2560", "9:16"),
    ("2560*1440", "16:9"),
    ("1280*1280", "1:1"),
    ("1080*1350", "9:16"),      # 4:5 竖图落到最近的竖桶
    ("1920x1080", "16:9"),
])
def test_empty_override_derives_from_canvas(monkeypatch, size, expected):
    monkeypatch.setenv("VIDEO_SIZE", size)
    assert bp._resolve_ratio(_req()) == expected


def test_explicit_constructor_override_wins(monkeypatch):
    monkeypatch.setenv("VIDEO_SIZE", "1440*2560")
    assert bp._resolve_ratio(_req(), "1:1") == "1:1"


def test_invalid_explicit_override_falls_back_to_canvas(monkeypatch):
    monkeypatch.setenv("VIDEO_SIZE", "1440*2560")
    assert bp._resolve_ratio(_req(), "4:5") == "9:16"


def test_provider_level_override_wins(monkeypatch):
    monkeypatch.setenv("VIDEO_SIZE", "1440*2560")
    monkeypatch.setattr(bp, "BAILIAN_VIDEO_RATIO", "16:9", raising=True)
    assert bp._resolve_ratio(_req()) == "16:9"


def test_unknown_video_size_falls_back_to_916(monkeypatch):
    monkeypatch.setenv("VIDEO_SIZE", "auto")
    assert bp._resolve_ratio(_req()) == "9:16"


@pytest.mark.parametrize("value,expected", [
    ("1440x2560", "9:16"),
    ("9:16", "9:16"),
    ("1024*1024", "1:1"),
    ("1280x720", "16:9"),
    ("1440*2560*1", None),
    ("", None),
    (None, None),
])
def test_ratio_from_canvas_parsing(value, expected):
    assert bp._ratio_from_canvas(value) == expected


def test_config_default_is_empty_string():
    """代码默认与 .env.content 都必须是空串（留空=推导），不能退回写死 9:16."""
    clean = {k: v for k, v in os.environ.items() if k != "BAILIAN_VIDEO_RATIO"}
    clean["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-c",
         "from shared.config import BAILIAN_VIDEO_RATIO as r;print(repr(r))"],
        cwd=str(_ROOT), env=clean, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180)
    assert proc.returncode == 0, proc.stderr[-600:]
    assert proc.stdout.strip() == "''"


def test_ratio_is_never_empty_for_t2v_payload(monkeypatch):
    """回归：`.env.content` 留空时不能把空串传进 API payload."""
    monkeypatch.setenv("VIDEO_SIZE", "1440*2560")
    captured = {}

    class _FakeResp:
        status_code = 500
        code = "fake"
        message = "fake"

    def _fake_async_call(**kwargs):
        captured.update(kwargs)
        return _FakeResp()

    monkeypatch.setattr(bp.VideoSynthesis, "async_call", staticmethod(_fake_async_call))
    service = bp.BailianVideoService(api_keys=["k"], t2v_models=["wan2.7-t2v-2026-06-12"],
                                     resolution="720P")
    service._cooldown = 0
    with pytest.raises(RuntimeError):
        service.create_task(_req())
    assert captured.get("ratio") == "9:16"
