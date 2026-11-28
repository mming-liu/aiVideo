# -*- coding: utf-8 -*-
"""S03：缺标记默认拒收、严格模式的明确边界（不靠库级纪元补来源）."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import character_bank as cb


def _bank(tmp_path, characters: dict, meta: dict | None = None) -> cb.CharacterBank:
    root = tmp_path / "cb"
    root.mkdir(parents=True, exist_ok=True)
    (root / "characters_bank.json").write_text(json.dumps(
        {"characters": characters, "meta": meta or {}}, ensure_ascii=False),
        encoding="utf-8")
    bank = cb.CharacterBank(root)
    bank.load(read_only=True)
    return bank


def _style_bank(tmp_path, payload: dict, *, anchor=True) -> cb.StyleBank:
    root = tmp_path / "sb"
    root.mkdir(parents=True, exist_ok=True)
    if anchor:
        (root / "style_anchor.png").write_bytes(b"png")
    (root / "style_bank.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    bank = cb.StyleBank(root)
    bank.load(read_only=True)
    return bank


@pytest.fixture()
def gufeng(monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "gufeng")
    monkeypatch.delenv(cb.STYLE_STRICT_ENV, raising=False)


def test_unmarked_view_is_rejected_even_when_character_is_tagged(tmp_path, gufeng):
    """角色级标签不能证明历史视图的来源画风."""
    bank = _bank(tmp_path, {"甲": {
        "identifier": "甲",
        "video_style": "gufeng",
        "portraits": {"front": {"path": "a.png"}},
    }})
    resolved = bank.resolve_portraits("甲")
    assert resolved["ok"] is False
    assert resolved["reason"] == "legacy_unstamped"
    assert resolved["rejected"] == [
        {"view": "front", "reason": "legacy_unstamped", "video_style": ""}]


def test_unmarked_view_is_rejected_even_when_epoch_matches(tmp_path, gufeng):
    bank = _bank(tmp_path, {"甲": {
        "identifier": "甲",
        "portraits": {"front": {"path": "a.png"}},
    }}, meta={"style_stamp": {"video_style": "gufeng"}})
    assert bank.resolve_portraits("甲")["reason"] == "legacy_unstamped"


def test_current_style_unknown_is_a_hard_failure(tmp_path, monkeypatch):
    monkeypatch.setenv(cb.STYLE_STRICT_ENV, "0")
    bank = _bank(tmp_path, {"甲": {
        "identifier": "甲",
        "portraits": {"front": {"path": "a.png", "video_style": "gufeng"}},
    }})
    resolved = bank.resolve_portraits("甲", style="")
    assert resolved["ok"] is False
    assert resolved["reason"] == "style_unknown"


@pytest.mark.parametrize("version,reason", [
    (99, "unsupported_contract"),
    ("2", "invalid_structure"),
    (True, "invalid_structure"),
])
def test_contract_version_is_enforced_even_with_strict_off(
        tmp_path, gufeng, monkeypatch, version, reason):
    monkeypatch.setenv(cb.STYLE_STRICT_ENV, "0")
    bank = _bank(tmp_path, {"甲": {
        "identifier": "甲",
        "portraits": {"front": {"path": "a.png", "video_style": "gufeng",
                                "asset_contract_version": version}},
    }})
    resolved = bank.resolve_portraits("甲")
    assert resolved["ok"] is False
    assert resolved["reason"] == reason


def test_non_dict_view_record_is_rejected(tmp_path, gufeng):
    bank = _bank(tmp_path, {"甲": {
        "identifier": "甲",
        "portraits": {"front": "a.png"},
    }})
    resolved = bank.resolve_portraits("甲")
    assert resolved["ok"] is False
    assert resolved["reason"] == "invalid_structure"


def test_strict_off_downgrades_unstamped_but_reports_it(tmp_path, gufeng, monkeypatch):
    monkeypatch.setenv(cb.STYLE_STRICT_ENV, "0")
    bank = _bank(tmp_path, {"甲": {
        "identifier": "甲",
        "portraits": {"front": {"path": "a.png"}},
    }})
    resolved = bank.resolve_portraits("甲")
    assert resolved["ok"] is True
    assert resolved["strict"] is False
    assert resolved["reason"] == "legacy_unstamped"
    assert resolved["rejected"][0]["reason"] == "legacy_unstamped"
    # 降级不等于补来源：内存里的视图仍没有标记
    assert "video_style" not in bank.get_character("甲")["portraits"]["front"]


def test_style_anchor_missing_marker_is_rejected_by_default(tmp_path, gufeng):
    bank = _style_bank(tmp_path, {"is_locked": True,
                                  "meta": {"style_stamp": {"video_style": "gufeng"}}})
    resolved = bank.get_anchor_for_style()
    assert resolved["ok"] is False
    assert resolved["reason"] == "legacy_unstamped"
    assert resolved["path"] is None


def test_style_anchor_strict_off_downgrades_with_marker_visible(
        tmp_path, gufeng, monkeypatch):
    monkeypatch.setenv(cb.STYLE_STRICT_ENV, "0")
    bank = _style_bank(tmp_path, {"is_locked": True})
    resolved = bank.get_anchor_for_style()
    assert resolved["ok"] is True
    assert resolved["strict"] is False
    assert resolved["reason"] == "legacy_unstamped"


def test_filter_portraits_by_style_rejects_unmarked_views(tmp_path, gufeng):
    allowed, rejected = cb.filter_portraits_by_style(
        {"front": {"path": "a.png"}}, current="gufeng", epoch="gufeng")
    assert allowed == {}
    assert rejected[0]["reason"] == "legacy_unstamped"

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setenv(cb.STYLE_STRICT_ENV, "0")
        allowed, rejected = cb.filter_portraits_by_style(
            {"front": {"path": "a.png"}}, current="gufeng", epoch="gufeng")
        assert set(allowed) == {"front"}
        assert rejected[0]["reason"] == "legacy_unstamped"
    finally:
        monkeypatch.undo()
