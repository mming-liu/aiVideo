# -*- coding: utf-8 -*-
"""R0 合法样本：**通用混合问题样本**（规格 R §4 第一行）.

样本在 `fixtures/plan_revision_mixed_legal_sample_v1.json`：一份基线计划同时含**多类**问题，
配一份**合法修法响应**。本用例只用**真实入口**（`outline.apply_replan_ops` +
`outline.plan_acceptance_problems`）断言两件事：

1. **基线确实不合格**，且问题**跨多类**——否则样本什么都没测；
2. 合法修法经真实应用器后**统一验收归零**——这是"合法解存在"的最小证据。

规格要求先有合法样本再写预期与实现；这条用例就是那个样本的守卫。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen import outline as o                                    # noqa: E402

_SAMPLE = json.loads(
    (Path(__file__).resolve().parent / "fixtures"
     / "plan_revision_mixed_legal_sample_v1.json").read_text(encoding="utf-8"))
_SRC = _SAMPLE["source"]


def _codes(plan):
    return [str(p.get("code")) for p in o.plan_acceptance_problems(plan, _SRC)]


def test_the_mixed_sample_baseline_is_really_broken_across_several_classes():
    """基线必须**真的**不合格，且**跨多类**——单类样本证明不了"混合问题"。"""
    _codes_before = _codes(_SAMPLE["base_plan"])
    assert len(_codes_before) >= 3, _codes_before
    assert len(set(_codes_before)) >= 3, "样本必须跨多类：%s" % _codes_before
    # 三类"关系型"问题都要在：承载节拍不符 / 孤儿声音条目 / 节拍未标载体
    assert any("mismatch" in c for c in _codes_before), _codes_before
    assert "orphan_uncarried" in _codes_before, _codes_before
    assert "carrier_unlabeled" in _codes_before, _codes_before


def test_the_legal_response_passes_the_real_entry():
    """合法修法：真实应用器接受 → 统一验收**归零**（合法解存在的最小证据）."""
    _new, _why = o.apply_replan_ops(_SAMPLE["base_plan"], _SAMPLE["response"], _SRC)
    assert isinstance(_new, dict), "合法修法被拒：%s" % str(_why)[:300]
    assert _codes(_new) == [], _codes(_new)


def test_the_sample_verdict_does_not_depend_on_the_names():
    """通用性：换人名/地点/编号，裁决不变（规格要求"不以样本关键词生效"）."""
    _blob = json.dumps(_SAMPLE, ensure_ascii=False)
    _swapped = json.loads(_blob.replace("周明", "沈砚").replace("林青", "柳娘")
                          .replace("北岸仓库", "西码头仓"))
    _new, _why = o.apply_replan_ops(_swapped["base_plan"], _swapped["response"],
                                    _swapped["source"])
    assert isinstance(_new, dict), "换人名后合法修法被拒：%s" % str(_why)[:200]
    assert [str(p.get("code"))
            for p in o.plan_acceptance_problems(_new, _swapped["source"])] == []


def test_the_dialogue_line_is_a_real_novel_speech_not_a_narration():
    """复核 2026-10-04 第 1 条：样本**不许**重复 R §2.3 第 4 条那个漏口.

    原样本把一句**叙述**（"周明在仓里干了十一年。"）登记成对白，而那正是原文逐字命中
    不等于原文人物发言的形态。本样本的 `dialogue_lines` 必须是原文里**带引号的真实发言**。
    """
    for _line in _SAMPLE["base_plan"]["dialogue_lines"]:
        assert _line in _SRC, "对白不在原文里：%r" % _line
        assert "“%s”" % _line in _SRC, \
            "对白 %r 在原文里不是带引号的**人物发言**（那是叙述）" % _line


def test_the_sample_does_not_claim_to_cover_budget_errors():
    """诚实记录它证明到哪：样本 A **不含**预算错误，所以它证明不了"时长＋承载同时
    有错的合法闭环"——那条由 ch1 混合样本承担（台账 §33.3 / §35）."""
    from step01_script_gen.plan_feasibility import evaluate_plan
    _rep = evaluate_plan(_SAMPLE["base_plan"], novel_text=_SRC)
    # `_codes()` 返回的是 **code 字符串**（不是记录字典）——这里直接筛前缀
    _budget_codes = [c for c in _codes(_SAMPLE["base_plan"])
                     if str(c).startswith("budget")]
    assert not _budget_codes, \
        "样本 A 一旦含预算错误，台账 §33.3 里「它不含预算错误」的说明就要跟着改：%s" \
        % _budget_codes
