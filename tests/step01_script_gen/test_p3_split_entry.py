# -*- coding: utf-8 -*-
"""P3 拆分进入真实修订入口：离线贯通矩阵（0 模型调用）.

覆盖 v2 + v2.1：
- 正常入口 `run_plan_revision(..., allowed_repair_actions={"split_fact"})`
  的零调用预检（缺失 R0、target 不唯一、来源问题 deferred）；
- 两份内容/载体分布不同的自足样本的成功闭环；
- 回答越权/证据越界在 M/R 前拒收；
- M shadow（pending_adjudication）、R 失败、局部采纳契约的 deferred /
  unconfirmed / degraded / affected 退化；
- terminal 的提交前守卫（revise 后超时、最终验收期间超时）；
- 普通 `illegal` 的默认继续行为不变。
"""
from __future__ import annotations

import copy
import json
import sys
import time
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import content_verification as cv                                # noqa: E402
from shared import fact_split as fs                                          # noqa: E402
from shared import presentation_contract as pc                               # noqa: E402
from shared.content_verification import VerificationController               # noqa: E402
from step01_script_gen import outline as o                                    # noqa: E402
from step01_script_gen import plan_content_repair as pcr                     # noqa: E402
from step01_script_gen import plan_revision as pr                            # noqa: E402
from step01_script_gen import source_revision as sr                          # noqa: E402
from step01_script_gen import split_adoption                                 # noqa: E402
from step01_script_gen import split_transaction as st                         # noqa: E402
from tests.step01_script_gen import p3_entry_fixtures as fx                  # noqa: E402

A = fx.load_sample("a")
B = fx.load_sample("b")


def _codes(problems) -> list:
    return [str(p.get("code") or "") for p in (problems or [])]


def _base(sample, *, partial_extra=()):
    return fx.baseline(sample, partial_extra=partial_extra)


def _prod_validate(base):
    """生产同源准入装配（同 `outline.py` 里 `_protocol_problems` 的调用）. """
    def _validate(rec, view, digest):
        return cv.record_protocol_problems(
            rec, view, plan_digest=digest, novel_text=base["novel"],
            novel_digest=base["record"]["novel_digest"])
    return _validate


# ---------------------------------------------------------------------------
# 成功：正常入口 + 两份不同样本
# ---------------------------------------------------------------------------
def test_empty_verification_cache_seeds_r0_without_extra_r_call():
    base = _base(A)
    ctl = fx.controller(base)
    assert ctl.used == 0 and ctl.records == {}
    seeds = []
    _seed = ctl.seed
    ctl.seed = lambda *a, **k: seeds.append(1) or _seed(*a, **k)
    out, svc, ctl = fx.run_entry(base, answer=fx.split_answer(A), ctl=ctl)
    assert out["stop_reason"] == "ok"
    assert len(seeds) == 1, "初始化只 seed 一次已有的 R0"
    assert out["seed"] == {"attempted": True, "ok": True}, \
        "成功 seed 也要有结构化证据（矩阵 #1）"
    assert fx.evidence_of(out)["seed"]["attempted"] is True
    assert ctl.used == 1, "R0 只由 seed 装载；R2 才是一次真实核验"
    assert svc.calls == ["plan_content_repair_answer", "fact_split_m.v1"]


@pytest.mark.parametrize("sample,child_uids", [
    (A, ["f02.1", "f02.2"]), (B, ["f02.1", "f02.2"])],
    ids=["sample_a_dialogue_visual", "sample_b_narration_visual"])
