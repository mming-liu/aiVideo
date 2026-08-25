"""test_canonical_anchor.py — Character.canonical_anchor() dedup 单测.

v6.43 重构：取代 vimax_bridge._inject_character_costume 里的硬编码 dedup 逻辑.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from step01_script_gen.prompt_semantic import Character


def test_canonical_anchor_basic():
    """基础：static + dynamic 拼接."""
    c = Character(
        name="杨旭",
        static_features="剑眉星目，国字脸",
        dynamic_features="素白轻袍，头戴平定四方巾",
    )
    anchor = c.canonical_anchor()
    assert anchor.startswith("杨旭：")
    assert "剑眉星目" in anchor
    assert "素白轻袍" in anchor
    # 含服装词用"，身着"
    assert "，身着" in anchor


def test_canonical_anchor_no_clothing_uses_comma():
    """dynamic 不含服装词 → 用"，"不用"，身着"."""
    c = Character(
        name="刺客",
        static_features="猫系脸，细长眼",
        dynamic_features="蒙面，浑身湿透",
    )
    anchor = c.canonical_anchor()
    # 不含服装词 → connector 是"，"
    assert "，身着" not in anchor
    assert "，蒙面" in anchor


def test_canonical_anchor_hair_state_dedup():
    """dynamic 已含 hair_state → 不前置"此刻X"（避免"披头散发"×2）."""
    c = Character(
        name="听香",
        static_features="鹿系脸，如花似玉，秀颈颀长",
        dynamic_features="一袭春衫，披头散发",
        hair_state="披头散发",
    )
    anchor = c.canonical_anchor()
    # 披头散发 应只出现 1 次
    assert anchor.count("披头散发") == 1
    # 不应出现 "此刻披头散发"（因为 dynamic 已含）
    assert "此刻披头散发" not in anchor


def test_canonical_anchor_hair_state_not_in_dynamic():
    """dynamic 不含 hair_state 但有 hair_state 字段 → 前置"此刻X"."""
    c = Character(
        name="听香",
        static_features="鹿系脸，如花似玉",
        dynamic_features="一袭春衫",
        hair_state="披头散发",
    )
    anchor = c.canonical_anchor()
    assert "此刻披头散发" in anchor
    # 披头散发 仍只 1 次
    assert anchor.count("披头散发") == 1


def test_canonical_anchor_strips_static_hair_phrases():
    """static 含发型短语（美人髻/束发等）+ hair_state 触发 → 静态发型剔除."""
    c = Character(
        name="听香",
        static_features="鹿系脸，青丝挽美人髻，秀颈颀长",
        dynamic_features="一袭春衫，披头散发",
        hair_state="披头散发",
    )
    anchor = c.canonical_anchor()
    # "青丝挽美人髻" 应被剔除
    assert "美人髻" not in anchor
    # 关键 dedup
    assert anchor.count("披头散发") == 1


def test_canonical_anchor_no_hair_state():
    """hair_state=""时按原模板拼."""
    c = Character(
        name="杨旭",
        static_features="剑眉星目",
        dynamic_features="素白轻袍",
    )
    anchor = c.canonical_anchor()
    # 没有"此刻"
    assert "此刻" not in anchor
    assert "杨旭：剑眉星目，身着素白轻袍" == anchor


def test_canonical_anchor_no_static_no_hair_state():
    """static 空 + hair_state 空 → 简化为 "name：dynamic"."""
    c = Character(
        name="杨旭",
        static_features="",
        dynamic_features="素白轻袍",
    )
    anchor = c.canonical_anchor()
    assert anchor == "杨旭：身着素白轻袍"


def test_canonical_anchor_idempotent():
    """二次调用 = 同样结果（无状态修改）."""
    c = Character(
        name="听香",
        static_features="鹿系脸，青丝挽美人髻",
        dynamic_features="一袭春衫，披头散发",
        hair_state="披头散发",
    )
    a1 = c.canonical_anchor()
    a2 = c.canonical_anchor()
    assert a1 == a2
