# -*- coding: utf-8 -*-
"""M04（2026-11-15）：审计预算准入 / 冻结候选续验 / 四项计时（零模型 / 零网络）.

用**模拟时钟**验证准入、耗尽与恢复，不依赖真实时间、不跑真实审计：

* 剩余时间必须容纳「本次动作 + 最终生效审计上限 + 提交收尾」；
* 预留用配置 cap，不能用已被剩余预算截短过的 allowance（否则判据恒真）；
* 预算不足 → `budget_exhausted` + 冻结候选写隔离根；
* 续验核对输入 revision / 审计配置 / 规则身份，**不借用旧审计结果**；
* 剩余预算 / 审计允许时长 / 服务端等待 / 流式耗时分别记录，不可得为 None。

夹具匿名自足，干净检出可运行。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen.repair_budget import (        # noqa: E402
    SimClock, admit_action, audit_reserve_sec, budget_timing_record,
    freeze_candidate, verify_frozen_candidate,
)

CAP = 300.0
RESERVE = 60.0
COST = 180.0
_CFG = {"audit_timeout_cap_sec": CAP, "commit_reserve_sec": RESERVE,
        "total_budget_sec": 1200.0, "audit_scope": "step01",
        "rule_source_fingerprint": "rule-fp-001"}
_RULE = "rule-fp-001"
_SCRIPT = {"hook": None, "shots": [
    {"index": 5, "subtitle": "原文", "keyframes": [{"t_sec": 1.0, "desc": "第一段"}]}]}


def _admit(remaining, cost=COST, cap=CAP, reserve=RESERVE):
    return admit_action(remaining_budget_sec=remaining, action_cost_sec=cost,
                        audit_cap_sec=cap, commit_reserve_sec=reserve)


def _frozen(admission=None, script=None, cfg=None, rule=_RULE):
    return freeze_candidate(
        script=script if script is not None else _SCRIPT,
        input_revision="rev-001",
        audit_config=cfg if cfg is not None else _CFG,
        rule_fingerprint=rule,
        admission=admission or _admit(100.0),
        action="repair_rounds", output_root=".dsh/step01_budget_frozen/test")


# ---------------------------------------------------------------------------
# ① 模拟时钟与准入
# ---------------------------------------------------------------------------
def test_sim_clock_is_deterministic_and_remaining_decreases():
    c = SimClock(total_budget_sec=1200.0)
    assert c.remaining() == 1200.0
    c.advance(1000.0)
    assert c.remaining() == 200.0
    c.advance(500.0)
    assert c.remaining() == -300.0        # 允许读到负值，判据侧按 0 截断
    assert SimClock(1200.0).advance(1000.0).remaining() == 200.0


def test_unbounded_budget_keeps_legacy_behavior():
    a = _admit(None)
    assert a.allowed and a.reason == "no_total_budget_cap"
    assert a.freeze_required is False and a.remaining_budget_sec is None


def test_admission_allows_when_time_covers_action_audit_and_commit():
    a = _admit(540.0)          # 180 + 300 + 60 = 540（恰好够）
    assert a.allowed is True and a.reason == "budget_ok"
    assert a.required_sec == 540.0
    assert a.audit_reserve_sec == 360.0
    assert a.projected_remaining_sec == 360.0


def test_admission_rejects_one_second_short_of_full_audit_reserve():
    a = _admit(539.0)
    assert a.allowed is False and a.reason == "budget_exhausted"
    assert a.freeze_required is True
    assert a.required_sec == 540.0


def test_audit_reserve_uses_config_cap_not_truncated_allowance():
    # 用户指出的缺口：若拿「已被剩余预算截短过的 allowance」当预留，
    # 剩余越少预留越小，判据恒真。这里必须用 cap。
    assert audit_reserve_sec(CAP, RESERVE) == 360.0
    _truncated_allowance = 126.0        # run i 的形态
    assert audit_reserve_sec(_truncated_allowance, RESERVE) == 186.0
    a = _admit(200.0)
    assert a.audit_cap_sec == CAP and a.audit_reserve_sec == 360.0
    assert a.allowed is False, "即使截短 allowance 到 126s，也必须拒绝"


def test_simulated_run_exhaustion_and_recovery():
    """1200s 预算被前序阶段烧到 200s → 拒绝；新一轮预算恢复 → 准入 + 续验放行."""
    clock = SimClock(1200.0).advance(1000.0)
    a1 = _admit(clock.remaining())
    assert a1.allowed is False and a1.freeze_required is True
    rec = _frozen(a1)
    assert rec["terminal"] == "budget_exhausted"

    # 恢复：新的独立 run（时钟重置）给出充足预算
    clock2 = SimClock(1200.0)
    a2 = _admit(clock2.remaining())
    assert a2.allowed is True
    out = verify_frozen_candidate(
        rec, script=_SCRIPT, input_revision="rev-001",
        audit_config=_CFG, rule_fingerprint=_RULE, budget_admission=a2)
    assert out["eligible"] is True, out
    assert out["checks"]["budget_recovered"] is True
    assert out["prior_audit_reused"] is False


# ---------------------------------------------------------------------------
# ② 冻结候选
# ---------------------------------------------------------------------------
def test_frozen_candidate_is_deterministic_and_carries_no_audit_payload():
    a = _admit(100.0)
    r1, r2 = _frozen(a), _frozen(a)
    assert json.dumps(r1, sort_keys=True, ensure_ascii=False) == \
        json.dumps(r2, sort_keys=True, ensure_ascii=False)
    assert "generated_at" not in r1 and "created_at" not in r1
    assert r1["kind"] == "budget_exhausted" and r1["terminal"] == "budget_exhausted"
    assert r1["prior_audit_reused"] is False
    assert not any(k in r1 for k in ("audit_payload", "issues", "status"))
    assert r1["admission"]["reason"] == "budget_exhausted"
    assert r1["admission"]["freeze_required"] is True


def test_frozen_candidate_records_isolated_output_root():
    r = _frozen()
    assert ".dsh/" in r["output_root"] and "/output" not in r["output_root"]


# ---------------------------------------------------------------------------
# ③ 续验三项身份
# ---------------------------------------------------------------------------
def test_verify_rejects_changed_script_identity():
    rec = _frozen()
    changed = copy.deepcopy(_SCRIPT)
    changed["shots"][0]["subtitle"] = "被改过的稿"
    out = verify_frozen_candidate(
        rec, script=changed, input_revision="rev-001",
        audit_config=_CFG, rule_fingerprint=_RULE)
    assert out["eligible"] is False
    assert out["checks"]["script_digest_ok"] is False
    assert out["prior_audit_reused"] is False


def test_verify_rejects_revision_mismatch_even_with_same_digest():
    rec = _frozen()
    out = verify_frozen_candidate(
        rec, script=_SCRIPT, input_revision="another-revision",
        audit_config=_CFG, rule_fingerprint=_RULE)
    assert out["checks"]["input_revision_ok"] is False
    assert out["eligible"] is False


def test_verify_rejects_audit_config_or_rule_identity_change():
    rec = _frozen()
    cfg2 = dict(_CFG, audit_timeout_cap_sec=180.0)
    out = verify_frozen_candidate(
        rec, script=_SCRIPT, input_revision="rev-001",
        audit_config=cfg2, rule_fingerprint=_RULE)
    assert out["checks"]["audit_config_ok"] is False
    assert out["eligible"] is False

    out2 = verify_frozen_candidate(
        rec, script=_SCRIPT, input_revision="rev-001",
        audit_config=_CFG, rule_fingerprint="different-rules")
    assert out2["checks"]["rule_identity_ok"] is False
    assert out2["eligible"] is False


def test_verify_rejects_record_smuggling_a_previous_audit_result():
    rec = _frozen()
    rec["audit_payload"] = {"status": "ok", "issues": []}
    out = verify_frozen_candidate(
        rec, script=_SCRIPT, input_revision="rev-001",
        audit_config=_CFG, rule_fingerprint=_RULE)
    assert out["checks"]["no_prior_audit_payload"] is False
    assert out["eligible"] is False
    assert out["prior_audit_reused"] is False


def test_verify_requires_budget_recovery_when_admission_given():
    rec = _frozen(_admit(100.0))
    out = verify_frozen_candidate(
        rec, script=_SCRIPT, input_revision="rev-001",
        audit_config=_CFG, rule_fingerprint=_RULE,
        budget_admission=_admit(100.0))
    assert out["checks"]["budget_recovered"] is False
    assert out["eligible"] is False, "预算没恢复不得进入完整审计链"


# ---------------------------------------------------------------------------
# ④ 四项计时分别记录
# ---------------------------------------------------------------------------
def test_budget_timing_records_four_fields_separately():
    r = budget_timing_record(
        remaining_budget_sec=612.4, audit_allowance_sec=300,
        server_wait_sec=126.123, stream_elapsed_sec=98.7,
        stream_timing_source="agent_plan_trace", phase="final_audit")
    for k in ("remaining_budget_sec", "audit_allowance_sec",
              "server_wait_sec", "stream_elapsed_sec"):
        assert k in r
    assert r["remaining_budget_sec"] == 612.4
    assert r["audit_allowance_sec"] == 300.0
    assert r["server_wait_sec"] == 126.123
    assert r["stream_elapsed_sec"] == 98.7
    assert r["stream_timing_source"] == "agent_plan_trace"


def test_unavailable_timing_is_none_and_labelled_never_estimated():
    r = budget_timing_record(
        remaining_budget_sec=None, audit_allowance_sec=None,
        server_wait_sec=None, stream_elapsed_sec=None)
    assert r["stream_elapsed_sec"] is None and r["server_wait_sec"] is None
    assert r["stream_timing_source"] == "provider_not_instrumented"


# ---------------------------------------------------------------------------
# ⑤ 生产桥接（真实 main / audit / provider 契约，不打模型）
# ---------------------------------------------------------------------------
def test_main_reserve_config_is_stable_without_runtime_readings():
    import step01_script_gen.main as m
    stable = m._final_audit_reserve_config()
    runtime = m._final_audit_reserve_config(include_runtime=True)
    assert "left_sec" not in stable and "audit_allowance_sec" not in stable
    assert "left_sec" in runtime and "audit_allowance_sec" in runtime
    assert stable["audit_timeout_cap_sec"] >= 1
    assert stable["commit_reserve_sec"] == m.AUDIT_COMMIT_RESERVE_SEC
    for k, v in stable.items():
        assert runtime[k] == v, "运行时读数不得改变配置身份"


def test_main_action_admission_uses_existing_budget_functions(monkeypatch):
    import step01_script_gen.main as m
    monkeypatch.setattr(m, "_budget_left_sec", lambda: 200.0)
    monkeypatch.setattr(m, "_total_budget_sec", lambda: 1200.0)
    rec = m._action_budget_admission("repair_rounds", 180.0)
    assert rec["allowed"] is False and rec["reason"] == "budget_exhausted"
    assert rec["audit_config"]["audit_timeout_cap_sec"] >= 1
    assert rec["required_sec"] > 200.0
    assert rec["action"] == "repair_rounds"

    monkeypatch.setattr(m, "_budget_left_sec", lambda: 900.0)
    assert m._action_budget_admission("repair_rounds", 180.0)["allowed"] is True


def test_audit_budget_timing_bridges_audit_result():
    import step01_script_gen.main as m
    from step01_script_gen.audit import AuditResult
    r = AuditResult("ok", [], timing={
        "server_wait_sec": 126.0, "stream_elapsed_sec": 98.0,
        "stream_timing_source": "agent_plan_trace",
        "provider_timing": {"done_at": 98.0, "events": 12}})
    out = m._audit_budget_timing(r, allowance_sec=300, left_sec=612.0)
    assert out["remaining_budget_sec"] == 612.0
    assert out["audit_allowance_sec"] == 300.0
    assert out["server_wait_sec"] == 126.0
    assert out["stream_elapsed_sec"] == 98.0
    assert out["stream_timing_source"] == "agent_plan_trace"
    assert out["phase"] == "final_audit"


def test_audit_budget_timing_without_provider_instrumentation_is_none():
    import step01_script_gen.main as m
    from step01_script_gen.audit import AuditResult
    r = AuditResult("request_failed", [], timing={
        "server_wait_sec": 12.0, "stream_elapsed_sec": None,
        "stream_timing_source": "provider_not_instrumented"})
    out = m._audit_budget_timing(r, allowance_sec=300, left_sec=100.0)
    assert out["server_wait_sec"] == 12.0
    assert out["stream_elapsed_sec"] is None
    assert out["stream_timing_source"] == "provider_not_instrumented"


def test_text_generation_result_has_optional_timing_field():
    from _text_gen.models import TextGenerationResult
    r = TextGenerationResult(success=True, text="x")
    assert r.timing is None
    r2 = TextGenerationResult(success=True, text="x",
                              timing={"done_at": 1.5, "events": 4})
    assert r2.timing["events"] == 4


def test_agent_plan_parse_response_attaches_stream_timing():
    """provider 已有的阶段轨迹必须挂到结果上（M04 流式耗时的真实来源）."""
    from _text_gen.agent_plan_provider import AgentPlanTextService

    class _Resp:
        text = "{}"

        def json(self):
            return {"choices": [{"message": {"content": "ok"},
                                 "finish_reason": "stop"}],
                    "usage": {}}

    svc = object.__new__(AgentPlanTextService)
    trace = {"headers_at": 1.0, "first_event_at": 2.0,
             "done_at": 9.5, "events": 30}
    out = svc._parse_response(_Resp(), model="m", trace=trace)
    assert out.success is True
    assert out.timing["done_at"] == 9.5
    assert out.timing["events"] == 30
    # 非流式 / 无轨迹时不得编造
    out2 = svc._parse_response(_Resp(), model="m", trace={})
    assert out2.timing is None


def test_freeze_writes_to_isolated_root_only(monkeypatch, tmp_path):
    import step01_script_gen.main as m
    monkeypatch.setattr(m, "_budget_freeze_root", lambda run_id="": str(tmp_path))
    monkeypatch.setattr(m, "_budget_left_sec", lambda: 100.0)
    adm = m._action_budget_admission("repair_rounds", 180.0)
    assert adm["reason"] == "budget_exhausted"
    rec = m._freeze_budget_exhausted(
        _SCRIPT, action="repair_rounds", admission=adm)
    assert rec["written"] is True
    assert (tmp_path / "frozen_candidate.json").exists()
    assert (tmp_path / "frozen_script.json").exists()
    saved = json.loads((tmp_path / "frozen_candidate.json").read_text(
        encoding="utf-8"))
    assert saved["terminal"] == "budget_exhausted"
    assert saved["prior_audit_reused"] is False
    assert json.loads((tmp_path / "frozen_script.json").read_text(
        encoding="utf-8")) == _SCRIPT
    # 续验对同一份稿通过（不打模型、不借用旧审计）
    out = m._verify_budget_resume(saved, _SCRIPT, admission=None)
    assert out["eligible"] is True, out
    assert out["prior_audit_reused"] is False
