# -*- coding: utf-8 -*-
"""M03（2026-11-15）：有界结构化反馈的单元验收（零模型 / 零网络）.

覆盖用户《Step01 修复闭环收敛方案》M03 的策略表与状态机：

* 七字段契约、未知 code 的 fail-closed、文本不参与分类；
* 每个冻结目标集合最多一次答卷纠正机会；
* 相同拒因 + 同一补丁重复 ⇒ 停止该目标；
* 工程故障优先阻断依赖动作；混合拒因逐项记录；
* 保护字段越权 / 合法应用但目标未解决 ⇒ 不额外发纠正，但保留真实残余；
* `transaction_feedback_records` 的 reason 映射与 writer 观测异常；
* 真实 `_apply_shot_replacements` 的**生产者分类**（不解析错误文本）。

夹具为匿名自足样本，干净检出可运行。
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen.repair_feedback import (      # noqa: E402
    RECORD_FIELDS, POLICY, PHASE_APPLY, PHASE_TRANSACTION, PHASE_ENGINEERING,
    answer_signature, feedback_record, frozen_signature, plan_feedback_state,
    render_feedback, transaction_feedback_records,
)

_TARGETS = ["shot_5", "shot_9"]
_REV = "rev-frozen-001"


def _rec(code="unauthorized_path", target="shot_5", path="subtitle", **kw):
    return feedback_record(code=code, target=target,
                           phase=kw.pop("phase", PHASE_APPLY),
                           field_path=path, allowed_paths=["keyframes"],
                           text=kw.pop("text", "示例原因文本"), **kw)


def _plan(records, prior=None, targets=_TARGETS, answer="sig-A"):
    return plan_feedback_state(
        records=records, prior_state=prior, frozen_targets=targets,
        source_revision=_REV, answer_sig=answer)


# ---------------------------------------------------------------------------
# ① 记录契约
# ---------------------------------------------------------------------------
def test_record_has_exactly_the_seven_contract_fields_plus_display_only_text():
    r = _rec()
    for k in RECORD_FIELDS:
        assert k in r, k
    assert r["retryable"] is True and r["terminal"] is False
    assert isinstance(r["allowed_paths"], list) and isinstance(r["details"], dict)


def test_free_text_never_changes_classification():
    a = _rec(text="完全无关的散文", code="unauthorized_path")
    b = _rec(text="长度不等 ≠ 2", code="unauthorized_path")
    assert a["code"] == b["code"] and a["retryable"] == b["retryable"]
    assert a["terminal"] == b["terminal"] and a["hint"] == b["hint"]


def test_unknown_code_is_fail_closed_engineering_failure():
    r = _rec(code="model_please_retry_anyway")
    assert r["code"] == "engineering_failure"
    assert r["retryable"] is False and r["terminal"] is True
    assert _plan([r])["stop_reason"] == "engineering_failure"


@pytest.mark.parametrize("code", [
    "unauthorized_path", "pair_shape_invalid", "frame_identity_unproven",
    "shape_invalid", "target_not_picked", "layout_structure_invalid",
    "new_blocking",
])
def test_user_policy_retryable_codes(code):
    assert POLICY[code][0] is True
    assert _plan([_rec(code=code)])["correction_granted"] is True


@pytest.mark.parametrize("code", ["protected_field", "no_target_resolved"])
def test_user_policy_non_retryable_codes_are_not_terminal(code):
    s = _plan([_rec(code=code)])
    assert s["correction_granted"] is False
    assert s["terminal"] is False, "保护字段/真实残余不是工程故障"
    assert s["feedback"], "反馈与残余仍须展示给下一次请求/提交层"


@pytest.mark.parametrize("code", [
    "engineering_failure", "budget_exhausted", "duplicate_rejection"])
def test_user_policy_terminal_codes(code):
    assert POLICY[code][1] is True
    assert _plan([_rec(code=code)])["terminal"] is True


# ---------------------------------------------------------------------------
# ② 展示层
# ---------------------------------------------------------------------------
def test_render_shows_code_path_allowed_paths_and_hint():
    out = render_feedback([_rec()])
    assert "unauthorized_path" in out and "shot_5" in out
    assert "subtitle" in out and "keyframes" in out
    assert POLICY["unauthorized_path"][2] in out
    assert render_feedback([]) == ""


def test_render_text_is_display_only_not_a_decision_input():
    r = _rec(text="长度不等")
    s1 = _plan([r], answer="sig-A")
    r2 = dict(r, text="完全不同的措辞")
    s2 = _plan([r2], answer="sig-A")
    assert s1["correction_granted"] == s2["correction_granted"]
    assert s1["stop_reason"] == s2["stop_reason"]


# ---------------------------------------------------------------------------
# ③ 稳定签名
# ---------------------------------------------------------------------------
def test_frozen_signature_is_order_independent_and_set_sensitive():
    assert frozen_signature(["shot_9", "shot_5"]) == frozen_signature(_TARGETS)
    assert frozen_signature(_TARGETS) != frozen_signature(["shot_5"])


def test_answer_signature_is_key_order_independent():
    assert answer_signature({"a": 1, "b": 2}) == answer_signature({"b": 2, "a": 1})
    assert answer_signature({"a": 1}) != answer_signature({"a": 2})


# ---------------------------------------------------------------------------
# ④ 有界纠正机会
# ---------------------------------------------------------------------------
def test_first_rejection_grants_exactly_one_correction():
    s = _plan([_rec()])
    assert s["correction_granted"] is True and s["used_correction"] is True
    assert s["terminal"] is False and s["stop_reason"] == ""
    assert s["source_revision"] == _REV
    assert s["frozen_signature"] == frozen_signature(_TARGETS)
    assert s["answer_signature"] == "sig-A"


def test_same_frozen_set_cannot_get_a_second_correction():
    first = _plan([_rec()])
    second = _plan([_rec()], prior=first, answer="sig-B")
    assert second["correction_granted"] is False
    assert second["terminal"] is True
    assert second["stop_reason"] == "correction_budget_exhausted"


def test_different_frozen_set_gets_its_own_correction_budget():
    first = _plan([_rec()])
    second = _plan([_rec()], prior=first, targets=["shot_5"], answer="sig-B")
    assert second["correction_granted"] is True, "冻结目标集合变了，额度另计"


def test_identical_reason_and_identical_patch_stops_the_target():
    first = _plan([_rec()], answer="sig-A")
    second = _plan([_rec()], prior=first, answer="sig-A")
    assert second["stop_reason"] == "duplicate_rejection"
    assert second["terminal"] is True
    assert "shot_5" in second["excluded"]
    assert "duplicate_rejection" in second["feedback"]
    assert all(r["code"] != "duplicate_rejection"
               for r in second["feedback_records"]), \
        "停止判据只进展示文本，不得污染生产者记录"


def test_same_reason_but_different_patch_is_not_a_duplicate():
    first = _plan([_rec()], answer="sig-A")
    second = _plan([_rec()], prior=first, answer="sig-B")
    # 同一冻结集合的额度已用完，但**不**应被误判成「相同补丁重复」
    assert second["stop_reason"] == "correction_budget_exhausted"


# ---------------------------------------------------------------------------
# ⑤ 混合拒因 / 工程故障优先
# ---------------------------------------------------------------------------
def test_engineering_failure_wins_over_retryable_records():
    records = [_rec(), _rec(code="engineering_failure",
                            phase=PHASE_ENGINEERING)]
    s = _plan(records)
    assert s["terminal"] is True and s["correction_granted"] is False
    assert s["stop_reason"] == "engineering_failure"
    assert [r["code"] for r in s["feedback_records"]] == [
        "unauthorized_path", "engineering_failure"], "混合拒因逐项保留"


def test_mixed_retryable_records_are_all_kept():
    records = [_rec(), _rec(code="frame_identity_unproven",
                            path="frame_layouts.kfs"),
               _rec(code="protected_field", path="subtitle")]
    s = _plan(records)
    assert s["correction_granted"] is True
    assert {r["code"] for r in s["feedback_records"]} == {
        "unauthorized_path", "frame_identity_unproven", "protected_field"}


def test_new_blocking_excludes_only_that_target():
    records = [_rec(code="new_blocking", target="shot_9", path=""),
               _rec(code="new_blocking", target="shot_5", path="")]
    s = _plan(records)
    assert s["excluded"] == ["shot_5", "shot_9"]


# ---------------------------------------------------------------------------
# ⑥ 事务裁决生产者
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("reason,code", [
    ("checker_failed", "engineering_failure"),
    ("postprocess_check_failed", "engineering_failure"),
    ("new_blocking", "new_blocking"),
    ("locked_changed", "protected_field"),
    ("locked_field_changed", "protected_field"),
    ("no_target_resolved", "no_target_resolved"),
    ("layout_structure_invalid", "layout_structure_invalid"),
    ("未登记的裁决原因", "engineering_failure"),
])
def test_transaction_reason_mapping(reason, code):
    recs = transaction_feedback_records(reason=reason, targets=["shot_5"])
    assert recs[0]["code"] == code, reason
    assert recs[0]["phase"] == PHASE_TRANSACTION


def test_transaction_records_carry_allowed_paths_and_real_residual():
    recs = transaction_feedback_records(
        reason="no_target_resolved", targets=["shot_5"],
        allowed_by_target={"shot_5": {"keyframes", "frame_layouts.kfs"}},
        details={"resolved": 0, "before": 3, "after": 3})
    r = recs[0]
    assert set(r["allowed_paths"]) == {"keyframes", "frame_layouts.kfs"}
    assert r["details"]["before"] == 3 and r["details"]["resolved"] == 0
    assert r["retryable"] is False and r["terminal"] is False


def test_writer_observation_error_becomes_engineering_terminal():
    recs = transaction_feedback_records(
        reason="layout_structure_invalid", targets=["shot_5"],
        frame_locator_issues=["帧结构被 writer 改动"],
        observation_errors=[{"writer": "run_hard_fix", "error": "回调异常"}])
    s = _plan(recs)
    assert s["terminal"] is True and s["stop_reason"] == "engineering_failure"
    eng = [r for r in recs if r["phase"] == PHASE_ENGINEERING]
    assert len(eng) == 2
    assert eng[-1]["details"]["writer"] == "run_hard_fix"


# ---------------------------------------------------------------------------
# ⑦ 真实应用器的生产者分类（不解析原因文本）
# ---------------------------------------------------------------------------
def _entry(name="甲", pose="姿态"):
    return {"name": name, "pose": pose, "gaze_target": "画上", "on_screen": True}


def _shot():
    return {
        "index": 5, "duration": 8.0, "duration_sec": 8.0,
        "narration_segment": "旁白", "subtitle": "字幕",
        "scene": "场景", "location": "地点", "main_subject": "甲",
        "prompt": "中景", "variation_type": "small",
        "ff_desc": "起始", "lf_desc": "结束",
        "motion_desc": "0-3秒：第一段；3-6秒：第二段",
        "keyframes": [
            {"t_sec": 1.0, "desc": "第一段", "stage": "pre"},
            {"t_sec": 3.0, "desc": "第二段", "stage": "approach"}],
        "frame_layouts": {"ff": [], "lf": [], "kfs": [
            [_entry(pose="姿态0")], [_entry(pose="姿态1")]]},
        "temporal_plan": {"events": [
            {"start_sec": 0.0, "end_sec": 3.0, "description": "第一段",
             "source_quote": ""}]},
    }


def _apply(items, allowed=("keyframes", "frame_layouts", "frame_layouts.kfs")):
    from step01_script_gen.main import _apply_shot_replacements
    script = {"hook": None, "fps": 24, "total_duration": 8,
              "shots": [_shot()]}
    out: list = []
    merged, n, rejected = _apply_shot_replacements(
        script, items, {"shot_5"},
        allowed_by_target={"shot_5": set(allowed)},
        expected_targets={"5"}, rejections_out=out)
    return merged, n, rejected, out


@pytest.mark.parametrize("item,code,path", [
    ({"shot_id": "5", "updates": [
        {"field_path": "subtitle", "value": "越权"}]},
     "unauthorized_path", "subtitle"),
    ({"shot_id": "5", "keyframe_additions": [
        {"frame": {"t_sec": 2.0, "desc": "补帧", "stage": "approach"}}]},
     "pair_shape_invalid", "keyframe_additions[].frame"),
])
def test_applier_emits_producer_classified_records(item, code, path):
    merged, n, rejected, out = _apply([item])
    assert n == 0 and rejected, "该答卷必须被拒"
    assert len(out) == 1, out
    r = out[0]
    assert r["code"] == code, (r, rejected)
    assert r["target"] == "shot_5" and r["field_path"] == path, r
    assert r["retryable"] is True and r["phase"] == PHASE_APPLY
    assert merged == {"hook": None, "fps": 24, "total_duration": 8,
                      "shots": [_shot()]}, "拒收不得改动基线"


def test_applier_rejections_out_is_cleared_per_call():
    merged, n, _rejected, out = _apply([
        {"shot_id": "5", "updates": [
            {"field_path": "subtitle", "value": "越权"}]}])
    assert len(out) == 1
    # 同一个容器再跑一次合法答卷，不得残留上一次的拒收记录
    out2: list = []
    from step01_script_gen.main import _apply_shot_replacements
    _apply_shot_replacements(
        {"hook": None, "fps": 24, "total_duration": 8, "shots": [_shot()]},
        [{"shot_id": "5", "keyframe_additions": [
            {"frame": {"t_sec": 2.0, "desc": "补入", "stage": "approach"},
             "layout": [_entry()]}]}],
        {"shot_5"},
        allowed_by_target={"shot_5": {"keyframes", "frame_layouts",
                                      "frame_layouts.kfs"}},
        expected_targets={"5"}, rejections_out=out2)
    assert out2 == [], "合法答卷不得残留上一次的拒收"


def test_applier_full_shot_without_identity_proof_is_classified():
    bad = copy.deepcopy(_shot())
    bad["keyframes"][1]["desc"] = "改写过的第二段"
    merged, n, rejected, out = _apply([{"shot_id": "5", "shot": bad}])
    assert n == 0, rejected
    assert any(r["code"] in ("frame_identity_unproven",
                             "protected_field", "pair_shape_invalid")
               for r in out), out
    assert out[0]["target"] == "shot_5"
