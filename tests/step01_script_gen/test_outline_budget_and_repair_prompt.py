# -*- coding: utf-8 -*-
"""审核第 1、2 项：修复提示词的自相矛盾 + 大纲时间预算与重生复用（v6.116）."""
from __future__ import annotations

import io
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent


# --------------------------------------------------------------- 1) 修复提示词
def test_repair_prompt_allows_necessary_additions():
    """「禁止新增任何短语」与「motion_desc 缺少跃船动作」的正解**自相矛盾**."""
    from tests.verification_helpers import step01_source_text
    src = step01_source_text()
    # 旧句（作为提示词）不得再出现
    assert '"禁止新增任何短语（动作' not in src
    assert '"2. 禁止新增任何短语（动作' not in src
    # 新口径：禁无关新增 + 允许为修复补必要动作
    assert "禁止**无关**新增" in src
    assert "可以在授权字段补必要动作" in src


def test_repair_prompt_requires_linked_fields_in_one_go():
    """v6.120：修复提示词不得再说「每条 warn 改一处字段即可」——它与成对授权冲突.

    背景（2026-09-25 真实重跑）：审计对 shot 5 的 kf 时序问题给出的正解要
    `keyframes[2].desc + keyframes[2].stage + frame_layouts.kfs[2][0].props` **三处
    一起改**，而修复提示词两处写着「每条 warn 改一处字段即可」，决策树还写
    「kf 矛盾→改 kf.desc」——模型按字面只改 desc，另两处被回填 → 半条修复 →
    新阻塞 → 整组回退。授权面（成对目标）与指令面必须同口径。
    """
    from tests.verification_helpers import step01_source_text
    src = step01_source_text()
    # 旧指令（作为提示词）不得再出现
    assert "每条 warn 改一处字段即可" not in src
    assert "一条 warn 改一处——" not in src
    assert "kf矛盾→改kf.desc；" not in src
    # 新口径：只改「解决它所需的已授权字段」，需要联动时一次改全
    assert src.count("需要联动时一次改全") >= 2, "两条修复提示词（多轮 + 兼容回退）都要改"
    assert "例外（联动必须一次改全）" in src
    assert "同镜多个关联问题" in src, "v6.116 的例外仍在（v6.120 只是把它推广到「联动」）"


# --------------------------------------------------------------- 2) 大纲预算
def test_time_budget_is_calibrated_into_the_target_range():
    from step01_script_gen.outline import _calibrate_time_budget

    # 实测形态：30+60+30 = 120s，远超 70-80s 目标
    out = _calibrate_time_budget("对白约30s；动作约60s；反应约30s")
    import re
    nums = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)s", out)]
    assert 70 <= sum(nums) <= 80, out
    assert "已按目标 70-80 秒校准" in out

    # 已在区间内 → 一字不动
    same = "对白约25s；动作约35s；反应约15s"
    assert _calibrate_time_budget(same) == same


def test_time_budget_free_text_and_empty_are_safe():
    from step01_script_gen.outline import _calibrate_time_budget

    assert _calibrate_time_budget("") == ""
    assert _calibrate_time_budget("对白适中；动作较多") == "对白适中；动作较多"


def test_anchor_injects_calibrated_budget():
    from step01_script_gen.outline import outline_to_anchor

    anchor = outline_to_anchor({
        "main_events": ["甲与乙对峙", "乙揭穿甲的身份", "甲被逼入死角"],
        "climax": "乙亮出信物",
        "required_facts": ["甲持信物"],
        "causal_chain": ["对峙→揭穿→逼入死角"],
        "shot_beats": ["甲与乙对峙", "乙揭穿甲的身份", "甲被逼入死角"],
        "time_budget": "对白约30s；动作约60s；反应约30s",
    })
    assert "【时间预算】" in anchor
    assert "对白约30s" not in anchor          # 120s 版本不得注入
    assert "已按目标 70-80 秒校准" in anchor


# --------------------------------------------------------------- 重生复用大纲
def test_both_regen_paths_reuse_the_outline_anchor():
    """结构重生与 GAP 重生此前重建 prompt 时不传大纲 → 重做整章却丢了主要事件/高潮.

    v6.125.2：两条重生路径**同时**要带道具候选短提示（`prop_anchor_block`）——
    它走独立块，不并进大纲锚点（会被 agent_plan 的 200 字压缩吞掉）。
    v6.128（用户 2026-09-25 决定）：注入改经开关 `prop_anchor_block_for()`
    （**默认关闭**），两条路径**仍然**把该 kwarg 传下去——关闭只是内容为空串。
    """
    from tests.verification_helpers import step01_source_text
    src = step01_source_text()
    assert "_sb_builder(novel_text, bank, objects_bank, outline_anchor," in src
    assert "prop_anchor_block=prop_anchor_block" in src, (
        "重生路径必须继续把道具候选短提示 kwarg 传下去（关闭只是内容为空串）")
    # v6.132（用户 2026-09-25 收口）：同两条路径还要带**生成执行契约**块，且契约
    # 必须由**最终组装处**追加（builder 之后还有反馈，放 builder 里就不在末尾）
    assert "contract_block: str = " in src
    assert "append_execution_contract(prompt, contract_block)" in src
    assert "_gap_builder(" in src
    assert "outline_anchor=_o2a(pre_extracted_outline, novel_text)" in src, (
        "两条重生路径的锚点渲染必须带原文——v6.131 起锚点渲染会做同一次"
        "台词逐字核验，不带原文等于少一道核验")
    assert "prop_anchor_block=_pabf(pre_extracted_outline)" in src
