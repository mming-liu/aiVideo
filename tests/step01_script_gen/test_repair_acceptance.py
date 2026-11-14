# -*- coding: utf-8 -*-
from __future__ import annotations

from step01_script_gen.main import _repair_acceptance_decision
from step01_script_gen.quality_issues import Issue


def _rec(code, shot_id="1", severity="visual_risk", **kw):
    return {
        "code": code,
        "severity": severity,
        "shot_id": shot_id,
        "frame_id": kw.get("frame_id", ""),
        "entity_id": kw.get("entity_id", ""),
        "field_path": kw.get("field_path", ""),
    }


def test_acceptance_uses_structured_identity_and_accepts_real_reduction():
    before = {"issues": [_rec("multi_person_staging"), _rec("person_boat_position")],
              "checker_failed": False}
    after = {"issues": [_rec("multi_person_staging")], "checker_failed": False}
    decision = _repair_acceptance_decision(before, after, [], {"shots": []}, False)
    assert decision["accepted"] is True
    assert decision["resolved"] == 1
    assert decision["new_blocking"] == 0


def test_acceptance_rejects_new_blocking_even_when_one_old_issue_resolved():
    before = {"issues": [_rec("old_issue")], "checker_failed": False}
    after = {"issues": [_rec("new_issue")], "checker_failed": False}
    decision = _repair_acceptance_decision(before, after, [], {"shots": []}, False)
    assert decision["accepted"] is False
    assert decision["reason"] == "new_blocking"


def test_acceptance_rejects_checker_failure():
    before = {"issues": [_rec("old_issue")], "checker_failed": False}
    after = {"issues": [], "checker_failed": True}
    decision = _repair_acceptance_decision(before, after, [], {"shots": []}, False)
    assert decision["accepted"] is False
    assert decision["reason"] == "checker_failed"


def test_acceptance_can_verify_audit_only_issue_by_evidence_disappearing():
    script = {"shots": [{"index": 4, "prompt": "修复后的画面"}]}
    issue = Issue(
        code="model_audit_x",
        severity="visual_risk",
        message="bad",
        shot_id="4",
        field_path="prompt",
        evidence="修复前的错误动作",
    )
    decision = _repair_acceptance_decision(
        {"issues": [], "checker_failed": False},
        {"issues": [], "checker_failed": False},
        [issue],
        script,
        False,
    )
    assert decision["accepted"] is True
    assert decision["audit_field_touched"] == 1


def test_acceptance_rejects_unresolved_audit_only_issue():
    script = {"shots": [{"index": 4, "prompt": "仍然保留修复前的错误动作"}]}
    issue = Issue(
        code="model_audit_x",
        severity="visual_risk",
        message="bad",
        shot_id="4",
        field_path="prompt",
        evidence="修复前的错误动作",
    )
    decision = _repair_acceptance_decision(
        {"issues": [], "checker_failed": False},
        {"issues": [], "checker_failed": False},
        [issue],
        script,
        False,
    )
    assert decision["accepted"] is False
    assert decision["reason"] == "no_target_resolved"

def test_postprocess_target_lost_rejects_round():
    """G7（2026-11-14 裁决）：合并后目标已达成、后处理又改回 → 拒绝本轮."""
    issue = Issue(code="model_audit_x", severity="visual_risk", message="bad",
                  shot_id="1", field_path="prompt", evidence="修复前的错误动作")
    before_script = {"shots": [{"index": 1, "prompt": "修复前的错误动作"}]}
    merged = {"shots": [{"index": 1, "prompt": "已经改好了"}]}       # LLM 已修对
    after = {"shots": [{"index": 1, "prompt": "修复前的错误动作"}]}  # 后处理改回
    state = {"issues": [], "checker_failed": False}
    decision = _repair_acceptance_decision(
        state, state, [issue], after, False,
        script_before=before_script, merged_script=merged)
    assert decision["accepted"] is False
    assert decision["reason"] == "postprocess_target_lost"
    assert decision["postprocess_target_lost"]


def test_postprocess_target_kept_still_accepts():
    """G7：合并后与后处理后目标都达成 → 不误拒."""
    issue = Issue(code="model_audit_x", severity="visual_risk", message="bad",
                  shot_id="1", field_path="prompt", evidence="修复前的错误动作")
    before_script = {"shots": [{"index": 1, "prompt": "修复前的错误动作"}]}
    merged = {"shots": [{"index": 1, "prompt": "已经改好了"}]}
    after = {"shots": [{"index": 1, "prompt": "已经改好了，等价派生"}]}
    state = {"issues": [], "checker_failed": False}
    decision = _repair_acceptance_decision(
        state, state, [issue], after, False,
        script_before=before_script, merged_script=merged)
    assert decision["accepted"] is True
    assert decision["postprocess_target_lost"] == []


# ---------------- v6.87：锁定字段闸门与「非目标改动」分开命名

def test_locked_by_llm_and_non_target_are_separate_gates():
    """两者是不同的闸门，reason 必须能区分（v6.87）.

    v6.83 起两者被并在同一个列表里、统一报 `non_target_changed`，于是日志永远
    指不清是哪一道拦下的——2026-09-21 三次运行都是这个 reason，只能靠事后复现定位。
    """
    before = {"issues": [_rec("multi_person_staging")], "checker_failed": False}
    after = {"issues": [], "checker_failed": False}

    d = _repair_acceptance_decision(
        before, after, [], {"shots": []}, False,
        non_target_changed=["shot 7.prompt"], locked_by_llm_changed=[])
    assert d["reason"] == "non_target_changed" and not d["accepted"]

    d = _repair_acceptance_decision(
        before, after, [], {"shots": []}, False,
        non_target_changed=[], locked_by_llm_changed=["shot 4.subtitle: 'a' -> 'b'"])
    assert d["reason"] == "locked_field_changed", "LLM 改锁定字段必须单独命名"
    assert not d["accepted"]
    assert d["locked_by_llm_changed"] == ["shot 4.subtitle: 'a' -> 'b'"]


