# -*- coding: utf-8 -*-
"""S02：风格锚点的读取必须与 StyleBank 同判据，且非法结构不得降级（零网络）."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

from shared.vimax_bridge_parts.paths_and_cache import _resolve_style_anchor_path


def _bank(tmp_path, monkeypatch, payload, *, with_default=True):
    import shared.paths as paths
    bank = tmp_path / "style_bank"
    bank.mkdir()
    if with_default:
        (bank / "style_anchor.png").write_bytes(b"png")
    if payload is not None:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        (bank / "style_bank.json").write_text(text, encoding="utf-8")
    monkeypatch.setattr(paths, "style_bank_dir", lambda *a, **k: bank)
    return bank


def _set_style(monkeypatch, style):
    monkeypatch.setenv("VIDEO_STYLE", style)
    monkeypatch.setenv("ASSET_STYLE_STRICT", "1")


def test_same_style_anchor_is_used(tmp_path, monkeypatch):
    bank = _bank(tmp_path, monkeypatch, {
        "style_anchor_image": str(tmp_path / "a.png"),
        "video_style": "anime",
        "meta": {"style_stamp": {"video_style": "anime"}},
    })
    (tmp_path / "a.png").write_bytes(b"png")
    _set_style(monkeypatch, "anime")
    assert _resolve_style_anchor_path() == str(tmp_path / "a.png")


def test_mismatched_anchor_is_rejected(tmp_path, monkeypatch):
    bank = _bank(tmp_path, monkeypatch, {
        "style_anchor_image": str(tmp_path / "a.png"),
        "video_style": "anime",
        "meta": {"style_stamp": {"video_style": "anime"}},
    })
    (tmp_path / "a.png").write_bytes(b"png")
    _set_style(monkeypatch, "gufeng")
    assert _resolve_style_anchor_path() is None


def test_unstamped_anchor_from_other_epoch_is_rejected(tmp_path, monkeypatch):
    _bank(tmp_path, monkeypatch, {
        "meta": {"style_stamp": {"video_style": "anime"}},
    })
    _set_style(monkeypatch, "gufeng")
    assert _resolve_style_anchor_path() is None


@pytest.mark.parametrize("payload", ["[]", "null", '"text"', "{broken"])
def test_illegal_top_level_never_falls_back_to_default(tmp_path, monkeypatch, payload):
    """`[]`/`null`/字符串/损坏 JSON 不得被当成空库，从而放行默认 style_anchor.png."""
    _bank(tmp_path, monkeypatch, payload)
    _set_style(monkeypatch, "gufeng")
    assert _resolve_style_anchor_path() is None


def test_legal_empty_object_is_rejected_by_missing_marker(tmp_path, monkeypatch):
    """S03：合法空对象没有画风标记，默认拒收（不再回退默认图片）."""
    _bank(tmp_path, monkeypatch, {})
    _set_style(monkeypatch, "gufeng")
    assert _resolve_style_anchor_path() is None
