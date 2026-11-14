# -*- coding: utf-8 -*-
"""P1 收口：G1 关闭依据 / G7 目标丢失（含确定性目标）的离线单测.

零模型、零网络。G7 走纯函数 `_repair_acceptance_decision`；G1 的提交门接线在
`main.py` 提交循环内（深层闭包），因此用**真实提交入口** `_commit_step01_outputs`
+ 审计/修复桩复现复核给出的两条链：

* 缺证据问题「改字段 → 修复轮接受 → 下轮审计沉默」**不得**被 G1 豁免；
* 同一 (镜头, family) 的确定性检查「上一轮报出、本轮干净」才是合法关闭依据。
"""
from __future__ import annotations

import copy
import json

from step01_script_gen.main import _repair_acceptance_decision
from tests.step01_script_gen.test_step01_commit import (  # noqa: F401  夹具
    _audit_payload, _fake_audit, _script, chapter)


def _rec(code: str, shot: str = "1", field: str = "prompt",
         sev: str = "hard") -> dict:
    return {
        "code": code, "shot_id": shot, "frame_id": "", "entity_id": "",
        "field_path": field, "severity": sev, "message": "m",
    }


def _g1_issue(code: str = "model_audit_STATE_CHAIN_CONFLICT") -> dict:
    """缺证据（evidence 为空）的阻塞审计条目：只能走字段触达兜底."""
    return {
        "code": code, "shot_id": "1", "frame_id": "", "entity_id": "",
        "field_path": "prompt", "severity": "visual_risk", "message": "状态链断裂",
        "evidence": "",
        "repair_targets": [{"shot_id": "1", "field_path": "prompt"}],
    }


# ---------------------------------------------------------------------------
# G7
# ---------------------------------------------------------------------------
def test_g7_deterministic_target_lost_rejects():
    """合并后已达成的确定性目标，在后处理后**又出现** → 拒收 target-loss.

    before={A,B} / after={A}（B 已 resolved）/ merged={} （A 合并后达成）
    ⇒ (before-merged)∩after = {A} ⇒ 确定性 target-loss。
    """
    dec = _repair_acceptance_decision(
        {"issues": [_rec("A"), _rec("B")]},
        {"issues": [_rec("A")]},
        [], {"shots": []}, False,
        merged_script={"shots": []},
        merged_state={"issues": []})
    assert dec["accepted"] is False
    assert dec["reason"] == "postprocess_target_lost"
    assert dec["postprocess_target_lost"], "确定性 target-loss 必须被报出"


def test_g7_no_target_loss_accepts():
    """合并后达成、后处理后仍未出现 → 不是 target-loss，正常接受."""
    dec = _repair_acceptance_decision(
        {"issues": [_rec("A")]},
        {"issues": []},
        [], {"shots": []}, False,
        merged_script={"shots": []},
        merged_state={"issues": []})
    assert dec["accepted"] is True
    assert not dec["postprocess_target_lost"]


def test_g7_without_merged_state_keeps_old_behavior():
    """不传 merged_state ⇒ 跳过确定性侧 target-loss（向后兼容，行为不变）."""
    dec = _repair_acceptance_decision(
        {"issues": [_rec("A")]},
        {"issues": []},
        [], {"shots": []}, False,
        merged_script={"shots": []})
    assert dec["accepted"] is True
    assert not dec["postprocess_target_lost"]


def test_g7_incomplete_merged_check_is_its_own_rejection():
    """复核 P2：合并后检查未完成（checker_failed）必须独立拒收，不得跳过.

    旧实现里 `_g7_merged_state` 异常返回 None，target-loss 被静默跳过，
    一轮可能被错误接受。
    """
    dec = _repair_acceptance_decision(
        {"issues": [_rec("A")]},
        {"issues": []},
        [], {"shots": []}, False,
        merged_script={"shots": []},
        merged_state={"issues": [], "checker_failed": True})
    assert dec["accepted"] is False
    assert dec["reason"] == "postprocess_check_failed"
    assert dec["merged_checker_failed"] is True