def test_shot_field_diff_pinpoints_fields_and_ignores_derived():
    """字段级诊断：说出**哪个字段**变了，且不把派生字段 `timecode` 算进去.

    2026-09-22 20:54 实测回退日志只有镜头号：`非目标镜头被改动 3 处: [12, 12.333, 18]`
    ——要定位是哪个字段被动过，只能重新复现一次运行（且未必复现）。
    """
    from step01_script_gen.main import _shot_field_diff

    b = {"shots": [{"index": 12, "prompt": "旧", "duration_sec": 5.0,
                    "timecode": {"start": 0.0}}], "hook": None}
    a = {"shots": [{"index": 12, "prompt": "新", "duration_sec": 5.0,
                    "timecode": {"start": 9.0}}], "hook": None}
    assert _shot_field_diff(b, a) == ["12.prompt"], "timecode 是派生字段，不该报"

    # 新增/删除的镜头也要报出来
    a2 = {"shots": [], "hook": None}
    assert any("新增/删除" in x for x in _shot_field_diff(b, a2))


def test_locked_signature_diff_reports_the_changed_field():
    """`locked_changed` 的**字段级**诊断（v6.94）.

    该闸门是第九次重跑后修复轮**唯一**的绑定闸门，而它只有布尔——从不说变了什么、
    也不看本轮授权了什么。先拿到字段名再决定修法，不猜根因。
    """
    from step01_script_gen.main import _locked_repair_signature, _locked_signature_diff

    def _mk(narr, dur):
        return {"shots": [{"index": 1, "narration_segment": narr, "subtitle": narr,
                           "duration_sec": dur, "scene": "船头", "location": "船头",
                           "variation_type": "中景"}], "hook": None}

    before = _locked_repair_signature(_mk("原始台词", 5.0))
    after = _locked_repair_signature(_mk("被改过的台词", 5.0))
    diff = _locked_signature_diff(before, after)
    assert any("narration_segment" in x for x in diff), diff
    assert any("subtitle" in x for x in diff), diff
    assert not any("duration_sec" in x for x in diff), "未变的字段不得报"

    assert _locked_signature_diff(before, before) == []


# ===========================================================================
# v6.102（第十六次修复项 #1）：audit 判据必须是「**本轮新**解决」
#
# 第十五次实测：第 2/3 轮 `before/after/resolved` **完全相同**（`resolved=0`），
# 却都通过验收——靠的是 `audit_resolved=2`，而这个数幂等：`_audit_issue_resolved`
# 问的是「evidence 现在还在不在稿子里」，第 1 轮清掉后**永远为真**。
# 于是同一批 4 镜被反复改写两次、白烧 2 次 LLM 调用，没有任何可测量的收益。
# ===========================================================================

def test_stale_audit_resolution_does_not_count_twice():
    from step01_script_gen.main import (
        _audit_issue_newly_resolved, _audit_issue_resolved,
    )
    from step01_script_gen.quality_issues import Issue

    issue = Issue(code="model_audit_x", severity="visual_risk", message="bad",
                  shot_id="1", field_path="prompt", evidence="修复前的错误动作")
    resolved = {"shots": [{"index": 1, "prompt": "已经改好了"}]}
    unresolved = {"shots": [{"index": 1, "prompt": "修复前的错误动作"}]}

    # 幂等口径：对同一份稿子问几次都是「已解决」
    assert _audit_issue_resolved(resolved, issue) is True
    assert _audit_issue_resolved(resolved, issue) is True
    # 新解决口径
    assert _audit_issue_newly_resolved(resolved, resolved, issue) is False, \
        "基线里已经解决 → 不算本轮功绩"
    assert _audit_issue_newly_resolved(unresolved, unresolved, issue) is False, \
        "模型没动 → 不算解决"
    assert _audit_issue_newly_resolved(resolved, unresolved, issue) is True, \
        "基线里还在、本轮消失 → 才算新解决"


def test_decision_without_new_resolution_is_rejected():
    """`resolved=0` 且「新解决」为 0 → 不接受（空转轮不得留下无收益的改写）."""
    from step01_script_gen.main import _repair_acceptance_decision

    before = {"issues": [_rec("multi_person_staging")], "checker_failed": False}
    decision = _repair_acceptance_decision(before, before, [], {"shots": []}, False)
    assert decision["accepted"] is False
    assert decision["reason"] == "no_target_resolved"


def test_already_resolved_audit_issue_does_not_carry_a_round():
    """端到端：基线里就已消失的 audit evidence **不得**再算本轮功绩（v6.102 #1）.

    修法前（幂等口径）：`audit_resolved=1` → 接受条件里 `audit_resolved > 0` 成立
    → 空转轮通过。修法后：`audit_resolved=0` → `no_target_resolved` → 拒绝。
    """
    from step01_script_gen.main import _repair_acceptance_decision
    from step01_script_gen.quality_issues import Issue

    issue = Issue(code="model_audit_x", severity="visual_risk", message="bad",
                  shot_id="1", field_path="prompt", evidence="修复前的错误动作")
    clean = {"shots": [{"index": 1, "prompt": "已经改好了"}]}     # evidence 早就不在了
    state = {"issues": [], "checker_failed": False}

    decision = _repair_acceptance_decision(state, state, [issue], clean, False,
                                           script_before=clean)
    assert decision["audit_field_touched"] == 0, "陈旧计数不得算成本轮功绩"
    assert decision["accepted"] is False
    assert decision["reason"] == "no_target_resolved"