def test_split_success_from_normal_revision_entry(sample, child_uids):
    base = _base(sample)
    assert _codes(o.plan_acceptance_problems(base["plan"], base["novel"])) \
        == ["verify_fact_unsupported"]
    out, svc, ctl = fx.run_entry(base, answer=fx.split_answer(sample))
    assert out["stop_reason"] == "ok", out
    assert out["calls"] == 1 and out["rounds"] == 1
    assert svc.calls == ["plan_content_repair_answer", "fact_split_m.v1"]
    assert ctl.used == 1, "R 只调用一次；R0 由 seed 复用"
    plan = out["plan"]
    assert plan["fact_uids"] == ["f01"] + child_uids + ["f03", "f04"]
    assert o.plan_acceptance_problems(plan, base["novel"]) == []
    _view, _vp = pc.view_from_plan(plan)
    assert _vp == []
    _entry = (fs.context(_view).get("splits") or [])[0]
    assert fs.split_state(_view, _entry,
                          record=plan.get("plan_verification")) == "effective"
    assert _entry["judgment"]["verdict"] == "supported"
    assert _entry["judgment"]["call_id"] == "stub-m"
    ev = fx.evidence_of(out)
    assert ev["route"] == "split_transaction"
    assert ev["deferred"] == []
    assert ev["transaction"]["ok"] is True
    assert ev["transaction"]["state"] == "effective"
    assert ev["final_residuals"] == []
    # 代码生成：模型只给 claim/evidence/carrier_uid
    assert [c["uid"] for c in ev["split_ops"]["children"]] == child_uids
    assert ev["split_ledger"]["base_digest"] == cv.plan_digest_of(
        pc.plan_from_view(base["view"]))
    assert ev["split_ledger"]["splits"][0]["authority"]["verdict"] == "partial"


# ---------------------------------------------------------------------------
# 零调用预检
# ---------------------------------------------------------------------------
def test_missing_r0_stops_before_any_service_call():
    base = _base(A)
    out, svc, ctl = fx.run_entry(base, answer=fx.split_answer(A), with_r0=False)
    assert out["stop_reason"] == "verification_failed"
    assert out["calls"] == 0 and out["rounds"] == 0
    assert svc.calls == [] and ctl.used == 0
    assert "verify_record_missing" in _codes(out["problems"])


def test_no_verification_capacity_stops_before_any_service_call():
    base = _base(A)
    ctl = fx.controller(base, max_verifications=0)
    svc = fx.StubService(fx.split_answer(A))
    orig = o.create_service
    o.create_service = lambda *a, **k: svc
    try:
        out = o.run_plan_revision(
            base["plan"], base["novel"], verification=ctl, max_calls=1,
            deadline_sec=60, target_fact_uids=["f02"],
            allowed_repair_actions={"split_fact"})
    finally:
        o.create_service = orig
    assert out["stop_reason"] == "verification_failed"
    assert out["calls"] == 0 and svc.calls == []
    assert "verification_capacity_exhausted" in _codes(out["problems"])


def test_seed_precondition_failure_never_calls_seed():
    """v2.2 §1.1：步骤 1–4 失败时不调用 seed（seed 自身失败另见下条）."""
    base = _base(A)
    base["record"]["plan_digest"] = "0" * 64       # 规范化后摘要与 R0 不符
    ctl = fx.controller(base)
    calls = []
    ctl.seed = lambda *a, **k: calls.append(1) or True
    out, svc, ctl = fx.run_entry(base, answer=fx.split_answer(A), ctl=ctl)
    assert out["stop_reason"] in ("invalid_target", "verification_failed")
    assert out["calls"] == 0 and svc.calls == [] and ctl.used == 0
    assert calls == [], "seed 前的检查失败不得调用 seed"
    assert out["seed"] == {"attempted": False}
    ev = out["history"][0]["evidence"]
    assert ev["seed"] == {"attempted": False}


@pytest.mark.parametrize("mode", ["returns_false", "raises"])
def test_seed_itself_failing_is_recorded_and_zero_call(mode):
    """v2.2 §1.2：seed 自身失败 = 已尝试（attempted=true）+ 零调用退出."""
    base = _base(A)
    ctl = fx.controller(base)

    def _seed(*a, **k):
        if mode == "raises":
            raise RuntimeError("seed boom")
        return False
    ctl.seed = _seed
    out, svc, ctl = fx.run_entry(base, answer=fx.split_answer(A), ctl=ctl)
    assert out["stop_reason"] == "verification_failed"
    assert out["calls"] == 0 and svc.calls == [] and ctl.used == 0
    assert "split_r0_seed_failed" in _codes(out["problems"])
    assert out["seed"]["attempted"] is True and out["seed"]["ok"] is False
    ev = out["history"][0]["evidence"]
    assert ev["seed"]["attempted"] is True and ev["seed"]["ok"] is False
    assert ev["seed"]["code"] == "split_r0_seed_failed"
    if mode == "raises":
        assert "seed boom" in ev["seed"]["error"]