def test_g7_collector_failure_returns_checker_failed(monkeypatch):
    """收集器抛异常 → 返回 checker_failed 状态（不是 None）."""
    import step01_script_gen.main as m

    def _boom(*args, **kwargs):
        raise RuntimeError("collector down")

    monkeypatch.setattr(m, "_collect_repair_warnings", _boom)
    state = m._g7_merged_state({"shots": []}, "")
    assert state is not None and state["checker_failed"] is True
    dec = m._repair_acceptance_decision(
        {"issues": []}, {"issues": []}, [], {"shots": []}, False,
        merged_script={"shots": []}, merged_state=state)
    assert dec["accepted"] is False
    assert dec["reason"] == "postprocess_check_failed"


# ---------------------------------------------------------------------------
# G1（纯函数口径：尝试记录不是关闭依据）
# ---------------------------------------------------------------------------
def _issue_obj(code: str = "model_audit_STATE_CHAIN_CONFLICT", entity_id: str = ""):
    from step01_script_gen.quality_issues import Issue
    return Issue(code=code, severity="visual_risk", message="m", shot_id="1",
                 frame_id="", entity_id=entity_id, field_path="prompt", evidence="",
                 repair_targets=[{"shot_id": "1", "field_path": "prompt"}],
                 repairable=True)


def test_g1_attempt_records_are_not_a_closure_basis():
    """复核 P1：G1 的判据里**不得**存在「已尝试 / 字段触达」输入."""
    import inspect

    import step01_script_gen.main as m

    issue = _issue_obj()
    script = {"shots": [{"index": 1, "prompt": "原样"}]}
    unclosed = m._g1_unclosed_issues(
        [issue], set(), script,
        prev_det_families=set(), now_det_families=set(), det_checked=True)
    assert unclosed == [issue], "没有确定性关闭依据就必须保留未关闭"
    assert "audit_attempted" not in inspect.signature(
        m._g1_unclosed_issues).parameters, "尝试记录不得成为关闭依据"


def test_g1_deterministic_family_transition_is_the_closure():
    """同一 (镜头, family) 的确定性检查「报出 → 干净」才算关闭；检查未跑完不算."""
    import step01_script_gen.main as m

    issue = _issue_obj()
    script = {"shots": [{"index": 1, "prompt": "原样"}]}
    ident = m._audit_issue_identity(issue)

    assert m._g1_unclosed_issues(
        [issue], set(), script,
        prev_det_families={ident}, now_det_families=set(),
        det_checked=True) == [], "确定性检查转干净 ⇒ 有依据关闭"
    assert m._g1_unclosed_issues(
        [issue], set(), script,
        prev_det_families={ident}, now_det_families=set(),
        det_checked=False) == [issue], "检查未跑完不得当关闭"
    assert m._g1_unclosed_issues(
        [issue], set(), script,
        prev_det_families={ident}, now_det_families={ident},
        det_checked=True) == [issue], "确定性检查仍报出 ⇒ 未关闭"
    assert m._g1_unclosed_issues(
        [issue], {ident}, script,
        prev_det_families=set(), now_det_families=set(),
        det_checked=True) == [], "本轮审计仍报出 ⇒ 不归 G1 管"


def test_g1_named_entity_removal_is_a_structural_closure():
    """问题点名的实体「修复前在布局里、修复后消失」= 确定性内容事实，可关闭.

    配对问题（证据必须留在上游字段）不能靠证据消失关闭，但"被点名的多余实体
    已经从布局里移除"是代码可核对的内容事实，不是"模型动过手"的尝试记录。
    """
    import step01_script_gen.main as m

    issue = _issue_obj(entity_id="小厮")
    before = {"shots": [{"index": 1,
                         "frame_layouts": {"kfs": [[{"name": "小厮"}]]}}]}
    after = {"shots": [{"index": 1, "frame_layouts": {"kfs": [[]]}}]}

    assert m._g1_unclosed_issues(
        [issue], set(), after,
        prev_det_families=set(), now_det_families=set(), det_checked=True,
        prev_script=before) == [], "实体由存在转为消失 ⇒ 结构化关闭依据成立"
    assert m._g1_unclosed_issues(
        [issue], set(), after,
        prev_det_families=set(), now_det_families=set(), det_checked=True,
        prev_script=after) == [issue], "修复前就不存在 ⇒ 不得反判成已解决"


