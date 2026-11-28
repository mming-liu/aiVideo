# -*- coding: utf-8 -*-
"""S02：两个资产 Bank 的只读装载与纯查询（零目录副作用，损坏显式）."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import character_bank as cb


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")


def _char(name: str, *, style: str | None = None, path: str = "") -> dict:
    entry = {
        "identifier": name,
        "portraits": {"front": {"path": path or f"{name}/front.png"}},
    }
    if style is not None:
        entry["video_style"] = style
        entry["portraits"]["front"]["video_style"] = style
    return entry


# ---------------------------------------------------------------------------
# 只读装载：文件缺失不是异常
# ---------------------------------------------------------------------------
def test_character_bank_read_only_missing_does_not_create_dir(tmp_path):
    bank_dir = tmp_path / "no_such_bank"
    bank = cb.CharacterBank(bank_dir)
    data = bank.load(read_only=True)
    assert data["characters"] == {}
    assert bank.load_state == "missing"
    assert not bank_dir.exists()


def test_style_bank_read_only_missing_does_not_create_dir(tmp_path):
    bank_dir = tmp_path / "no_such_style"
    bank = cb.StyleBank(bank_dir)
    data = bank.load(read_only=True)
    assert data["is_locked"] is False
    assert bank.load_state == "missing"
    assert not bank_dir.exists()


# ---------------------------------------------------------------------------
# 只读装载：损坏/非法结构是结构化异常，不静默降级
# ---------------------------------------------------------------------------
def test_character_bank_read_only_invalid_json_raises(tmp_path):
    bank_dir = tmp_path / "cb"
    _write(bank_dir / "characters_bank.json", "{broken")
    bank = cb.CharacterBank(bank_dir)
    with pytest.raises(cb.BankLoadError) as excinfo:
        bank.load(read_only=True)
    assert excinfo.value.reason == "invalid_json"
    assert not (bank_dir / "extra").exists()


@pytest.mark.parametrize("payload", [
    [], None, '"text"',
    {"characters": []},
    {"characters": {"甲": "not-a-dict"}},
    {"characters": {"甲": {"portraits": []}}},
    {"characters": {}, "meta": []},
])
def test_character_bank_read_only_rejects_illegal_structure(tmp_path, payload):
    bank_dir = tmp_path / "cb"
    _write(bank_dir / "characters_bank.json", payload)
    bank = cb.CharacterBank(bank_dir)
    with pytest.raises(cb.BankLoadError) as excinfo:
        bank.load(read_only=True)
    assert excinfo.value.reason == "invalid_structure"


@pytest.mark.parametrize("payload", [
    [], None, '"text"',
    {"video_style": 3},
    {"meta": []},
    {"meta": {"style_stamp": []}},
    {"meta": {"style_stamp": {"video_style": 3}}},
])
def test_style_bank_read_only_rejects_illegal_structure(tmp_path, payload):
    bank_dir = tmp_path / "sb"
    _write(bank_dir / "style_bank.json", payload)
    bank = cb.StyleBank(bank_dir)
    with pytest.raises(cb.BankLoadError) as excinfo:
        bank.load(read_only=True)
    assert excinfo.value.reason == "invalid_structure"


# ---------------------------------------------------------------------------
# 查询是纯读取：不盖章、不改内存、不碰目录
# ---------------------------------------------------------------------------
def test_read_only_load_and_queries_do_not_mutate(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "gufeng")
    bank_dir = tmp_path / "cb"
    _write(bank_dir / "characters_bank.json",
           {"characters": {"旧角色": _char("旧角色")}, "meta": {}})
    path = bank_dir / "characters_bank.json"
    before_bytes = path.read_bytes()
    bank = cb.CharacterBank(bank_dir)
    bank.load(read_only=True)
    before_memory = json.dumps(bank._data, ensure_ascii=False, sort_keys=True)

    resolved = bank.resolve_portraits("旧角色")
    bank.get_all_portraits_as_registry()
    bank.style_audit()
    bank.style_context()

    assert resolved["ok"] is False
    assert resolved["reason"] == "legacy_unstamped"
    assert resolved["epoch"] == ""
    assert json.dumps(bank._data, ensure_ascii=False, sort_keys=True) == before_memory
    assert path.read_bytes() == before_bytes
    assert sorted(p.name for p in bank_dir.iterdir()) == ["characters_bank.json"]


def test_style_bank_queries_are_pure_reads(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "gufeng")
    bank_dir = tmp_path / "sb"
    _write(bank_dir / "style_bank.json", {"is_locked": False})
    (bank_dir / "style_anchor.png").write_bytes(b"png")
    path = bank_dir / "style_bank.json"
    before_bytes = path.read_bytes()
    bank = cb.StyleBank(bank_dir)
    bank.load(read_only=True)

    result = bank.get_anchor_for_style()
    bank.style_audit()
    assert result["epoch"] == ""
    assert bank._data.get("meta") is None
    assert path.read_bytes() == before_bytes


# ---------------------------------------------------------------------------
# 默认装载保持历史容错（并仍会建目录），避免改变生产写路径
# ---------------------------------------------------------------------------
def test_default_load_keeps_tolerance_and_dir_creation(tmp_path):
    bank_dir = tmp_path / "cb"
    _write(bank_dir / "characters_bank.json", "{broken")
    bank = cb.CharacterBank(bank_dir)
    data = bank.load()
    assert data["characters"] == {}
    assert bank.load_state == "invalid_json"
    assert bank_dir.is_dir()


# ---------------------------------------------------------------------------
# 派生注册表过滤：Bank 拒收整条剔除；无 Bank 时逐视图过同一判据
# ---------------------------------------------------------------------------
def test_filter_portrait_registry_uses_bank_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "gufeng")
    bank_dir = tmp_path / "cb"
    _write(bank_dir / "characters_bank.json", {
        "characters": {
            "阿古": _char("阿古", style="gufeng"),
            "阿动": _char("阿动", style="anime"),
        },
        "meta": {"style_stamp": {"video_style": "gufeng"}},
    })
    bank = cb.CharacterBank(bank_dir)
    bank.load(read_only=True)
    registry = {
        "阿古": {"front": {"path": "a.png", "video_style": "gufeng"}},
        "阿动": {"front": {"path": "b.png", "video_style": "anime"}},
        "未登记但带标记": {"front": {"path": "c.png", "video_style": "anime"}},
    }
    allowed, rejected = cb.filter_portrait_registry(registry, bank=bank)
    assert set(allowed) == {"阿古"}
    reasons = {(row["identifier"], row["reason"]) for row in rejected}
    assert ("阿动", "style_mismatch") in reasons
    assert ("未登记但带标记", "style_mismatch") in reasons


def test_filter_portrait_registry_without_bank_filters_views(monkeypatch):
    monkeypatch.setenv("VIDEO_STYLE", "gufeng")
    registry = {"某角色": {
        "front": {"path": "a.png", "video_style": "gufeng"},
        "side": {"path": "b.png", "video_style": "anime"},
    }}
    allowed, rejected = cb.filter_portrait_registry(registry, bank=None)
    assert set(allowed["某角色"]) == {"front"}
    assert rejected[0]["view"] == "side"
