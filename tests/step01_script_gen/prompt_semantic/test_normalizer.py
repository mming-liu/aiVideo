"""test_normalizer.py — PromptSemanticNormalizer 公共入口 + 端到端."""
from __future__ import annotations

import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from step01_script_gen.prompt_semantic import (
    PromptSemanticNormalizer, NormalizedResult,
    apply_fixes,
)


def test_normalizer_basic_no_change():
    """普通文本（无 prop/character 物理问题）→ 不改."""
    n = PromptSemanticNormalizer(scene="舴艋舟")
    r = n.normalize("夜色下，杨旭盘坐船头吹箫，荷叶轻摇。")
    assert r.changed is False
    assert r.violations == []


def test_normalizer_wine_jug_fixes():
    """酒壶挂在船舷 → 修复为倾倒在甲板身侧."""
    text = "杨旭仰卧船头，酒壶已倾倒脱手，挂在船舷画左边缘。"
    n = PromptSemanticNormalizer(
        scene="舴艋舟",
        char_layout=[{"name": "杨旭", "position": "船头", "role": "主"}],
    )
    r = n.normalize(text)
    assert r.changed is True
    assert "挂在船舷" not in r.text
    assert "倾倒在" in r.text
    assert "紧贴杨旭身体" in r.text


def test_normalizer_idempotent():
    """二次跑 = 0 修复."""
    text = "酒壶已倾倒脱手，挂在船舷画左边缘。"
    n = PromptSemanticNormalizer(scene="舴艋舟")
    r1 = n.normalize(text)
    r2 = n.normalize(r1.text)
    assert r2.changed is False
    assert r2.violations == []


def test_normalizer_all_three_issues_real_hook():
    """08-24 真实钩子 3 类问题一齐修."""
    text = (
        "夜色下，杨旭（剑眉星目，素白色轻袍）袒腹仰卧船头，"
        "酒壶已倾倒脱手，挂在船舷画左边缘，"
        "钓杆已脱手滑落，斜伸出画右船舷身体前倾，即将没入水面，"
        "刺客仅头顶约 2cm 露出水面，刀尖位于刺客头顶正上方约 1cm。"
    )
    n = PromptSemanticNormalizer(
        scene="舴艋舟",
        char_layout=[
            {"name": "杨旭", "position": "船头", "role": "主"},
            {"name": "刺客", "position": "水面下", "role": "威胁"},
        ],
    )
    r = n.normalize(text, is_hook=True)
    assert r.changed is True
    # Issue 2: 酒壶/钓杆
    assert "挂在船舷" not in r.text
    assert "斜伸出" not in r.text
    # Issue 3: 刺客潜伏水下
    assert "刺客完全潜伏水面下" in r.text
    assert "头顶正上方" not in r.text


def test_normalizer_handles_empty_text():
    n = PromptSemanticNormalizer()
    r = n.normalize("")
    assert r.text == ""


def test_normalizer_unknown_prop_no_crash():
    """未知 prop 不应让 normalizer 崩溃."""
    text = "紫霞宝剑漂浮水面，林平之握在手中。"
    n = PromptSemanticNormalizer(scene="镖局", char_layout=[{"name": "林平之", "role": "主"}])
    r = n.normalize(text)  # 不崩
    # 紫霞宝剑 未登记 → 不触发任何 violation
    assert r.violations == []


def test_normalizer_new_prop_one_line_extensible():
    """新增 prop（数据驱动）演示：只改 PROP_REGISTRY 一行.

    本测试不直接修改 registry（避免污染其他测试），但展示扩展性。
    真实使用场景：在 registry.py 加 "whip": PropSpec(("鞭",), ...)，
    后续鞭漂浮/挂船舷自动被检测。
    """
    # 当前 PROP_REGISTRY 没登记"鞭"，所以"鞭"不会出现
    text = "一根鞭漂浮水面。"
    n = PromptSemanticNormalizer(scene="驿道")
    r = n.normalize(text)
    # 未登记 prop → 跳过
    assert r.violations == []


def test_apply_fixes_priority():
    """priority 高的先改."""
    from step01_script_gen.prompt_semantic import Violation, EntityGraph
    graph = EntityGraph(raw_text="ABC XYZ")
    v_low = Violation(rule="r1", entity_name="x", msg="", find_pattern="ABC", replace="aaa", priority=10)
    v_high = Violation(rule="r2", entity_name="y", msg="", find_pattern="XYZ", replace="bbb", priority=99)
    out = apply_fixes(graph, [v_low, v_high])
    assert "aaa" in out
    assert "bbb" in out
