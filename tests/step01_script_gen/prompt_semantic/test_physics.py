"""test_physics.py — 物理规则单测.

每条规则独立测 detect + fix；通用化后应不依赖具体角色名/prop 名.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from step01_script_gen.prompt_semantic import (
    extract, Violation, PhysicsContext, run_all,
    detect_dropped_prop_no_support,
    detect_anchor_duplicate_phrase,
    detect_underwater_contradiction,
)


def _ctx(scene="舴艋舟", is_hook=False, surfaces=None):
    if surfaces is None:
        from step01_script_gen.prompt_semantic import get_surfaces
        surfaces = get_surfaces(scene)
    return PhysicsContext(scene=scene, surfaces=surfaces, is_hook=is_hook)


# ============================================================================
# Rule: dropped_prop_must_have_support
# ============================================================================

def test_dropped_prop_wine_jug_hang_gunwale():
    """酒壶挂在船舷外 → violation."""
    text = "杨旭仰卧船头，酒壶已倾倒脱手，挂在船舷画左边缘。"
    g = extract(text, char_layout=[{"name": "杨旭", "position": "船头", "role": "主"}])
    viols = detect_dropped_prop_no_support(g, _ctx())
    assert len(viols) == 1
    v = viols[0]
    assert v.entity_name == "酒壶"
    assert v.rule == "dropped_prop_must_have_support"
    assert "倾倒在" in v.replace
    assert "紧贴杨旭身体" in v.replace


def test_dropped_prop_fishing_rod_lean_outside():
    """钓杆斜伸出画右船舷 → violation（杆类用"斜倚"模板）."""
    text = "钓杆已脱手滑落，斜伸出画右船舷身体前倾，即将没入水面。"
    g = extract(text)
    viols = detect_dropped_prop_no_support(g, _ctx())
    assert len(viols) == 1
    v = viols[0]
    assert v.entity_name == "钓杆"
    assert "斜倚在" in v.replace
    assert "杆尖自然垂下没入水面" in v.replace


def test_dropped_prop_no_violation_when_normal():
    """正常描述：酒壶在桌上 → 不触发."""
    text = "酒壶搁在画中甲板上，杨旭端坐。"
    g = extract(text)
    viols = detect_dropped_prop_no_support(g, _ctx())
    assert viols == []


def test_dropped_prop_umbrella_hang():
    """油纸伞挂在船舷 → violation."""
    text = "油纸伞已脱手，挂在船舷。"
    g = extract(text)
    viols = detect_dropped_prop_no_support(g, _ctx())
    assert len(viols) == 1
    assert viols[0].entity_name == "油纸伞"


# ============================================================================
# Rule: character_anchor_phrase_dedup
# ============================================================================

def test_anchor_phrase_dedup_pitou():
    """听香 anchor 块内「披头散发」重复 → strip."""
    text = (
        "听香：鹿系脸，大而清澈的眼睛，秀颈颀长，身段风流，"
        "此刻披头散发，身着一袭春衫，轻腰欲折，头插步摇，"
        "披头散发（头面部无任何动物特征）。"
    )
    g = extract(text, char_layout=[{"name": "听香", "position": "船梢"}])
    viols = detect_anchor_duplicate_phrase(g, _ctx())
    assert len(viols) == 1
    v = viols[0]
    assert v.entity_name == "听香"
    assert v.replace == ""  # 删除
    assert "披头散发" in v.find_pattern


def test_anchor_phrase_no_dedup_when_unique():
    """anchor 块内无重复 → 不触发."""
    text = "听香：鹿系脸，身段风流，身着一袭春衫，头插步摇。"
    g = extract(text, char_layout=[{"name": "听香"}])
    viols = detect_anchor_duplicate_phrase(g, _ctx())
    assert viols == []


def test_anchor_phrase_dedup_yixi_chunshan():
    """'一袭春衫' 重复也去重."""
    text = "听香：鹿系脸，身着一袭春衫，轻腰欲折，一袭春衫。"
    g = extract(text, char_layout=[{"name": "听香"}])
    viols = detect_anchor_duplicate_phrase(g, _ctx())
    assert len(viols) == 1


# ============================================================================
# Rule: underwater_head_no_weapon_above
# ============================================================================

def test_underwater_contradiction_hook_full():
    """钩子首帧刺客+露出水面+刀尖+头顶 4 要素并存 → violation."""
    text = "刺客仅头顶约 2cm 露出水面，刀尖位于刺客头顶正上方约 1cm。"
    g = extract(text, char_layout=[{"name": "刺客", "position": "水面下", "role": "威胁"}])
    viols = detect_underwater_contradiction(g, _ctx(is_hook=True))
    assert len(viols) == 1
    v = viols[0]
    assert v.severity == "HARD"
    assert v.entity_name == "刺客"
    assert "完全潜伏水面下" in v.replace
    assert "匕首在刺客手中位于水面下" in v.replace


def test_underwater_contradiction_nonhook_without_underwater_position():
    """非钩子 + char_layout 刺客不在水下 → 不触发."""
    text = "刺客仅头顶约 2cm 露出水面，刀尖位于刺客头顶正上方约 1cm。"
    g = extract(text, char_layout=[{"name": "刺客", "position": "船头", "role": "威胁"}])
    viols = detect_underwater_contradiction(g, _ctx(is_hook=False))
    assert viols == []


def test_underwater_contradiction_no_weapon_above():
    """缺"刀尖"或"头顶" → 不触发（4 要素不全）."""
    text = "刺客仅头顶约 2cm 露出水面，杨旭仰卧船头。"
    g = extract(text, char_layout=[{"name": "刺客", "position": "水面下"}])
    viols = detect_underwater_contradiction(g, _ctx(is_hook=True))
    assert viols == []


# ============================================================================
# 集成：run_all 跑全部规则
# ============================================================================

def test_run_all_real_hook_three_issues():
    """08-24 实测钩子 3 类问题一齐修."""
    text = (
        "夜色下，杨旭（剑眉星目，素白色轻袍）袒腹仰卧船头，"
        "酒壶已倾倒脱手，挂在船舷画左边缘，"
        "钓杆已脱手滑落，斜伸出画右船舷身体前倾，即将没入水面，"
        "刺客仅头顶约 2cm 露出水面，刀尖位于刺客头顶正上方约 1cm。"
    )
    g = extract(
        text,
        char_layout=[
            {"name": "杨旭", "position": "船头", "role": "主"},
            {"name": "刺客", "position": "水面下", "role": "威胁"},
        ],
        scene="舴艋舟",
    )
    viols = run_all(g, _ctx(is_hook=True))
    # 3 类问题：酒壶 + 钓杆 + 刺客
    assert len(viols) == 3
    rules = {v.rule for v in viols}
    assert "dropped_prop_must_have_support" in rules
    assert "underwater_head_no_weapon_above" in rules