def test_target_must_be_exactly_one_before_any_service_call():
    base = _base(A)
    ctl = fx.controller(base)
    svc = fx.StubService(fx.split_answer(A))
    orig = o.create_service
    o.create_service = lambda *a, **k: svc
    try:
        out = o.run_plan_revision(
            base["plan"], base["novel"], verification=ctl, max_calls=1,
            deadline_sec=60, target_fact_uids=[],
            allowed_repair_actions={"split_fact"})
    finally:
        o.create_service = orig
    assert out["stop_reason"] == "invalid_target"
    assert out["calls"] == 0 and svc.calls == [] and ctl.used == 0
    assert "split_target_required" in _codes(out["problems"])


def test_split_actions_must_be_exclusive_before_any_call():
    base = _base(A)
    ctl = fx.controller(base)
    svc = fx.StubService(fx.split_answer(A))
    orig = o.create_service
    o.create_service = lambda *a, **k: svc
    try:
        out = o.run_plan_revision(
            base["plan"], base["novel"], verification=ctl, max_calls=1,
            deadline_sec=60, target_fact_uids=["f02"],
            allowed_repair_actions={"split_fact", "rewrite"})
    finally:
        o.create_service = orig
    assert out["stop_reason"] == "invalid_target"
    assert out["calls"] == 0 and svc.calls == [] and ctl.used == 0
    assert "split_actions_not_exclusive" in _codes(out["problems"])


def test_source_problem_is_deferred_and_never_calls_source_revision(monkeypatch):
    base = _base(A)
    ctl = fx.controller(base)
    assert ctl.seed(base["digest"], base["record"], plan=base["plan"])
    calls = []
    monkeypatch.setattr(sr, "revise_with_service",
                        lambda *a, **k: calls.append(1) or {})
    svc = fx.StubService(fx.split_answer(A))
    monkeypatch.setattr(o, "create_service", lambda *a, **k: svc)
    budget = pr.RevisionBudget(max_calls=1, deadline_sec=60)
    out = o.revise_plan_once(
        copy.deepcopy(base["plan"]),
        [{"code": "fact_basis_invalid", "uid": "f02", "ref": "f02",
          "path": "", "reason": "来源缺陷"},
         {"code": "verify_fact_unsupported", "uid": "f02", "ref": "f02",
          "path": "", "reason": "展示不完整"}],
        budget, None, novel_text=base["novel"], verification=ctl,
        target_fact_uids=["f02"], allowed_repair_actions={"split_fact"})
    assert calls == [], "拆分模式不得抢先进入 source_revision"
    assert out["kind"] == "applied" and out.get("terminal", {}).get("applied")
    assert "fact_basis_invalid" in out["evidence"].get("deferred", [])
    assert svc.calls == ["plan_content_repair_answer", "fact_split_m.v1"]


# ---------------------------------------------------------------------------
# 回答拒收：M/R 之前
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("answer,code", [
    (fx.split_answer(A, extra={"uid": "f02.1"}),
     "repair_split_child_unknown_field"),
    (fx.split_answer(A, extra={"carrier_uid": "dZZ"}),
     "repair_split_carrier_unknown"),
    (fx.split_answer(A, extra={"evidence": "不在父 span 里"}),
     "repair_split_evidence_not_derived"),
], ids=["uid_injection", "unknown_carrier", "evidence_outside_spans"])
def test_answer_rejections_stop_before_m_and_r(answer, code):
    base = _base(A)
    out, svc, ctl = fx.run_entry(base, answer=answer, max_calls=3)
    assert out["stop_reason"] == "split_failed"
    assert out["calls"] == 1 and out["rounds"] == 1
    assert svc.calls == ["plan_content_repair_answer"], "M 不得发送"
    assert ctl.used == 0, "R 不得调用"
    assert out["plan"]["fact_uids"] == ["f01", "f02", "f03", "f04"]
    assert code in _codes(fx.evidence_of(out).get("answer_problems") or [])


def test_shared_child_carrier_is_not_rejected_by_compile():
    """D 只要求每个子事实有一个载体；共用载体由 R 判表达，不新增“必须不同”规则."""
    base = _base(A)
    slot = ((pcr.content_slots(
        base["view"],
        [{"code": "verify_fact_unsupported", "uid": "f02", "ref": "f02",
          "path": "", "reason": ""}],
        allow_split=True, split_target_uids=["f02"],
        verification_record=base["record"], novel_text=base["novel"],
        record_admitted=True)["slots"])[0])
    answer = fx.split_answer(A, kids=[
        {"claim": "甲说会在天黑前回来", "evidence": "我会在天黑前回来。",
         "carrier": {"kind": "dialogue", "uid": "d01"}},
        {"claim": "甲转身离开", "evidence": "说完他转身离开。",
         "carrier": {"kind": "dialogue", "uid": "d01"}}])
    ops, ledger, scope, problems = pcr.compile_split(
        base["view"], base["plan"], slot, answer, record=base["record"],
        novel_text=base["novel"])
    assert problems == [], problems
    assert [c["uid"] for c in ops["children"]] == ["f02.1", "f02.2"]
    assert {c["carrier"]["uid"] for c in ops["children"]} == {"d01"}
    assert not any("duplicate" in c for c in _codes(problems))


