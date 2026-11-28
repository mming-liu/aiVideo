# -*- coding: utf-8 -*-
"""SceneObjectsBank._consolidate_aliases 同物异名合并（2026-09-12）.

根因背景：LLM 抽取把同一实体注册两条并给出互相矛盾的尺寸（实测「舟」
长5.5米宽1.5米 vs「无篷舴艋舟」宽1.4米），prompt 把两份尺寸都喂给模型，
帧描述出现两套打架的船体规格。合并规则：≤2 字泛名是长名子串 → 折叠进长名
条目，尺寸取「长宽齐全」侧，泛名降级为 consistent_term。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _bank_with(objects: dict, tmp_path: Path):
    from shared.scene_objects_bank import SceneObjectsBank
    b = SceneObjectsBank(bank_dir=str(tmp_path / "bank"))
    b._data = {"objects": objects, "meta": {"last_updated_chapter": 1, "total_objects": len(objects)}}
    return b


def test_generic_short_name_folded_into_specific(tmp_path):
    """「舟」（长宽齐全）折叠进「无篷舴艋舟」（只有宽）→ 尺寸取齐全侧，泛名进称呼."""
    from shared.scene_objects_bank import SceneObjectsBank
    b = _bank_with({
        "无篷舴艋舟": {"identifier": "无篷舴艋舟", "category": "船", "width_m": 1.4,
                      "capacity": "3", "consistent_terms": []},
        "舟": {"identifier": "舟", "category": "船", "length_m": 5.5, "width_m": 1.5,
               "capacity": "2-3", "consistent_terms": []},
    }, tmp_path)
    b.save()
    merged = b._data["objects"]
    assert set(merged) == {"无篷舴艋舟"}, f"泛名应被折叠: {set(merged)}"
    e = merged["无篷舴艋舟"]
    assert e["length_m"] == 5.5 and e["width_m"] == 1.5  # 长宽齐全侧优先
    assert "舟" in e["consistent_terms"]  # 泛名降级为称呼（匹配不丢）


def test_specific_long_name_standalone_kept(tmp_path):
    """无子串关系的两个物体不合并."""
    from shared.scene_objects_bank import SceneObjectsBank
    b = _bank_with({
        "马车": {"identifier": "马车", "category": "车", "length_m": 3.0},
        "照月湾": {"identifier": "照月湾", "category": "场景"},
    }, tmp_path)
    b.save()
    assert set(b._data["objects"]) == {"马车", "照月湾"}
