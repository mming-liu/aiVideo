"""SelfReviewConstraint 单元测试（v6.42 PoC）.

覆盖：
  1. 默认 disabled：SELF_REVIEW_ENABLED 未设 → apply() 跳过
  2. enabled 但 LLM 失败 → 静默不抛
  3. enabled + LLM 返回"OK" → 0 违例
  4. enabled + LLM 返回"shot N: ..." → N 个 Violation
  5. _parse_review_output 鲁棒性：空行、杂行、"OK"、多行
  6. 防重入：连续两次 apply() 只调 1 次 LLM
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ============================================================================
# 解析器单测
# ============================================================================

def test_parse_review_output_ok():
    """LLM 输出 OK → 0 违例."""
    from step01_script_gen.constraints.self_review import _parse_review_output
    assert _parse_review_output("OK") == []
    assert _parse_review_output("ok") == []
    assert _parse_review_output("  OK  \n") == []


def test_parse_review_output_single_issue():
    """单行 'shot N: <msg>' → 1 违例."""
    from step01_script_gen.constraints.self_review import _parse_review_output
    vs = _parse_review_output("shot 1: 锚点缺失")
    assert len(vs) == 1
    assert vs[0].shot_index == 1
    assert vs[0].message == "锚点缺失"
    assert vs[0].constraint_name == "self_review"


def test_parse_review_output_multiple_issues():
    """多行 → 多违例."""
    from step01_script_gen.constraints.self_review import _parse_review_output
    text = "shot 1: 锚点缺失\nshot 3: 致命伤断裂\nshot 5: 抽象词污染"
    vs = _parse_review_output(text)
    assert len(vs) == 3
    assert [v.shot_index for v in vs] == [1, 3, 5]


def test_parse_review_output_ignores_garbage():
    """乱码/解释行被忽略."""
    from step01_script_gen.constraints.self_review import _parse_review_output
    text = "以下是问题：\nshot 1: 锚点缺失\n---\n总评：3 处问题"
    vs = _parse_review_output(text)
    assert len(vs) == 1
    assert vs[0].shot_index == 1


def test_parse_review_output_chinese_colon():
    """支持中文冒号：'shot 1：<msg>'."""
    from step01_script_gen.constraints.self_review import _parse_review_output
    vs = _parse_review_output("shot 2：空间方位缺")
    assert len(vs) == 1
    assert vs[0].shot_index == 2
    assert vs[0].message == "空间方位缺"


# ============================================================================
# Constraint 单测
# ============================================================================

def test_self_review_disabled_by_default(monkeypatch):
    """v6.43（08-23 审计）：默认 disabled（opt-in），docstring 与代码对齐.

    v6.42 默认 "1" 与 module docstring 描述「opt-in」冲突——跑批时悄悄
    调 LLM，既无必要又可能改写. v6.43 改回默认 "0"，要启用显式
    SELF_REVIEW_ENABLED=1.
    """
    monkeypatch.delenv("SELF_REVIEW_ENABLED", raising=False)
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.self_review import SelfReviewConstraint

    script = make_script([make_shot(index=1, prompt="杨旭看水面")])
    ctx = make_ctx(script)
    repairs = SelfReviewConstraint().apply(ctx)
    assert repairs == []
    # 不会调用 LLM
    assert getattr(ctx, "_self_review_done", False) is False


def test_self_review_opt_in_v643(monkeypatch):
    """v6.43：SELF_REVIEW_ENABLED=1 显式 opt-in → 调 LLM."""
    monkeypatch.setenv("SELF_REVIEW_ENABLED", "1")
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.self_review import SelfReviewConstraint

    with patch(
        "step01_script_gen.constraints.self_review._call_llm_review",
        return_value="OK",
    ):
        script = make_script([make_shot(index=1, prompt="杨旭看")])
        ctx = make_ctx(script)
        c = SelfReviewConstraint()
        repairs = c.apply(ctx)
    assert ctx._self_review_done is True  # 调过 LLM


def test_self_review_enabled_but_llm_fails(monkeypatch):
    """LLM 调用失败应静默."""
    monkeypatch.setenv("SELF_REVIEW_ENABLED", "1")
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.self_review import SelfReviewConstraint

    with patch(
        "step01_script_gen.constraints.self_review._call_llm_review",
        return_value=None,
    ):
        script = make_script([make_shot(index=1, prompt="杨旭看水面")])
        ctx = make_ctx(script)
        repairs = SelfReviewConstraint().apply(ctx)
    assert repairs == []
    assert ctx._self_review_done is True  # 即使失败也标记 done（防重入）


def test_self_review_enabled_llm_returns_ok(monkeypatch):
    """LLM 返回 OK → 0 违例."""
    monkeypatch.setenv("SELF_REVIEW_ENABLED", "1")
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.self_review import SelfReviewConstraint

    with patch(
        "step01_script_gen.constraints.self_review._call_llm_review",
        return_value="OK",
    ):
        script = make_script([make_shot(index=1, prompt="杨旭看水面")])
        ctx = make_ctx(script)
        SelfReviewConstraint().apply(ctx)
    violations = ctx.drain_violations()
    assert violations == []


def test_self_review_enabled_llm_reports_issues(monkeypatch):
    """LLM 报告问题 → 上报到 violations_remaining."""
    monkeypatch.setenv("SELF_REVIEW_ENABLED", "1")
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints import apply_constraints

    mock_text = "shot 1: 锚点缺失\nshot 2: 致命伤断裂"
    with patch(
        "step01_script_gen.constraints.self_review._call_llm_review",
        return_value=mock_text,
    ):
        script = make_script([
            make_shot(index=1, prompt="杨旭看水面"),
            make_shot(index=2, prompt="夜宴"),
        ])
        ctx = make_ctx(script)
        # apply_constraints 内部已 drain violations_remaining 到 report
        report = apply_constraints(ctx)

    self_review_v = [
        v for v in report.violations_remaining
        if v.constraint_name == "self_review"
    ]
    assert len(self_review_v) == 2
    assert {v.shot_index for v in self_review_v} == {1, 2}


def test_self_review_no_reentrant(monkeypatch):
    """连续两次 apply() 只调 1 次 LLM（ctx 标志防重入）."""
    monkeypatch.setenv("SELF_REVIEW_ENABLED", "1")
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints.self_review import SelfReviewConstraint

    call_count = 0

    def fake_call(script):
        nonlocal call_count
        call_count += 1
        return "shot 1: 锚点缺失"

    with patch(
        "step01_script_gen.constraints.self_review._call_llm_review",
        side_effect=fake_call,
    ):
        script = make_script([make_shot(index=1, prompt="杨旭")])
        ctx = make_ctx(script)
        c = SelfReviewConstraint()
        c.apply(ctx)
        c.apply(ctx)  # 应跳过

    assert call_count == 1