# ---------------------------------------------------------------------------
# M / R 失败
# ---------------------------------------------------------------------------
def test_m_negative_is_pending_adjudication_without_r():
    base = _base(A)
    out, svc, ctl = fx.run_entry(
        base, answer=fx.split_answer(A), m_verdict="partial", max_calls=3)
    assert out["stop_reason"] == "split_pending_adjudication"
    assert out["calls"] == 1 and out["rounds"] == 1
    assert svc.calls == ["plan_content_repair_answer", "fact_split_m.v1"]
    assert ctl.used == 0
    assert out["plan"]["fact_uids"] == ["f01", "f02", "f03", "f04"]
    tx = fx.evidence_of(out)["transaction"]
    assert tx["stage"] == "m" and tx["status"] == "pending_adjudication"
    assert tx["judgment"]["verdict"] == "partial"
    assert tx["judgment"]["call_id"] == "stub-m"


def test_r_child_partial_keeps_baseline_and_does_not_adopt():
    base = _base(A)

    def _verify(view, digest):
        return fx.record_for(view, digest, partial=("f02.1",),
                             novel=base["novel"])
    out, svc, ctl = fx.run_entry(base, answer=fx.split_answer(A),
                                 verify=_verify, max_calls=3)
    assert out["stop_reason"] == "split_failed"
    assert out["plan"]["fact_uids"] == ["f01", "f02", "f03", "f04"]
    assert ctl.used == 1 and out["calls"] == 1 and out["rounds"] == 1
    tx = fx.evidence_of(out)["transaction"]
    assert tx["stage"] == "r_state" and tx["ok"] is False
    assert "split_pending" in _codes(tx["problems"])


def test_stale_r0_and_source_unsupported_are_zero_call():
    base = _base(A)
    base["record"]["plan_digest"] = "0" * 64
    out, svc, ctl = fx.run_entry(base, answer=fx.split_answer(A))
    assert out["stop_reason"] == "invalid_target"
    assert out["calls"] == 0 and svc.calls == [] and ctl.used == 0
    assert "split_authority_record_mismatch" in _codes(out["problems"]) or \
        "split_base_digest_mismatch" in _codes(out["problems"])

    base = _base(A)
    for _x in base["record"]["source_support"]:
        if _x["fact_uid"] == "f02":
            _x["verdict"] = "partial"
    out, svc, ctl = fx.run_entry(base, answer=fx.split_answer(A))
    assert out["stop_reason"] == "invalid_target"
    assert out["calls"] == 0 and svc.calls == [] and ctl.used == 0
    assert "split_authority_source_unsupported" in _codes(out["problems"]) or \
        "split_authority_record_mismatch" in _codes(out["problems"])


def test_children_count_out_of_range_stops_before_m():
    base = _base(A)
    one = [fl for fl in A["split"]["children"][:1]]
    out, svc, ctl = fx.run_entry(base, answer=fx.split_answer(A, kids=one))
    assert out["stop_reason"] == "split_failed"
    assert svc.calls == ["plan_content_repair_answer"]
    assert ctl.used == 0
    assert "repair_split_children_out_of_range" in _codes(
        fx.evidence_of(out).get("answer_problems") or [])


def test_m_call_exception_is_pending_and_not_retried():
    base = _base(A)

    class _BadM(fx.StubService):
        def generate(self, request):
            _name = str((request.json_schema or {}).get("name") or "")
            if _name == "fact_split_m.v1":
                self.calls.append(_name)
                raise RuntimeError("m boom")
            return super().generate(request)
    out, svc, ctl = fx.run_entry(base, answer=fx.split_answer(A),
                                 svc=_BadM(fx.split_answer(A)))
    assert out["stop_reason"] == "split_pending_adjudication"
    assert svc.calls == ["plan_content_repair_answer", "fact_split_m.v1"]
    assert ctl.used == 0
    assert out["plan"]["fact_uids"] == ["f01", "f02", "f03", "f04"]
    tx = fx.evidence_of(out)["transaction"]
    assert tx["stage"] == "m" and tx["status"] == "pending_adjudication"


