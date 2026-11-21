# -*- coding: utf-8 -*-
"""S10：核验判据稳定性兜底（2026-11-21 用户裁决·方案1）.

口径：
- 单次语义负面（fact/voice）不再是硬门：控制器对负面项**再核验一次**，两次一致负面
  才继续阻断；单次负面降为 advisory（保留在记录诊断里，进反馈/报告）。
- coherence 轴本轮整体降为 advisory（A/B 样本三判不稳定，先不触发停稿）。
- 复核调用用独立小额度（每次运行 2 次）；额度/截止不足或复核不可用 ⇒ **fail-closed**，
  保留原负面结论继续阻断。
- 协议/证据/身份类问题（引文缺失、摘要不符等）**不**降级，照旧阻断。
"""
from __future__ import annotations

import copy

import pytest

from shared import content_verification as cv
from shared import presentation_contract as pc
from tests import verification_helpers as vh
from tests.step01_script_gen.test_plan_content_slots import _fixture

_NOVEL = "任意匿名的冻结原文片段：甲说你好，乙说再见。"


def _view():
    _f = _fixture()
    view, errs = pc.view_from_plan(_f["plan"])
    assert view is not None and not errs, errs
    return view


def _base_record(view, digest="d1"):
    return vh.legal_record_for(view, novel_text=_NOVEL, plan_digest=digest)


def _fact_negative(record, uid="f01"):
    rec = copy.deepcopy(record)
    for f in rec.get("facts") or []:
        if str(f.get("fact_uid")) == uid:
            f["verdict"] = cv.VERDICT_PARTIAL
            f["missing"] = "只演出了一半"
            f["support_spans"] = []
    return rec


def _coherence_negative(record, view):
    rec = copy.deepcopy(record)
    _beat = str((view.get("beats") or [{}])[0].get("uid") or "")
    rec["coherence"] = dict(rec["coherence"], verdict=cv.VERDICT_PARTIAL,
                            conflict_beats=[_beat],
                            evidence_spans=[_NOVEL[:20]],
                            reason="节拍先后与原文因果相反")
    return rec


def _codes(problems):
    return [p.get("code") for p in (problems or []) if isinstance(p, dict)]


def _acceptance_codes(record, view):
    return _codes(cv.verification_problems(
        record, view, plan_digest=str(record.get("plan_digest") or ""),
        novel_text=_NOVEL, novel_digest=str(record.get("novel_digest") or "")))


def test_a_single_negative_verdict_is_downgraded_to_advisory():
    view = _view()
    seq = [_fact_negative(_base_record(view)), _base_record(view)]
    ctl = cv.VerificationController(verify=lambda v, d: seq.pop(0),
                                    max_verifications=4, stability_confirmation=True)
    rec = ctl.record_for(view, "d1")
    st = rec.get("stability") or {}
    assert st.get("confirmation") == "performed", st
    assert st.get("advisory_facts") == ["f01"], st
    assert st.get("axis_samples", {}).get("facts", {}).get("f01") == ["partial",
                                                                    "supported"]
    codes = _acceptance_codes(rec, view)
    assert "verify_fact_unsupported" not in codes, codes
    assert "verify_fact_unknown" not in codes, codes
    # 记录本身仍保留真实负面结论（不是伪造 supported）
    _f = [x for x in rec.get("facts") or [] if x.get("fact_uid") == "f01"][0]
    assert _f["verdict"] == "partial"


def test_consistent_negative_verdicts_still_block():
    view = _view()
    seq = [_fact_negative(_base_record(view)), _fact_negative(_base_record(view))]
    ctl = cv.VerificationController(verify=lambda v, d: seq.pop(0),
                                    max_verifications=4, stability_confirmation=True)
    rec = ctl.record_for(view, "d1")
    st = rec.get("stability") or {}
    assert st.get("advisory_facts") == [], st
    codes = _acceptance_codes(rec, view)
    assert "verify_fact_unsupported" in codes, codes


def test_coherence_axis_is_advisory_without_extra_confirmation_call():
    view = _view()
    rec_once = _coherence_negative(_base_record(view), view)
    ctl = cv.VerificationController(verify=lambda v, d: rec_once,
                                    max_verifications=4, stability_confirmation=True)
    rec = ctl.record_for(view, "d1")
    st = rec.get("stability") or {}
    assert st.get("advisory_coherence") is True, st
    assert st.get("confirmation") == "not_needed", st
    assert ctl.used == 1 and ctl.stability_used == 0
    codes = _acceptance_codes(rec, view)
    assert "verify_plan_coherence" not in codes, codes


def test_protocol_problems_are_not_downgraded():
    """证据/引文类问题与判据稳定性无关，仍必须阻断."""
    view = _view()
    _bad = _fact_negative(_base_record(view))
    # 让记录自相矛盾：负面结论却仍带着 supported 义务支座 ⇒ 协议问题
    seq = [_bad, _base_record(view)]
    ctl = cv.VerificationController(verify=lambda v, d: seq.pop(0),
                                    max_verifications=4, stability_confirmation=True)
    rec = ctl.record_for(view, "d1")
    codes = _acceptance_codes(rec, view)
    assert any(c.startswith("verify_fact_verdict_inconsistent")
               or c.startswith("fact_basis_invalid") for c in codes), codes


def test_confirmation_budget_is_bounded_and_fails_closed():
    view = _view()
    neg = _fact_negative(_base_record(view))
    good = _base_record(view)
    seq = [neg, good, neg, good, neg]      # 第三个候选只剩负面，没有复核槽
    ctl = cv.VerificationController(verify=lambda v, d: seq.pop(0),
                                    max_verifications=4, stability_confirmation=True)
    ctl.record_for(view, "d1")
    ctl.record_for(view, "d2")
    rec3 = ctl.record_for(view, "d3")
    st3 = rec3.get("stability") or {}
    assert st3.get("confirmation") == "unavailable", st3
    assert "verify_fact_unsupported" in _acceptance_codes(rec3, view)


def test_confirmation_failure_keeps_the_negative_blocking():
    view = _view()
    neg = _fact_negative(_base_record(view))

    def _verify(v, d):
        _verify.calls += 1
        if _verify.calls == 2:
            raise RuntimeError("confirmation network failure")
        return neg

    _verify.calls = 0
    ctl = cv.VerificationController(verify=_verify, max_verifications=4, stability_confirmation=True)
    rec = ctl.record_for(view, "d1")
    st = rec.get("stability") or {}
    assert st.get("confirmation") == "unavailable", st
    assert "verify_fact_unsupported" in _acceptance_codes(rec, view)
