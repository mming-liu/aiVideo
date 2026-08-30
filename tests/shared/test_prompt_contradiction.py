# -*- coding: utf-8 -*-
"""prompt 矛盾自检器（v6.50，08-28）单元测试.

样本全部来自 08-26 step02a.log 真实提交内容。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import pytest

from shared.prompt_contradiction import (
    PromptContradictionError,
    assert_no_contradictions,
    find_contradictions,
)


def test_weapon_possession_vs_denial():
    """08-26 实测样本：匕首在杨旭手中 + 杨旭手中没有武器 → 矛盾."""
    prompt = (
        "【道具锚定】神秘人匕首在杨旭手中（场景：照月湾水面）。"
        "【武器归属】武器（短刃、刀、剑）只能由神秘人持有。"
        "杨旭手中没有武器，没有刀，没有剑，没有短刃。"
    )
    issues = find_contradictions(prompt)
    assert issues, "应检出武器持有/否认矛盾"
    assert any("杨旭" in i for i in issues)


def test_weapon_denial_alone_ok():
    """只有否认（无持有断言）→ 不报."""
    prompt = "杨旭手中没有武器，没有刀，没有剑。神秘人右手握刀。"
    assert find_contradictions(prompt) == []


def test_weapon_consistent_ok():
    """持有与归属一致（匕首在神秘人手中 + 杨旭没有武器）→ 不报."""
    prompt = (
        "神秘人右手所握匕首寒光如月。"
        "【武器归属】杨旭手中没有武器，没有刀，没有剑。"
    )
    assert find_contradictions(prompt) == []


def test_shadow_forbid_and_require():
    """剧情需要水下黑影 + 禁止黑影人影 → 矛盾."""
    prompt = (
        "画右水中一道黑影上浮，激起细微波纹直逼船头。"
        "【人物数量限制】画面中只允许出现以下角色：杨旭、听香。"
        "禁止出现任何其他人物、黑影、人影。"
    )
    issues = find_contradictions(prompt)
    assert issues and any("黑影" in i for i in issues)


def test_shadow_forbid_alone_ok():
    """无黑影需求时禁止黑影 → 不报."""
    prompt = "杨旭与听香对坐舱中。禁止出现任何其他人物、黑影、人影。"
    assert find_contradictions(prompt) == []


def test_duplicate_block_detected():
    """角色描述块整体重复注入 → 报重复."""
    block = "杨旭：国字脸，剑眉星目，黑发束冠，身姿挺拔，弱冠之年，身着素白色轻袍，头戴平定四方巾，赤脚，手持钓杆"
    prompt = f"{block}。听香跪坐船头。{block}。该照月湾小舟为窄身无篷舴艋舟。"
    issues = find_contradictions(prompt)
    assert any("重复" in i for i in issues)


def test_double_punctuation():
    assert find_contradictions("水面平复。。听香跪坐") == ["双句号/双逗号残留（模板拼接痕迹）"]


def test_clean_prompt_passes():
    prompt = (
        "夜色荷湾，一叶无篷舴艋舟（宽1.4米，可容3人）静泊，杨旭袒腹仰卧船头，"
        "仰面朝上双目紧闭。画右水中一道黑影蛰伏，仅一截刀尖微露水面。"
    )
    assert find_contradictions(prompt) == []


def test_assert_raises_in_strict_mode(monkeypatch):
    monkeypatch.setenv("PROMPT_CONTRADICTION_STRICT", "1")
    with pytest.raises(PromptContradictionError):
        assert_no_contradictions(
            "匕首在杨旭手中。杨旭手中没有武器，没有刀。", "shot0 首帧")


def test_assert_warns_in_lenient_mode(monkeypatch):
    monkeypatch.setenv("PROMPT_CONTRADICTION_STRICT", "0")
    # 不抛即通过（降级为告警）
    assert_no_contradictions(
        "匕首在杨旭手中。杨旭手中没有武器，没有刀。", "shot0 首帧")


# ── v6.x.x（08-30 实抽）：ff_lf_stage_conflict 第 5 类矛盾 ──

def test_ff_lf_stage_conflict_detected():
    """ff 同时含前态词（闭目/未觉/潜伏） + 伤锚完整句 → 报 ff_lf_stage_conflict.

    现场：hook.prompt 字段同时含"杨旭闭目吟哦"（前态）+ "伤处白袍心口一片暗红
    血迹洇开"（后态），模型自由发挥成"船里灌水/听香在船外"画面。
    """
    prompt = (
        "杨旭素白袍袒腹仰卧船头，仰面朝上双目紧闭，闭目吟哦。"
        "神秘人潜伏在舟侧水下，仅露头部。"
        "伤处白袍心口一片暗红血迹洇开，血痕清晰可见。"
    )
    issues = find_contradictions(prompt)
    assert any("ff_lf_stage_conflict" in i for i in issues), \
        f"应报 ff_lf_stage_conflict，实测 {issues}"


def test_ff_lf_stage_pure_pre_state_ok():
    """纯前态（闭目吟哦 + 无伤锚）→ 不报."""
    prompt = (
        "杨旭素白袍袒腹仰卧船头，仰面朝上双目紧闭，闭目吟哦。"
        "神秘人潜伏在舟侧水下，仅露头部。"
    )
    issues = find_contradictions(prompt)
    assert not any("ff_lf_stage_conflict" in i for i in issues), \
        f"纯前态不该报，实测 {issues}"


def test_ff_lf_stage_pure_post_state_ok():
    """纯后态（伤处白袍 + 无前态词）→ 不报（合法刺杀后态）."""
    prompt = (
        "刺客已没入水中。杨旭仰卧不动，伤处白袍心口一片暗红血迹洇开，"
        "血痕清晰可见。"
    )
    issues = find_contradictions(prompt)
    assert not any("ff_lf_stage_conflict" in i for i in issues), \
        f"纯后态不该报，实测 {issues}"