def test_r_call_exception_is_split_failed_and_baseline_kept():
    base = _base(A)

    def _verify(view, digest):
        raise RuntimeError("r boom")
    out, svc, ctl = fx.run_entry(base, answer=fx.split_answer(A),
                                 verify=_verify)
    assert out["stop_reason"] == "split_failed"
    assert out["plan"]["fact_uids"] == ["f01", "f02", "f03", "f04"]
    assert svc.calls == ["plan_content_repair_answer", "fact_split_m.v1"]
    tx = fx.evidence_of(out)["transaction"]
    # 控制器把 verify 异常收成 failed record（不重发）⇒ 事务在 r_state 拒收。
    assert tx["stage"] in ("r", "r_state")
    assert ("split_pending" in _codes(tx["problems"])
            or "split_r_call_failed" in _codes(tx["problems"]))


# ---------------------------------------------------------------------------
# 局部采纳：deferred / unconfirmed / degraded
# ---------------------------------------------------------------------------
def test_deferred_protected_fact_returns_candidate_with_new_residuals():
    base = _base(A, partial_extra=("f01",))

    def _verify(view, digest):
        return fx.record_for(view, digest, partial=("f01",),
                             spans={"f02": A["split"]["parent_evidence_spans"]},
                             novel=base["novel"])
    out, svc, ctl = fx.run_entry(base, answer=fx.split_answer(A),
                                 verify=_verify)
    assert out["stop_reason"] == "split_applied_with_deferred", out
    assert out["plan"]["fact_uids"] == ["f01", "f02.1", "f02.2", "f03", "f04"]
    assert out["plan"]["fact_uids"] != ["f01", "f02", "f03", "f04"]
    assert _codes(out["problems"]) == ["verify_fact_unsupported"]
    assert out["problems"][0]["uid"] == "f01"
    assert ctl.used == 1
    ev = fx.evidence_of(out)
    assert _codes(ev["final_residuals"]) == ["verify_fact_unsupported"]
    assert ev["candidate_problems"] == ev["final_residuals"]
    # **普通事务的默认零残余门不变**：同一候选不传 acceptance 仍被完整残余拒收。
    tx_default = st.run_split_transaction(
        base["view"], base["plan"], base["record"], ev["split_ops"],
        scope=ev["split_scope"], ledger=ev["split_ledger"],
        novel_text=base["novel"],
        m_call=lambda _p: {"verdict": "supported", "missing": "", "reason": ""},
        m_call_id="default-gate-m",
        verify_candidate=lambda cand: _verify(
            cand, cv.plan_digest_of(pc.plan_from_view(cand))))
    assert tx_default["ok"] is False
    assert tx_default["stage"] == "acceptance"
    assert "verify_fact_unsupported" in _codes(tx_default["problems"])


def test_protected_fact_missing_changed_is_unconfirmed_not_degraded():
    base = _base(A, partial_extra=("f01",))

    def _verify(view, digest):
        return fx.record_for(view, digest, partial=("f01",),
                             missing={"f01": "另一个缺失"},
                             novel=base["novel"])
    out, svc, ctl = fx.run_entry(base, answer=fx.split_answer(A),
                                 verify=_verify)
    assert out["stop_reason"] == "split_pending_adjudication"
    assert out["plan"]["fact_uids"] == ["f01", "f02", "f03", "f04"]
    ev = fx.evidence_of(out)
    assert "split_adoption_unconfirmed" in _codes(
        ev["transaction"].get("problems") or [])


def test_protected_fact_contradicted_is_split_failed():
    base = _base(A, partial_extra=("f01",))

    def _verify(view, digest):
        rec = fx.record_for(view, digest, partial=("f01",),
                            novel=base["novel"])
        for _f in rec["facts"]:
            if _f["fact_uid"] == "f01":
                _f["verdict"] = "contradicted"
                _f["support_spans"] = []
                _f["obligations"][0]["verdict"] = "contradicted"
        return rec
    out, svc, ctl = fx.run_entry(base, answer=fx.split_answer(A),
                                 verify=_verify)
    assert out["stop_reason"] == "split_failed"
    assert out["plan"]["fact_uids"] == ["f01", "f02", "f03", "f04"]
    ev = fx.evidence_of(out)
    assert "split_adoption_degraded" in _codes(
        ev["transaction"].get("problems") or [])