# ---------------------------------------------------------------------------
# G1（真实提交入口）
# ---------------------------------------------------------------------------
def test_g1_field_touch_then_silence_is_not_a_closure(chapter, monkeypatch):
    """复核 P1：缺证据问题「改字段 → 修复轮接受 → 下轮审计沉默」不得豁免.

    第 1 轮：审计报出无 evidence 的 blocking，修复只改点名字段（走触达兜底）
    ⇒ 该轮被接受、身份进入「已尝试」集合。
    第 2 轮：审计沉默。旧 G1 直接拿「已尝试」当关闭依据 → 放行；新 G1 要求
    独立的确定性关闭依据 → BLOCK。
    """
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    calls = _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, issues=[] if n > 1 else [_g1_issue()],
        scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))

    def repair(script, novel_text, **kw):
        out = copy.deepcopy(script)
        out["shots"][0]["prompt"] = "[中景] 测试镜头1，画面各不相同（修）"
        # 模拟真实修复轮落定后的兜底记账（`_repair_shot_warnings` 在轮次被接受后
        # 把字段触达身份并入 `audit_attempted`）。复核 P1 的完整链条是：
        # 改字段 → 修复轮接受 → 身份入「已尝试」→ 下轮审计沉默。G1 不得据此放行。
        attempted = kw.get("audit_attempted")
        if attempted is not None:
            attempted.add(("1", "state_contradiction"))
        return out

    monkeypatch.setattr(m, "_repair_shot_warnings", repair)
    _, summary = m._commit_step01_outputs(_script(), "")

    assert calls["n"] == 2, "第一轮审计 + 采纳修复后的重审"
    assert summary["status"] == "BLOCK"
    assert "G1" in str(summary.get("detail") or ""), summary
    manifest_path = chapter / "step01_commit.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "blocking_unclosed_without_evidence" in {
        b.get("code") for b in manifest.get("blocking") or []}


def test_g1_deterministic_check_going_clean_is_a_valid_closure(chapter, monkeypatch):
    """同一 (镜头, family) 的确定性检查由报出转为干净 ⇒ 独立关闭依据成立.

    审计条目本身缺证据（`_audit_issue_resolved` 不可判），但第 1 轮确定性质量门
    报了同族问题、第 2 轮干净——这是独立于「模型动过手」的复核，G1 应放行。
    """
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import (Issue, RULE_SOURCE_FINGERPRINT,
                                                  RULE_VERSION)

    def _collect(script, *args, **kwargs):
        if "已修复" in str((script.get("shots") or [{}])[0].get("prompt") or ""):
            return [], False, RULE_VERSION
        return ([Issue(code="state_chain_static", severity="visual_risk",
                       message="状态链断裂", shot_id="1", frame_id="ff",
                       field_path="ff_desc", repairable=True)], False, RULE_VERSION)

    monkeypatch.setattr(m, "collect_structured_issues", _collect)
    calls = _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, issues=[] if n > 1 else [_g1_issue()],
        scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))

    def repair(script, novel_text, **kw):
        out = copy.deepcopy(script)
        out["shots"][0]["prompt"] = "已修复 测试镜头1"
        return out

    monkeypatch.setattr(m, "_repair_shot_warnings", repair)
    _, summary = m._commit_step01_outputs(_script(), "")

    assert calls["n"] == 2
    assert summary["status"] == "COMMITTED", summary
