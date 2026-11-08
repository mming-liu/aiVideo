# -*- coding: utf-8 -*-
"""场景身份解析：显式 id > 完整唯一命中；歧义/无命中只诊断，不猜（2026-11-08）."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.scene_identity import resolve_scene_identity  # noqa: E402


def _scenes(*items):
    return {sid: {"consistent_terms": terms} for sid, terms in items}


def test_bank_order_permutation_keeps_the_same_unique_selection():
    forward = _scenes(("荷湾", []), ("荷塘", []), ("堤岸", []))
    backward = dict(reversed(list(forward.items())))
    a = resolve_scene_identity(forward, scene_name="荷塘", location="荷塘")
    b = resolve_scene_identity(backward, scene_name="荷塘", location="荷塘")
    assert (a.scene_id, a.reason) == ("荷塘", "exact")
    assert (b.scene_id, b.reason) == ("荷塘", "exact")


def test_complete_identifier_match_is_unique_even_with_fuzzy_lookalike():
    scenes = _scenes(("照月湾荷湾", ["照月湾"]), ("照月湾荷塘", []))
    match = resolve_scene_identity(
        scenes, scene_name="照月湾荷塘", location="照月湾荷塘")
    assert match.scene_id == "照月湾荷塘"
    assert match.reason == "exact"
    assert match.candidates == ("照月湾荷塘", "照月湾荷湾")


def test_complete_alias_match_is_unique():
    scenes = _scenes(("A", ["甲地"]), ("B", []))
    match = resolve_scene_identity(scenes, scene_name="甲地", location="")
    assert (match.scene_id, match.reason) == ("A", "exact")


def test_shared_place_name_is_ambiguous_not_silently_picked():
    scenes = _scenes(("荷湾", ["照月湾"]), ("荷塘", ["照月湾"]))
    match = resolve_scene_identity(scenes, scene_name="照月湾", location="")
    assert match.scene_id is None
    assert match.reason == "ambiguous"
    assert match.candidates == ("荷塘", "荷湾")  # 稳定排序仅供诊断
    assert "歧义" in " ".join(match.diagnostics)


def test_no_match_for_unrelated_location():
    scenes = _scenes(("甲地", []), ("乙地", ["乙别名"]))
    match = resolve_scene_identity(scenes, scene_name="丙地", location="丙地")
    assert match.scene_id is None
    assert match.reason == "no_match"
    assert match.candidates == ()


def test_unrelated_scene_set_never_matches_by_shared_substring():
    scenes = _scenes(("东岸", ["东岸渡口"]), ("西岸", ["西岸渡口"]))
    match = resolve_scene_identity(scenes, scene_name="北山客栈", location="北山")
    assert match.scene_id is None
    assert match.reason == "no_match"


def test_explicit_identity_wins_over_text_match():
    scenes = _scenes(("A", []), ("B", []))
    match = resolve_scene_identity(
        scenes, explicit_id="B", scene_name="A", location="A")
    assert (match.scene_id, match.reason) == ("B", "explicit")


def test_explicit_identity_must_exist():
    scenes = _scenes(("A", []))
    match = resolve_scene_identity(scenes, explicit_id="B", scene_name="A")
    assert match.scene_id is None
    assert match.reason == "explicit_missing"


def test_invalid_and_empty_terms_are_diagnosed_not_crashing():
    scenes = {
        "A": {"consistent_terms": {"bad": 1}},
        "B": {"consistent_terms": None},
        "C": {"consistent_terms": ["甲地", 123, "  "]},
    }
    match = resolve_scene_identity(scenes, scene_name="甲地", location="")
    assert (match.scene_id, match.reason) == ("C", "exact")
    text = " ".join(match.diagnostics)
    assert "类型非法" in text
    assert "非字符串项" in text


def test_empty_shot_fields_are_reported():
    match = resolve_scene_identity(_scenes(("A", [])), scene_name="", location=None)
    assert match.scene_id is None
    assert match.reason == "empty_input"


# ---------------------------------------------------------------------------
# 实际参考图组装入口：唯一命中选图；歧义不选；bank 顺序不影响结果
# ---------------------------------------------------------------------------

class _FakeSceneBank:
    def __init__(self, entries, images):
        self._entries = entries
        self._images = images

    def load(self):
        return None

    def get_all_identifiers(self):
        return list(self._entries)

    def get_scene(self, sid):
        return self._entries.get(sid)

    def get_wide_image(self, sid):
        return self._images.get((sid, "wide"))

    def get_near_image(self, sid):
        return self._images.get((sid, "near"))


def _png(path: Path):
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), (30, 40, 50)).save(path)
    return str(path)


def _install_bank(monkeypatch, entries, images):
    import shared.assets_bank as assets_bank
    monkeypatch.setattr(assets_bank, "SceneAssetsBank",
                        lambda *a, **k: _FakeSceneBank(entries, images))


def test_collect_scene_refs_picks_unique_identity(monkeypatch, tmp_path):
    import shared.vimax_bridge as bridge

    near = _png(tmp_path / "荷塘_near.png")
    entries = {"照月湾荷塘": {"consistent_terms": []},
               "照月湾荷湾": {"consistent_terms": ["照月湾"]}}
    _install_bank(monkeypatch, entries, {("照月湾荷塘", "near"): near})
    shot = {"index": 1, "scene": "照月湾荷塘", "location": "照月湾荷塘",
            "ff_desc": "夜色荷叶", "lf_desc": ""}
    refs = bridge._collect_scene_asset_refs(shot)
    assert refs == [near]
    assert shot["_scene_match"]["reason"] == "exact"
    assert shot["_scene_match"]["scene_id"] == "照月湾荷塘"


def test_collect_scene_refs_refuses_ambiguous_identity(monkeypatch, tmp_path):
    import shared.vimax_bridge as bridge

    near = _png(tmp_path / "x_near.png")
    entries = {"荷湾": {"consistent_terms": ["照月湾"]},
               "荷塘": {"consistent_terms": ["照月湾"]}}
    _install_bank(monkeypatch, entries, {("荷湾", "near"): near, ("荷塘", "near"): near})
    shot = {"index": 2, "scene": "", "location": "照月湾",
            "ff_desc": "夜色", "lf_desc": ""}
    assert bridge._collect_scene_asset_refs(shot) == []
    assert shot["_scene_match"]["reason"] == "ambiguous"
    assert len(shot["_scene_match"]["candidates"]) == 2


def test_collect_scene_refs_order_permutation_same_result(monkeypatch, tmp_path):
    import shared.vimax_bridge as bridge

    near = _png(tmp_path / "甲_near.png")
    entries = {"甲地": {"consistent_terms": []}, "乙地": {"consistent_terms": []}}
    images = {("甲地", "near"): near}
    shot = {"index": 3, "scene": "甲地", "location": "甲地",
            "ff_desc": "夜", "lf_desc": ""}
    _install_bank(monkeypatch, entries, images)
    first = bridge._collect_scene_asset_refs(shot)
    _install_bank(monkeypatch, dict(reversed(list(entries.items()))), images)
    second = bridge._collect_scene_asset_refs(shot)
    assert first == second == [near]


def test_containment_beats_shorter_alias_by_length():
    scenes = _scenes(("照月湾荷塘", []), ("照月湾荷湾", ["照月湾"]))
    match = resolve_scene_identity(
        scenes, scene_name="云河镇照月湾荷塘", location="云河镇照月湾荷塘")
    assert (match.scene_id, match.reason) == ("照月湾荷塘", "contained")


def test_common_substring_tier_is_last_resort():
    scenes = _scenes(("甲", ["照月湾小舟"]), ("乙", ["湖上行舟"]))
    match = resolve_scene_identity(scenes, scene_name="照月湾湖上", location="")
    assert (match.scene_id, match.reason) == ("甲", "common_substr")


def test_equal_top_scores_are_ambiguous_not_ordered():
    scenes = _scenes(("甲", ["照月湾"]), ("乙", ["照月湾"]))
    match = resolve_scene_identity(scenes, scene_name="照月湾", location="")
    assert match.scene_id is None and match.reason == "ambiguous"
    assert match.candidates == ("乙", "甲")