def test_degraded_wins_over_unconfirmed_in_same_round():
    """v2.2 §4.1：同轮 unconfirmed + degraded ⇒ split_failed，不得落 pending."""
    base = _base(A, partial_extra=("f01", "f03"))

    def _verify(view, digest):
        rec = fx.record_for(view, digest, partial=("f01", "f03"),
                            missing={"f01": "另一个缺失"}, novel=base["novel"])
        for _f in rec["facts"]:
            if _f["fact_uid"] == "f03":
                _f["verdict"] = "contradicted"
                _f["support_spans"] = []
                _f["obligations"][0]["verdict"] = "contradicted"
        return rec
    out, svc, ctl = fx.run_entry(base, answer=fx.split_answer(A),
                                 verify=_verify)
    assert out["stop_reason"] == "split_failed"
    assert out["plan"]["fact_uids"] == ["f01", "f02", "f03", "f04"]
    codes = _codes(fx.evidence_of(out)["transaction"].get("problems") or [])
    assert "split_adoption_unconfirmed" in codes, "待裁决项仍要留档"
    assert "split_adoption_degraded" in codes


def test_empty_acceptance_problem_set_is_not_pending(monkeypatch):
    """v2.2 §4.1：判据是集合相等（{unconfirmed}），空集不得落 pending."""
    base = _base(A)
    monkeypatch.setattr(st, "run_split_transaction",
                        lambda *a, **k: {"ok": False, "stage": "acceptance",
                                         "problems": [], "m_calls": 1})
    out, svc, ctl = fx.run_entry(base, answer=fx.split_answer(A))
    assert out["stop_reason"] == "split_failed"
    assert out["plan"]["fact_uids"] == ["f01", "f02", "f03", "f04"]


def test_protected_fact_source_axis_unknown_to_contradicted_blocks():
    """v2.2 §4.2：来源轴 unknown→contradicted 阻断局部采纳（两轴分别比较）."""
    base = fx.baseline(A, partial_extra=("f01",),
                       source_verdict={"f01": "unknown"})
    assert "fact_basis_invalid" in _codes(
        o.plan_acceptance_problems(base["plan"], base["novel"]))

    def _verify(view, digest):
        return fx.record_for(view, digest, partial=("f01",),
                             source_verdict={"f01": "contradicted"},
                             novel=base["novel"])
    out, svc, ctl = fx.run_entry(base, answer=fx.split_answer(A),
                                 verify=_verify)
    assert out["stop_reason"] == "split_failed"
    assert out["plan"]["fact_uids"] == ["f01", "f02", "f03", "f04"]
    ev = fx.evidence_of(out)
    codes = _codes(ev["transaction"].get("problems") or [])
    assert "split_adoption_degraded" in codes
    # 对照：候选残余与本应被拦下的退化**同码同身份**——只按 (code, uid) 命中会误放。
    cand = ev.get("split_candidate") or {}
    assert "fact_basis_invalid" in _codes(
        o.plan_acceptance_problems(cand, base["novel"]))


def test_protected_fact_axes_are_independent():
    """两轴各自判定：任一轴退化即 degraded；两轴不退化但 missing 变才是 unconfirmed."""
    base = fx.baseline(A, partial_extra=("f01",))
    view = base["view"]
    cand_view = copy.deepcopy(view)
    cand_plan = pc.plan_from_view(cand_view)
    cand_digest = cv.plan_digest_of(cand_plan)

    def _probs(*, cand_rec=None):
        return split_adoption.adoption_problems(
            base_view=view, candidate_view=cand_view,
            base_plan=base["plan"], candidate_plan=cand_plan,
            base_record=base["record"], candidate_record=cand_rec,
            parent_uid="f02", child_uids=[],
            baseline_problems=[], candidate_problems=[])

    _src_cand = fx.record_for(cand_view, cand_digest, partial=("f01",),
                              source_verdict={"f01": "contradicted"},
                              novel=base["novel"])
    assert "split_adoption_degraded" in _codes(_probs(cand_rec=_src_cand))

    _disp_cand = fx.record_for(cand_view, cand_digest, partial=("f01",),
                               novel=base["novel"])
    for _f in _disp_cand["facts"]:
        if _f["fact_uid"] == "f01":
            _f["verdict"] = "contradicted"
            _f["support_spans"] = []
            _f["obligations"][0]["verdict"] = "contradicted"
    assert "split_adoption_degraded" in _codes(_probs(cand_rec=_disp_cand))

    _miss_cand = fx.record_for(cand_view, cand_digest, partial=("f01",),
                               missing={"f01": "另一个缺失"}, novel=base["novel"])
    _p = _probs(cand_rec=_miss_cand)
    assert "split_adoption_unconfirmed" in _codes(_p)
    assert "split_adoption_degraded" not in _codes(_p)


