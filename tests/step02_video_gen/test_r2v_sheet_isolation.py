# -*- coding: utf-8 -*-
"""F2：r2v 参考表拼表**绝不写生产 ``shots/``**，缓存哈希是纯读路径.

此前 `prompt_builder._collect_r2v_references` 把 `fit_refs` 的拼表目录固定为
`shot_work_dir(...)`（生产 `shots/`）；`submit_poll._get_shot_hash` 在 r2v 下也
调用同一收集器，于是「算缓存哈希」也会往正式目录写 `ref_sheet_*.png`。
本用例用受控的 6 张肖像 + 上限 3 的桩场景覆盖两条路径。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from step02_video_gen import prompt_builder as pb


def _registry(tmp_path: Path, count: int = 6) -> tuple[list[dict], dict]:
    chars: list[dict] = []
    registry: dict = {}
    for i in range(count):
        name = f"角色{i}"
        p = tmp_path / "characters_bank" / name / "front.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), "white").save(p)
        chars.append({"identifier": name})
        registry[name] = {"front": {"path": str(p)}}
    return chars, registry


def _stub_collection_env(monkeypatch, tmp_path, chars, registry):
    """把 r2v 收集器收敛到「6 张肖像 + 上限 3」，不读真实资产库/注册表。"""
    from shared import ref_compose as rc

    shot = {
        "index": 1,
        "ff_desc": " ".join(c["identifier"] for c in chars),
        "lf_desc": "",
        "motion_desc": "",
        "prompt": "",
    }
    monkeypatch.setattr(pb, "is_empty_scene", lambda *a, **k: False)
    monkeypatch.setattr(pb, "_find_fallback_reference", lambda *a, **k: None)
    monkeypatch.setattr(pb, "_log_r2v_collect", lambda *a, **k: None)
    monkeypatch.setattr("shared.paths.first_frame_file",
                        lambda idx: tmp_path / "no_first_frame.png")
    monkeypatch.setattr("shared.vimax_bridge._char_aliases",
                        lambda ident, characters=None: [ident])
    monkeypatch.setattr("shared.model_caps.active_video_model", lambda: "stub-r2v")
    monkeypatch.setattr(rc, "get_ref_limit", lambda model=None: 3)

    class _EmptyPropBank:
        def load(self):
            return None

        def get_all_identifiers(self):
            return []

    monkeypatch.setattr("shared.assets_bank.PropAssetsBank", _EmptyPropBank)
    return shot, chars, registry


def test_r2v_composed_sheet_goes_only_to_given_sheet_dir(tmp_path, monkeypatch):
    chars, registry = _registry(tmp_path)
    shot, chars, registry = _stub_collection_env(monkeypatch, tmp_path, chars, registry)
    sheet_dir = tmp_path / "attempt" / "refs"
    production_shots = tmp_path / "production" / "shots" / "1"
    production_shots.mkdir(parents=True)

    refs = pb._collect_r2v_references(
        shot, characters=chars, registry=registry, sheet_dir=sheet_dir)

    sheets = [Path(p) for p in refs if Path(p).name.startswith("ref_sheet_")]
    assert sheets, f"超限参考应拼表，实际 refs={refs}"
    assert all(sheet_dir in p.parents for p in sheets), sheets
    assert not list(production_shots.glob("ref_sheet_*")), "不得写生产 shots/"


def test_get_shot_hash_does_not_materialize_reference_sheets(tmp_path, monkeypatch):
    from step02_video_gen import submit_poll as sp

    chars, registry = _registry(tmp_path)
    shot, chars, registry = _stub_collection_env(monkeypatch, tmp_path, chars, registry)
    production_shots = tmp_path / "production" / "shots" / "1"
    production_shots.mkdir(parents=True)

    monkeypatch.setenv("VIDEO_PROVIDER", "bailian")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "r2v")
    monkeypatch.setattr(pb, "_build_prompt", lambda shot, script, mode="A": "same prompt")
    monkeypatch.setattr(pb, "_load_character_registry", lambda: registry)

    shot_hash = sp._get_shot_hash(shot, {"characters": chars}, shot_dir=tmp_path / "2")

    assert shot_hash
    assert not list(production_shots.rglob("ref_sheet_*.png")), \
        "算缓存哈希不得拼表写盘"
    assert not list(tmp_path.rglob("ref_sheet_*.png")), \
        "纯读路径不得在临时目录里落参考表"


@pytest.mark.parametrize("count", [6])
def test_r2v_read_mode_returns_uncompressed_sources(tmp_path, monkeypatch, count):
    chars, registry = _registry(tmp_path, count=count)
    shot, chars, registry = _stub_collection_env(monkeypatch, tmp_path, chars, registry)

    refs = pb._collect_r2v_references(
        shot, characters=chars, registry=registry, materialize=False)

    assert len(refs) == count
    assert not any(Path(p).name.startswith("ref_sheet_") for p in refs)
