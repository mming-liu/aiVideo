# -*- coding: utf-8 -*-
"""S05：修订请求的**内容无关性**与**按类裁剪**（2026-11-21）.

本文件钉住三件事（匿名样本，不依赖测试小说）：

1. **内容无关性**：增量协议说明（`_PLAN_DELTA_INSTRUCTION` 与裁剪后的
   `delta_instruction_for(problems)`）不得含具体小说的人名/地名/情节模板，
   也不得残留"第 N 轮实测""例如 d09"这类现场痕迹；
2. **单一来源**：裁剪后的每条规则正文都出自同一份 `_DELTA_RULES`/全量说明，
   不复制第二份规则文本；
3. **按类裁剪**：没有预算类问题就不下发预算规则；没有删除/增删内容可能就不下发
   删改互斥规则；`always` 类协议规则始终在。
"""
from __future__ import annotations

import sys
from pathlib import Path

from step01_script_gen import outline as o

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _content_binding import ALL_TERMS  # noqa: E402  作品词单点清单，本文件不重写

#: 测试素材专属特征（作品词 + 现场痕迹/情节模板）：不是生产提示可用的示例。
_SAMPLE_TOKENS = ALL_TERMS + ("例如 d09", "88 字", "心理决断", "当面应对", "动身离开",
                              "第 2 轮", "第 16/17", "r3/r4")

_VIEW_PROBLEMS = [
    {"code": "verify_fact_unsupported", "uid": "f01", "reason": "画面没有完整表达"},
    {"code": "verify_plan_coherence", "uid": "b01", "reason": "先后关系颠倒"},
]
_BUDGET_PROBLEMS = _VIEW_PROBLEMS + [
    {"code": "budget_infeasible", "path": "time_budget", "reason": "总下界超预算"},
]
_DELETE_PROBLEMS = _VIEW_PROBLEMS + [
    {"code": "verify_voice_not_speech", "uid": "d01", "reason": "叙述冒充对白"},
]


def _assert_no_sample_tokens(text: str, where: str) -> None:
    hit = [t for t in _SAMPLE_TOKENS if t in text]
    assert not hit, "%s 里出现测试素材特征：%r" % (where, hit)


def test_the_delta_instruction_carries_no_sample_specific_content():
    _assert_no_sample_tokens(o._PLAN_DELTA_INSTRUCTION, "全量增量说明")
    for name, probs in (("view", _VIEW_PROBLEMS), ("budget", _BUDGET_PROBLEMS),
                        ("delete", _DELETE_PROBLEMS)):
        _assert_no_sample_tokens(o.delta_instruction_for(probs), "%s 裁剪说明" % name)


def test_trimmed_rules_come_from_the_single_source_text():
    """裁剪只选规则，不改写规则正文：每条选中规则都逐字存在于全量说明."""
    full = o._PLAN_DELTA_INSTRUCTION
    for probs in (_VIEW_PROBLEMS, _BUDGET_PROBLEMS, _DELETE_PROBLEMS):
        text = o.delta_instruction_for(probs)
        # 协议头/脚注与全量版本同源
        assert o._DELTA_PROTOCOL_HEADER.rstrip() in text
        assert o._DELTA_PROTOCOL_FOOTER in text
        for _rid, _cat, rule in o._DELTA_RULES:
            if rule in text:
                assert rule in full, "裁剪规则正文与全量来源不一致：%s" % _rid


def test_budget_rules_are_only_sent_when_budget_problems_exist():
    without = o.delta_instruction_for(_VIEW_PROBLEMS)
    with_budget = o.delta_instruction_for(_BUDGET_PROBLEMS)
    assert "**预算**" not in without, "没有预算问题却下发了预算规则"
    assert "**预算**" in with_budget, "有预算问题却没下发预算规则"
    # 其余 always 规则不受影响（对照：不是整块规则消失）
    assert "自检" in without and "自检" in with_budget


def test_delete_rules_are_only_sent_when_deletion_may_be_needed():
    pure_view = o.delta_instruction_for(_VIEW_PROBLEMS)
    may_delete = o.delta_instruction_for(_DELETE_PROBLEMS)
    _delete_marker = "`delete` 里的每个 id"
    assert _delete_marker not in pure_view, "没有删除可能却下发了删改互斥规则"
    assert _delete_marker in may_delete, "可能删除却没下发删改互斥规则"
    # `content` 档（要增删内容）同样可能删除 ⇒ 也要下发
    content = o.delta_instruction_for(
        [{"code": "plan_no_dialogue_lines", "path": "dialogue_lines"}])
    assert _delete_marker in content


def test_two_anonymous_inputs_trim_independently_and_keep_always_rules():
    """两个匿名样本（问题类别/数量不同）各自裁剪，`always` 规则都在."""
    a = o.delta_instruction_for(
        [{"code": "verify_fact_unsupported", "uid": "f01"}])
    b = o.delta_instruction_for(
        _BUDGET_PROBLEMS + [{"code": "budget_unrepresentable", "uid": "n01"}])
    assert a != b, "不同问题集应得到不同裁剪结果"
    always_text = " ".join(rule for _rid, cat, rule in o._DELTA_RULES
                           if cat == "always")
    for text in (a, b):
        for _rid, cat, rule in o._DELTA_RULES:
            if cat == "always":
                assert rule in text, "always 规则 %s 缺失" % _rid


def test_the_actual_delta_request_uses_the_trimmed_instruction(monkeypatch):
    """真实 `_replan_by_view` 请求：没有预算问题时请求里不出现预算规则正文."""
    from tests.step01_script_gen.test_plan_content_slots import _Budget, _novel
    from tests.step01_script_gen.test_plan_phase_g_rejection_feedback import _legal_plan

    captured: dict = {}

    class _Svc:
        def generate(self, req):
            captured["prompt"] = str(req.user_prompt)
            from _text_gen.models import TextGenerationResult
            return TextGenerationResult(success=False, error="stop-after-capture")

    monkeypatch.setattr(o, "create_service", lambda *a, **k: _Svc())
    o._replan_by_view(_legal_plan(), list(_VIEW_PROBLEMS), _novel(), _Budget(), [],
                      evidence={}, mode="legacy_audio",
                      protocol=o.ROUTE_VIEW_DELTA)
    prompt = captured.get("prompt") or ""
    assert "**预算**" not in prompt, "预算规则进了无预算问题的修订请求"
    # 请求正文会**原样包含本次输入的小说原文**（内容来自输入，不是提示硬编码）；
    # 这里只核"说明块"自己的内容无关性：真实请求里出现的裁剪说明与常量同源且无样本特征。
    assert o._DELTA_PROTOCOL_HEADER.rstrip() in prompt
    for token in ("例如 d09", "88 字", "心理决断", "动身离开"):
        assert token not in prompt, "请求说明块里出现测试素材特征：%r" % token