def test_candidate_r2_identity_and_three_leg_metering():
    """R0 只服务基线；候选取得一次 R2；三类调用分开计量.

    控制器按**生产同源装配**注入 `validate_record`（同 `outline.py` 的
    `_protocol_problems`），正例因此同时证明完整准入链可通过。
    """
    base = _base(A)
    _validated = []
    _check = _prod_validate(base)

    def _validate(rec, view, digest):
        _validated.append(1)
        return _check(rec, view, digest)
    ctl = fx.controller(base, validate_record=_validate)
    seeds = []
    _seed = ctl.seed
    ctl.seed = lambda *a, **k: seeds.append(1) or _seed(*a, **k)
    out, svc, ctl = fx.run_entry(base, answer=fx.split_answer(A), ctl=ctl)
    assert out["stop_reason"] == "ok"
    assert _validated, "候选 R2 必须经既有准入校验（不是空装配）"
    assert len(seeds) == 1 and ctl.used == 1
    assert svc.calls.count("plan_content_repair_answer") == 1
    assert svc.calls.count("fact_split_m.v1") == 1
    cand_view, _vp = pc.view_from_plan(out["plan"])
    assert not _vp
    cand_digest = cv.plan_digest_of(pc.plan_from_view(cand_view))
    assert cand_digest != base["digest"], "拆分换身份，候选摘要不与 R0 相同"
    assert out["plan"]["plan_verification"]["plan_digest"] == cand_digest


def test_r0_cannot_stand_in_for_candidate_record():
    """R0 冒充候选记录：由既有完整准入链拒收，不只靠摘要比较."""
    base = _base(A)
    ctl = fx.controller(base, verify=lambda view, d: base["record"],
                        validate_record=_prod_validate(base))
    out, svc, ctl = fx.run_entry(base, answer=fx.split_answer(A), ctl=ctl)
    assert out["stop_reason"] == "split_failed"
    assert out["plan"]["fact_uids"] == ["f01", "f02", "f03", "f04"]
    cand = (fx.evidence_of(out) or {}).get("split_candidate") or {}
    rec = cand.get("plan_verification") or {}
    assert rec.get("status") != "ok"
    assert any(_c.startswith("verify_") for _c in _codes(rec.get("problems"))), \
        "准入链必须指出 R0 记录覆盖不了候选事实，而不是静默复用"


def test_structural_freeze_and_unmapped_residual_are_rejected():
    base = _base(A)
    view = base["view"]
    cand = copy.deepcopy(view)
    cand["frozen_facts"][0]["claim"] = "被改过的主体"
    cand_plan = pc.plan_from_view(cand)
    cand_record = fx.record_for(cand, cv.plan_digest_of(cand_plan),
                                novel=base["novel"])
    probs = split_adoption.adoption_problems(
        base_view=view, candidate_view=cand, base_plan=base["plan"],
        candidate_plan=cand_plan, base_record=base["record"],
        candidate_record=cand_record, parent_uid="f02",
        child_uids=["f02.1", "f02.2"],
        baseline_problems=[], candidate_problems=[
            {"code": "budget_infeasible", "uid": "", "path": "made.up"}])
    codes = _codes(probs)
    assert "split_adoption_frozen_modified" in codes
    assert "split_adoption_unmapped_residual" in codes


