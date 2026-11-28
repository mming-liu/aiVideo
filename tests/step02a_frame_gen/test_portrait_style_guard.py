# -*- coding: utf-8 -*-
"""S02：画风判据统一到预检与参考图收集（跨画风资产不得经任何回退进入请求）."""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _png(path: Path, color=(40, 60, 80)) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (16, 16), color).save(path)
    return path


def _char(name: str, *, style: str, front: Path) -> dict:
    return {
        "identifier": name,
        "asset_id": "C01",
        "static_features": "青年",
        "dynamic_features": "沉静",
        "first_chapter": 1,
        "last_chapter": 2,
        "video_style": style,
        "asset_contract_version": 2,
        "portraits": {
            "front": {"path": str(front), "video_style": style,
                      "asset_contract_version": 2},
            "base": {"path": str(front), "video_style": style,
                     "asset_contract_version": 2},
        },
    }


def _set_style(monkeypatch, style: str = "gufeng") -> None:
    monkeypatch.setenv("VIDEO_STYLE", style)
    monkeypatch.setenv("ASSET_STYLE_STRICT", "1")


def _env(tmp_path, monkeypatch, *, bank: dict | None, registry: dict | None,
         style_payload: dict | None = None, with_anchor: bool = False):
    """隔离 paths：返回 (bank_dir, work_dir, style_dir)。"""
    bank_dir = tmp_path / "global" / "characters_bank"
    work_dir = tmp_path / "work"
    style_dir = tmp_path / "global" / "style_bank"
    bank_dir.mkdir(parents=True, exist_ok=True)
    if bank is not None:
        (bank_dir / "characters_bank.json").write_text(
            json.dumps(bank, ensure_ascii=False), encoding="utf-8")
    if registry is not None:
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "character_portraits_registry.json").write_text(
            json.dumps(registry, ensure_ascii=False), encoding="utf-8")
    if style_payload is not None:
        style_dir.mkdir(parents=True, exist_ok=True)
        (style_dir / "style_bank.json").write_text(
            json.dumps(style_payload, ensure_ascii=False), encoding="utf-8")
    if with_anchor:
        _png(style_dir / "style_anchor.png")
    monkeypatch.setattr("shared.paths.characters_bank_dir", lambda: bank_dir)
    monkeypatch.setattr("shared.paths.vimax_working_dir", lambda: work_dir)
    monkeypatch.setattr("shared.paths.style_bank_dir", lambda: style_dir)
    return bank_dir, work_dir, style_dir


def _script(name: str) -> dict:
    return {
        "characters": [{"identifier": name}],
        "shots": [{
            "index": 1,
            "ff_desc": f"{name}站在门口。",
            "frame_layouts": {"ff": [{"name": name, "on_screen": True}]},
        }],
    }


# ---------------------------------------------------------------------------
# 预检：跨画风肖像即使注册表里有，也必须拒收并透传原因
# ---------------------------------------------------------------------------
def test_precheck_rejects_stamped_cross_style_portrait(tmp_path, monkeypatch):
    from step02a_frame_gen.execution_plan import check_pre_shot_asset_gate

    _set_style(monkeypatch, "gufeng")
    front = _png(tmp_path / "global" / "characters_bank" / "阿动" / "base.png")
    bank_dir, work_dir, _ = _env(
        tmp_path, monkeypatch,
        bank={"characters": {"阿动": _char("阿动", style="anime", front=front)},
              "meta": {"style_stamp": {"video_style": "gufeng"}}},
        # 派生缓存仍指向同一张动漫肖像：不能因为缓存非空就跳过 Bank 守卫
        registry={"阿动": {"front": {"path": str(front), "video_style": "anime"},
                           "base": {"path": str(front), "video_style": "anime"}}},
    )
    gate = check_pre_shot_asset_gate(_script("阿动"), only_indices={1}, frames={"ff"})
    assert gate["status"] == "blocked"
    assert gate["missing_portraits"] == [{
        "shot_index": 1, "frame": "ff", "character": "阿动",
        "reason": "style_mismatch",
    }]
    reasons = {issue.get("reason") for issue in gate["issues"]}
    assert "style_mismatch" in reasons
    assert not (bank_dir / "extra").exists()


def test_precheck_accepts_same_style_portrait(tmp_path, monkeypatch):
    from step02a_frame_gen.execution_plan import check_pre_shot_asset_gate

    _set_style(monkeypatch, "gufeng")
    front = _png(tmp_path / "global" / "characters_bank" / "阿古" / "base.png")
    _env(tmp_path, monkeypatch,
         bank={"characters": {"阿古": _char("阿古", style="gufeng", front=front)},
               "meta": {"style_stamp": {"video_style": "gufeng"}}},
         registry={"阿古": {"front": {"path": str(front), "video_style": "gufeng"},
                            "base": {"path": str(front), "video_style": "gufeng"}}})
    gate = check_pre_shot_asset_gate(_script("阿古"), only_indices={1}, frames={"ff"})
    assert gate["status"] == "ok"
    assert gate["missing_portraits"] == []


