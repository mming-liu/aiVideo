"""test_extractor.py — 文本 → 实体图抽取单测."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from step01_script_gen.prompt_semantic import (
    extract, EntityGraph, Character, Prop,
    PROP_STATE_DROPPED, PROP_STATE_HELD, PROP_STATE_HANGING,
)


def test_extract_empty_text():
    g = extract("")
    assert isinstance(g, EntityGraph)
    assert g.characters == []
    assert g.props == []


def test_extract_characters_from_layout():
    layout = [
        {"name": "杨旭", "position": "船头", "facing": "面向镜头", "role": "主"},
        {"name": "听香", "position": "船梢", "facing": "面向画右的杨旭", "role": "次"},
    ]
    bank = {
        "杨旭": {"static_features": "剑眉星目", "dynamic_features": "素白轻袍"},
        "听香": {"static_features": "鹿系脸", "dynamic_features": "一袭春衫，披头散发"},
    }
    g = extract("杨旭盘坐船头吹箫。", char_layout=layout, characters_bank=bank, scene="舴艋舟")
    assert len(g.characters) == 2
    yangxu = g.character_by_name("杨旭")
    assert yangxu is not None
    assert yangxu.position == "船头"
    assert yangxu.static_features == "剑眉星目"
    assert yangxu.dynamic_features == "素白轻袍"
    assert yangxu.hair_state == ""  # 杨旭动态无发型词

    tingxiang = g.character_by_name("听香")
    assert tingxiang.hair_state == "披头散发"  # 抽出发型词


def test_extract_props_from_text():
    """文本含 酒壶/钓杆 → 抽到 props."""
    g = extract("酒壶放在桌上，钓杆斜倚在岸边。")
    prop_names = {p.name for p in g.props}
    assert "酒壶" in prop_names
    assert "钓杆" in prop_names
    # 未知 prop 不抽
    assert "紫霞宝剑" not in prop_names


def test_extract_prop_state_inference():
    """prop state 推断：'持'→held, '挂'→hanging, '漂'→floating."""
    g1 = extract("杨旭持酒壶")
    p1 = g1.prop_by_name("酒壶")
    assert p1.state == PROP_STATE_HELD

    g2 = extract("酒壶挂在船舷")
    p2 = g2.prop_by_name("酒壶")
    assert p2.state == PROP_STATE_HANGING

    g3 = extract("酒壶漂浮水面")
    p3 = g3.prop_by_name("酒壶")
    assert p3.state == "floating"


def test_extract_unknown_prop_ignored():
    """未登记的 prop 名不抽取."""
    g = extract("紫霞宝剑握在手中")
    assert all(p.name != "紫霞宝剑" for p in g.props)


def test_extract_threat_detection_from_characters():
    """char_layout 含 threat role → is_threat=True."""
    layout = [{"name": "刺客", "position": "水面下", "role": "威胁"}]
    g = extract("刺客潜伏水下", char_layout=layout)
    assassin = g.character_by_name("刺客")
    assert assassin.is_threat is True


def test_extract_relations_collected():
    """空间关系抽取：酒壶 + 在 + 甲板 → 1 relation."""
    g = extract("酒壶在画左甲板上，钓杆斜倚在画右甲板")
    # 至少 1 个 relation
    assert len(g.relations) >= 1
    # 至少 1 个 relation 涉及酒壶 或 钓杆
    subjects = {r.subject for r in g.relations}
    assert "酒壶" in subjects or "钓杆" in subjects
