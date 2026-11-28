# -*- coding: utf-8 -*-
"""资产库画风硬隔离（2026-11-28 O9）.

构造混放 `anime` / `gufeng` 资产的临时资产库，验证切到 `gufeng` 时 100% 拒收
`anime` 肖像与画风锚点、给出 `style_mismatch` 原因，并且**历史无标记资产**在首次
使用（风格纪元）里不被误杀、换画风后又被正确拒收。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import character_bank as cb


def _raw_bank(path: Path, *, characters: dict, meta: dict | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "characters_bank.json").write_text(json.dumps(
        {"characters": characters, "meta": meta or {}}, ensure_ascii=False),
        encoding="utf-8")


def _legacy_char(name: str, *, style: str | None = None) -> dict:
    entry = {
        "identifier": name, "asset_id": "C01",
        "static_features": "青年男子", "dynamic_features": "从容",
        "first_chapter": 1, "last_chapter": 2,
        "portraits": {"front": {"path": f"{name}/front.png", "description": "正面"}},
    }
    if style is not None:
        entry["video_style"] = style
    return entry


@pytest.fixture()
def gufeng_style(monkeypatch):
    monkeypatch.setattr(cb, "current_video_style", lambda: "gufeng")
    monkeypatch.delenv(cb.STYLE_STRICT_ENV, raising=False)


# ---------------------------------------------------------------------------
# 纯判定
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("entry,current,epoch,expected", [
    ("gufeng", "gufeng", "gufeng", "ok"),
    ("anime", "gufeng", "anime", "style_mismatch"),
    ("", "gufeng", "gufeng", "legacy_in_epoch"),
    ("", "gufeng", "anime", "legacy_unstamped"),
    ("", "", "", "style_unknown"),
    ("", "", "", "style_unknown"),
])
def test_resolve_style_state_table(entry, current, epoch, expected):
    assert cb.resolve_style_state(entry, current=current, epoch=epoch) == expected


# ---------------------------------------------------------------------------
# 肖像：混放库按画风过滤
# ---------------------------------------------------------------------------
def test_mixed_bank_rejects_other_style_portraits(tmp_path, gufeng_style):
    bank = cb.CharacterBank(tmp_path / "cb")
    bank.load()
    bank.register_character("阿古", "古风青年", "沉静", 1, video_style="gufeng")
    bank.set_portraits("阿古", {"front": {"path": "a.png", "description": "古风"}},
                       video_style="gufeng")
    bank.register_character("阿动", "动漫少年", "活泼", 1, video_style="anime")
    bank.set_portraits("阿动", {"front": {"path": "b.png", "description": "动漫"}},
                       video_style="anime")

    ok = bank.resolve_portraits("阿古")
    assert ok["ok"] is True and set(ok["portraits"]) == {"front"}
    bad = bank.resolve_portraits("阿动")
    assert bad["ok"] is False
    assert bad["reason"] == "style_mismatch"
    assert bad["portraits"] == {}
    assert "画风" in bad["message"]

    registry = bank.get_all_portraits_as_registry()
    assert list(registry) == ["阿古"], "动漫肖像绝不能进古风注册表"

    audit = bank.style_audit()
    reasons = {row["identifier"]: row["reason"] for row in audit["characters"]}
    assert reasons == {"阿古": "ok", "阿动": "style_mismatch"}


def test_portrait_records_are_stamped(tmp_path, gufeng_style):
    bank = cb.CharacterBank(tmp_path / "cb")
    bank.load()
    bank.register_character("甲", "s", "d", 1, video_style="gufeng")
    bank.set_portraits("甲", {"front": {"path": "a.png"}})
    char = bank.get_character("甲")
    assert char["video_style"] == "gufeng"
    assert char["asset_contract_version"] == cb.ASSET_CONTRACT_VERSION
    assert char["portraits"]["front"]["video_style"] == "gufeng"
    assert char["portraits"]["front"]["asset_contract_version"] == cb.ASSET_CONTRACT_VERSION


# ---------------------------------------------------------------------------
# 历史资产：风格纪元
# ---------------------------------------------------------------------------
def test_legacy_assets_are_adopted_in_the_current_epoch(tmp_path, gufeng_style):
    _raw_bank(tmp_path / "cb", characters={"旧角色": _legacy_char("旧角色")})
    bank = cb.CharacterBank(tmp_path / "cb")
    bank.load()

    resolved = bank.resolve_portraits("旧角色")
    assert resolved["ok"] is True
    assert resolved["reason"] == "legacy_in_epoch"
    assert resolved["epoch"] == "gufeng"
    assert bank.get_all_portraits_as_registry()

    # 纪元随 save 落盘
    assert bank.save(run_id="run-1") is True
    saved = json.loads((tmp_path / "cb" / "characters_bank.json").read_text(encoding="utf-8"))
    assert saved["meta"]["style_stamp"]["video_style"] == "gufeng"


def test_style_switch_rejects_legacy_assets(tmp_path, gufeng_style):
    _raw_bank(tmp_path / "cb", characters={"旧角色": _legacy_char("旧角色")})
    bank = cb.CharacterBank(tmp_path / "cb")
    bank.load()
    assert bank.resolve_portraits("旧角色")["ok"] is True     # 先在 gufeng 纪元里被采纳
    bank.save(run_id="run-1")

    # 切到 anime：历史无标记资产无法证明属于 anime ⇒ 拒收
    switched = bank.resolve_portraits("旧角色", style="anime")
    assert switched["ok"] is False and switched["reason"] == "legacy_unstamped"
    # 显式标了 anime 的资产仍然可用
    bank.register_character("新角色", "s", "d", 1, video_style="anime")
    bank.set_portraits("新角色", {"front": {"path": "n.png"}}, video_style="anime")
    assert bank.resolve_portraits("新角色", style="anime")["ok"] is True


def test_strict_off_downgrades_mismatch_to_warning(tmp_path, gufeng_style, monkeypatch):
    monkeypatch.setenv(cb.STYLE_STRICT_ENV, "0")
    bank = cb.CharacterBank(tmp_path / "cb")
    bank.load()
    bank.register_character("阿动", "s", "d", 1, video_style="anime")
    bank.set_portraits("阿动", {"front": {"path": "b.png"}}, video_style="anime")
    resolved = bank.resolve_portraits("阿动")
    assert resolved["ok"] is True
    assert resolved["reason"] == "style_mismatch"       # 仍如实记录原因


# ---------------------------------------------------------------------------
# 画风锚点
# ---------------------------------------------------------------------------
def _png(path: Path) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), (40, 60, 80)).save(path)
    return path


def test_style_anchor_is_isolated_by_style(tmp_path, gufeng_style):
    src = _png(tmp_path / "anchor.png")
    bank = cb.StyleBank(tmp_path / "sb")
    bank.load()
    assert bank.lock(str(src), 1) is True
    bank.save()

    resolved = bank.get_anchor_for_style("gufeng")
    assert resolved["ok"] is True and resolved["path"]
    assert resolved["asset_video_style"] == "gufeng"

    other = bank.get_anchor_for_style("anime")
    assert other["ok"] is False and other["reason"] == "style_mismatch"
    assert other["path"] is None

    # 公开的旧入口同样不再返回跨画风锚点
    assert bank.get_anchor_path() == resolved["path"]