def test_affected_non_target_fact_degradation_is_rejected():
    """子载体在 base view 上还承载 f01 时，f01 是受影响事实；R2 退化必须拒收."""
    base = fx.baseline(A)
    view = copy.deepcopy(base["view"])
    for _b in view["fact_bindings"]:
        if _b["fact_uid"] == "f01":
            _b["carriers"] = [{"kind": "visual", "uid": "b02"}]
    from shared import fact_metadata as fm
    plan = pc.plan_from_view(view)
    plan[fm.LEGACY_ACCEPTANCE_FIELD] = fm.LEGACY_ACCEPTANCE_VERSION
    plan = o.normalize_plan_ranges(plan)[0]
    digest = cv.plan_digest_of(plan)
    record = fx.record_for(view, digest, partial=("f02",),
                           spans={"f02": A["split"]["parent_evidence_spans"]},
                           novel=base["novel"])
    plan["plan_verification"] = record
    affected_base = {"sample": A, "novel": base["novel"], "plan": plan,
                     "view": view, "digest": digest, "record": record,
                     "parent": "f02"}

    def _verify(cand_view, cand_digest):
        _rec = fx.record_for(cand_view, cand_digest, novel=base["novel"])
        for _f in _rec["facts"]:
            if _f["fact_uid"] == "f01":
                _f["verdict"] = "contradicted"
                _f["support_spans"] = []
                _f["obligations"][0]["verdict"] = "contradicted"
        return _rec
    out, svc, ctl = fx.run_entry(affected_base, answer=fx.split_answer(A),
                                 verify=_verify)
    assert out["stop_reason"] == "split_failed"
    assert out["plan"]["fact_uids"] == ["f01", "f02", "f03", "f04"]
    assert "split_adoption_affected_unsupported" in _codes(
        fx.evidence_of(out)["transaction"].get("problems") or [])


def test_split_r_disables_protocol_correction():
    base = fx.baseline(A)
    repairs = []
    ctl = VerificationController(
        verify=lambda view, digest: {"nope": True},
        novel_digest=base["record"]["novel_digest"],
        max_verifications=2, rule_version=cv.DEFAULT_RULE_VERSION,
        repair=lambda *a, **k: repairs.append(1) or None)
    rec = ctl.record_for(base["view"], base["digest"], allow_correction=False)
    assert rec["status"] != "ok"
    assert repairs == [], "拆分 R 不得触发协议纠正重发"
    assert ctl.used == 1


# ---------------------------------------------------------------------------
# terminal 提交前守卫
# ---------------------------------------------------------------------------
def _terminal_controller(*, deadline_sec, revise_delay=0.0, accept_delay=0.0):
    def accept(plan, novel_text=""):
        if plan.get("climax") == "b":
            if accept_delay:
                time.sleep(accept_delay)
            return []
        return [{"code": "needs_work"}]

    def revise(plan, problems, budget, rejections):
        if revise_delay:
            time.sleep(revise_delay)
        return {"kind": "applied", "plan": {"climax": "b"}, "detail": "split",
                "call_id": "c1",
                "terminal": {"stop_reason": "split_applied_with_deferred",
                             "applied": True}}

    budget = pr.RevisionBudget(max_calls=3, deadline_sec=deadline_sec)
    return pr.RevisionController(accept=accept, revise=revise, budget=budget,
                                 log=lambda *a, **k: None)


def test_terminal_applied_expiring_after_revise_returns_baseline():
    out = _terminal_controller(deadline_sec=0.05, revise_delay=0.08).run(
        {"climax": "a"}, "novel")
    assert out["stop_reason"] == "deadline_exhausted"
    assert out["plan"] == {"climax": "a"}
    assert out["deadline_exhausted"] is True


def test_terminal_applied_expiring_during_final_accept_returns_baseline():
    out = _terminal_controller(deadline_sec=0.05, accept_delay=0.08).run(
        {"climax": "a"}, "novel")
    assert out["stop_reason"] == "deadline_exhausted"
    assert out["plan"] == {"climax": "a"}


def test_normal_illegal_still_continues_by_default():
    calls = {"n": 0}
    def accept(plan, novel_text=""):
        return [] if plan.get("climax") == "c" else [{"code": "needs_work"}]
    def revise(plan, problems, budget, rejections):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"kind": "illegal", "detail": "bad", "call_id": "c1"}
        return {"kind": "applied", "plan": {"climax": "c"}, "detail": "",
                "call_id": "c2"}
    budget = pr.RevisionBudget(max_calls=3, deadline_sec=60)
    out = pr.RevisionController(accept=accept, revise=revise, budget=budget,
                                log=lambda *a, **k: None).run({"climax": "a"}, "n")
    assert out["stop_reason"] == "ok"
    assert calls["n"] == 2