# ---------------------------------------------------------------------------
# 参考图收集：与预检同一判据
# ---------------------------------------------------------------------------
def test_collector_omits_cross_style_portrait_with_reason(tmp_path, monkeypatch):
    from shared import vimax_bridge as vb

    _set_style(monkeypatch, "gufeng")
    front = _png(tmp_path / "global" / "characters_bank" / "阿动" / "base.png")
    _env(tmp_path, monkeypatch,
         bank={"characters": {"阿动": _char("阿动", style="anime", front=front)},
               "meta": {"style_stamp": {"video_style": "gufeng"}}},
         registry={"阿动": {"front": {"path": str(front), "video_style": "anime"}}})
    monkeypatch.setattr(vb, "_collect_scene_asset_refs", lambda *a, **k: [])
    monkeypatch.setattr(vb, "_collect_space_object_refs", lambda *a, **k: [])
    monkeypatch.setattr(vb, "_collect_prop_asset_refs", lambda *a, **k: [])

    info = vb._collect_frame_refs_for_shot(
        _script("阿动")["shots"][0], [{"identifier": "阿动"}],
        for_frame="首帧", return_buckets=True)
    assert info["buckets"][1] == ("chars", [])
    assert any(m.get("reason") == "style_mismatch" for m in info["missing"])


def test_collector_uses_same_style_portrait(tmp_path, monkeypatch):
    from shared import vimax_bridge as vb

    _set_style(monkeypatch, "gufeng")
    front = _png(tmp_path / "global" / "characters_bank" / "阿古" / "base.png")
    _env(tmp_path, monkeypatch,
         bank={"characters": {"阿古": _char("阿古", style="gufeng", front=front)},
               "meta": {"style_stamp": {"video_style": "gufeng"}}},
         registry={"阿古": {"front": {"path": str(front), "video_style": "gufeng"},
                            "base": {"path": str(front), "video_style": "gufeng"}}})
    monkeypatch.setattr(vb, "_collect_scene_asset_refs", lambda *a, **k: [])
    monkeypatch.setattr(vb, "_collect_space_object_refs", lambda *a, **k: [])
    monkeypatch.setattr(vb, "_collect_prop_asset_refs", lambda *a, **k: [])

    info = vb._collect_frame_refs_for_shot(
        _script("阿古")["shots"][0], [{"identifier": "阿古"}],
        for_frame="首帧", return_buckets=True)
    assert len(info["buckets"][1][1]) == 1
    assert Path(info["buckets"][1][1][0]).name == "base.png"


# ---------------------------------------------------------------------------
# 锚点拒收原因透传，不伪装成“图片不存在”
# ---------------------------------------------------------------------------
def test_precheck_reports_anchor_style_rejection(tmp_path, monkeypatch):
    from step02a_frame_gen.execution_plan import check_pre_shot_asset_gate

    _set_style(monkeypatch, "gufeng")
    front = _png(tmp_path / "global" / "characters_bank" / "阿古" / "base.png")
    _env(tmp_path, monkeypatch,
         bank={"characters": {"阿古": _char("阿古", style="gufeng", front=front)},
               "meta": {"style_stamp": {"video_style": "gufeng"}}},
         registry={"阿古": {"front": {"path": str(front), "video_style": "gufeng"}}},
         style_payload={"video_style": "anime",
                        "meta": {"style_stamp": {"video_style": "anime"}}},
         with_anchor=True)
    gate = check_pre_shot_asset_gate(_script("阿古"), only_indices={1}, frames={"ff"})
    assert gate["style_anchor"] is None
    assert gate["style_anchor_reason"] == "style_mismatch"
    codes = {issue.get("code"): issue for issue in gate["issues"]}
    assert "style_anchor_rejected" in codes
    assert codes["style_anchor_rejected"]["reason"] == "style_mismatch"


# ---------------------------------------------------------------------------
# 损坏的资产库显式阻断；只读检查不建目录
# ---------------------------------------------------------------------------
def test_precheck_blocks_on_corrupt_character_bank(tmp_path, monkeypatch):
    from step02a_frame_gen.execution_plan import check_pre_shot_asset_gate

    _set_style(monkeypatch, "gufeng")
    bank_dir, _, _ = _env(tmp_path, monkeypatch, bank=None, registry=None)
    (bank_dir / "characters_bank.json").write_text("{broken", encoding="utf-8")
    gate = check_pre_shot_asset_gate(_script("甲"), only_indices={1}, frames={"ff"})
    assert gate["status"] == "blocked"
    codes = {issue.get("code"): issue for issue in gate["issues"]}
    assert codes["asset_bank_load_error"]["reason"] == "invalid_json"


def test_precheck_is_read_only_when_bank_dir_is_absent(tmp_path, monkeypatch):
    from step02a_frame_gen.execution_plan import check_pre_shot_asset_gate

    _set_style(monkeypatch, "gufeng")
    bank_dir = tmp_path / "global" / "characters_bank"
    work_dir = tmp_path / "work"
    style_dir = tmp_path / "global" / "style_bank"
    monkeypatch.setattr("shared.paths.characters_bank_dir", lambda: bank_dir)
    monkeypatch.setattr("shared.paths.vimax_working_dir", lambda: work_dir)
    monkeypatch.setattr("shared.paths.style_bank_dir", lambda: style_dir)

    gate = check_pre_shot_asset_gate(_script("甲"), only_indices={1}, frames={"ff"})
    assert gate["status"] == "blocked"
    assert not bank_dir.exists()
    assert not work_dir.exists()
    assert not style_dir.exists()
