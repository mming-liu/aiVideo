# -*- coding: utf-8 -*-
"""S06：核验员载体建议降级为**未核实提示** + 请求附载体能力表（2026-11-21）.

背景（D4）：核验员给出的 `suggested_carriers` 被原样拼成"建议改的载体：…"写进
问题正文，且没有载体能力校验——实测把内心事实建议挂到画面节拍，模型照做后仍被
拒。修复方向：建议降级为**结构化 hint（标注未核实）**；修订请求附一张由呈现协议
`CARRIER_KINDS` 生成的**能力表**，只描述"哪种载体能承载什么"，不做语义判定。
"""
from __future__ import annotations

import pytest

from shared import content_verification as cv
from shared import presentation_contract as pc
from step01_script_gen import outline as o
from step01_script_gen import plan_content_repair as pcr

_SAMPLE_TOKENS = ("杨旭", "听香", "张十三", "青州", "杀青州", "锦衣夜行")


def _partial_fact_record():
    return {"facts": [{
        "fact_uid": "f01", "verdict": cv.VERDICT_PARTIAL,
        "missing": "只演出了一半，缺让步条件",
        "suggested_carriers": ["b07", "n03"],
    }]}


def test_suggested_carriers_become_structured_unverified_hints():
    problems = cv._fact_axis_problems(_partial_fact_record(), {})
    assert len(problems) == 1, problems
    _p = problems[0]
    assert _p["code"] == "verify_fact_unsupported"
    assert "建议改的载体" not in _p["reason"], "指令式载体建议还在问题正文里"
    assert "缺让步条件" in _p["reason"], "核验员给出的缺失内容仍要保留"
    assert _p["hint_carriers"] == ["b07", "n03"], _p
    assert "未核实" in _p["hint_note"], _p


def test_revision_feedback_marks_the_hint_as_unverified():
    problems = cv._fact_axis_problems(_partial_fact_record(), {})
    block = o._view_feedback_block(problems, {})
    assert "建议改的载体" not in block, block
    assert "核验员提示，未核实" in block, block
    assert "b07" in block and "n03" in block, block


def test_carrier_capability_table_is_generated_from_the_contract():
    table = pc.carrier_capability_table()
    for _kind in pc.CARRIER_KINDS:
        assert "`%s`" % _kind in table, "能力表缺载体种类 %s" % _kind
        _desc = pc.CARRIER_CAPABILITY[_kind][1]
        assert _desc[:8] in table, "能力表说明与 CARRIER_CAPABILITY 不同源：%s" % _kind
    assert "只描述能力" in table
    assert not [t for t in _SAMPLE_TOKENS if t in table], "能力表里出现测试素材"


def _capture_delta_prompt(monkeypatch):
    from tests.step01_script_gen.test_plan_content_slots import _Budget, _novel
    from tests.step01_script_gen.test_plan_phase_g_rejection_feedback import _legal_plan

    captured: dict = {}

    class _Svc:
        def generate(self, req):
            captured["prompt"] = str(req.user_prompt)
            from _text_gen.models import TextGenerationResult
            return TextGenerationResult(success=False, error="stop-after-capture")

    monkeypatch.setattr(o, "create_service", lambda *a, **k: _Svc())
    o._replan_by_view(_legal_plan(), [{"code": "verify_fact_unsupported", "uid": "f01"}],
                      _novel(), _Budget(), [], evidence={}, mode="legacy_audio",
                      protocol=o.ROUTE_VIEW_DELTA)
    return captured["prompt"]


def test_delta_revision_request_carries_the_capability_table(monkeypatch):
    prompt = _capture_delta_prompt(monkeypatch)
    assert "载体能力表" in prompt, "增量修订请求里没有载体能力表"
    assert "只描述能力，不做语义判定" in prompt
    for _kind in pc.CARRIER_KINDS:
        assert "`%s`" % _kind in prompt, "请求能力表缺种类 %s" % _kind


def test_slot_request_carries_the_capability_table():
    from tests.step01_script_gen.test_plan_content_slots import _fixture, _novel

    _f = _fixture()
    _view, _err = pc.view_from_plan(_f["plan"])
    assert _view is not None and _err == [], _err
    _found = pcr.content_slots(_view, _f["problems"])
    _req = pcr.slot_request(_view, _found["slots"])
    _prompt = pcr.slot_prompt(_req, _novel())
    assert "载体能力表" in _prompt, "槽位请求里没有载体能力表"
    for _kind in pc.CARRIER_KINDS:
        assert "`%s`" % _kind in _prompt, "槽位请求能力表缺种类 %s" % _kind


def test_the_record_contract_still_accepts_suggested_carriers():
    """核验记录契约不变：`suggested_carriers` 仍是可选的 list[str]（只降级展示，不改输入）."""
    _facts_contract = cv.VERIFICATION_RECORD_CONTRACT.get("facts") or {}
    assert _facts_contract.get("suggested_carriers") == "list[str]", _facts_contract
    # 缺省/空值仍合法（不误拒）
    problems = cv._fact_axis_problems(
        {"facts": [{"fact_uid": "f01", "verdict": cv.VERDICT_UNKNOWN}]}, {})
    assert problems and "hint_carriers" not in problems[0]
