# -*- coding: utf-8 -*-
"""M06（2026-11-22）：来源修正通道同步纠正核心摘要（S11 core_field_frozen ×4 的根因）.

S11 第 2 章 4 轮在来源修正通道连续 5 次被拒：提示要求模型在 core_updates 同步纠正
仍带同一无来源推断的 main_events / climax，来源保留核验也要求「不能在其他摘要留下同一
无来源推断」，但授权表恒为空（没有生产者写 core_dependency），每份按提示作答的响应都被
判 core_field_frozen。另外即使给了授权，apply_response 写入的 core_update_trace 也会让
admission_problems 以「上下文形状非法」拒收——授权路径从未走通。

本文件不打桩 admission_problems，用真实核验夹具走 apply → view → 准入 → 保留核验读取。
"""
import copy

import pytest

from step01_script_gen import plan_coherence as c
from step01_script_gen import source_revision as s
from step01_script_gen.outline import _source_core_authorization
from shared.presentation_contract import view_from_plan
from tests.step01_script_gen.test_legacy_semantic import (
    NOVEL, narration_response, plan_for_narration, verify_through_service)

pytestmark = pytest.mark.v2_request


def _baseline(monkeypatch):
    """f01 主张多了原文没有的「主动」，且同一推断也写进了 main_events."""
    plan = c.mark_plan(plan_for_narration())
    plan['required_facts'][0] = plan['required_facts'][0].replace('周明去年辞职。', '周明去年主动辞职。', 1)
    raw = narration_response(claim='周明去年主动辞职。', verdict='partial')
    plan = verify_through_service(monkeypatch, plan, raw)
    return plan


def _apply(plan, raw, allowed):
    from step01_script_gen.content_verify import novel_identity
    return s.apply_response(plan, raw, novel_text=NOVEL,
                            novel_digest=novel_identity(NOVEL)['digest'],
                            allowed_core_updates=allowed)


def _response(plan, *, main_events=None, echo=()):
    core = {k: copy.deepcopy(plan.get(k)) for k in echo}
    if main_events is not None:
        core['main_events'] = main_events
    return {'fact_updates': [{'fact_uid': 'f01', 'claim': '周明去年辞职。', 'evidence': NOVEL}],
            'core_updates': core}


def _preservation():
    return {'target_uids': ['f01'], 'verdict': 'supported', 'evidence_spans': [NOVEL],
            'reason': '保留辞职、主体和时间，仅移除原文未确定的主动性'}


def test_current_negative_verdict_authorizes_core_sync_with_a_reason(monkeypatch):
    plan = _baseline(monkeypatch)
    allowed = _source_core_authorization(plan, [])
    assert set(allowed) == set(s.CORE_FIELDS)
    assert all('f01' in why for why in allowed.values())


def test_no_current_negative_verdict_means_no_authorization(monkeypatch):
    plan = _baseline(monkeypatch)
    plan.pop('plan_verification')
    assert _source_core_authorization(plan, []) == {}


def test_explicit_dependency_still_wins(monkeypatch):
    plan = _baseline(monkeypatch)
    allowed = _source_core_authorization(
        plan, [{'core_dependency': '显式依赖', 'core_fields': ['end_hook']}])
    assert allowed['end_hook'] == '显式依赖'


def test_authorized_core_sync_reaches_preservation_review(monkeypatch):
    plan = _baseline(monkeypatch)
    new_events = [str(e).replace('主动', '') for e in plan['main_events']] or ['周明去年辞职。']
    if new_events == plan['main_events']:
        new_events = new_events + ['周明去年辞职。']
    cand, bad = _apply(plan, _response(plan, main_events=new_events, echo=('causal_chain',)),
                       _source_core_authorization(plan, []))
    assert bad == [] and cand is not None
    assert cand['main_events'] == new_events
    trace = cand['source_revision_context']['core_update_trace']
    assert set(trace) == {'main_events'}, '回显的 causal_chain 不算修改，不进留痕'
    view, vbad = view_from_plan(cand)
    assert not vbad and not s.admission_problems(view), '带留痕的上下文必须能通过准入'
    assert not s.problems({'source_preservation': _preservation()}, view, novel_text=NOVEL)


def test_unauthorized_core_change_is_still_frozen(monkeypatch):
    plan = _baseline(monkeypatch)
    cand, bad = _apply(plan, _response(plan, main_events=['改写后的主线。']), None)
    assert cand is None
    assert {p['code'] for p in bad} == {'core_field_frozen'}


def test_echo_only_core_updates_need_no_authorization(monkeypatch):
    plan = _baseline(monkeypatch)
    cand, bad = _apply(plan, _response(plan, echo=('main_events', 'causal_chain')), None)
    assert bad == [] and cand is not None
    assert 'core_update_trace' not in cand['source_revision_context']


@pytest.mark.parametrize('trace', [{}, {'dialogue_lines': 'x'}, {'main_events': ' '}, ['main_events']])
def test_malformed_trace_is_rejected_by_admission(monkeypatch, trace):
    plan = _baseline(monkeypatch)
    cand, bad = _apply(plan, _response(plan), None)
    assert cand is not None
    cand['source_revision_context']['core_update_trace'] = trace
    view, _ = view_from_plan(cand)
    assert s.admission_problems(view)[0]['code'] == 'verify_source_revision_context'
