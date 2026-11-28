# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from step01_script_gen.constraints.context import ConstraintContext
from step01_script_gen.constraints.duration_budget import DurationBudgetConstraint
from step01_script_gen.quality_issues import _duration_budget_issues



def test_duration_budget_reports_without_compressing():
    """总时长超 90s → **只报告**，不再按比例压缩各镜时长（v6.79，审核发现 1）.

    压缩是链后写入：它发生在 KF 时间检查之后，会让「审计通过的时长」≠
    「最终时长」，且压缩后没有重新校验 KF 归属。正确处置是输出需要重写/拆分的
    镜头集合，交完整镜头事务处理。
    """
    script = {"shots": [
        {"index": 1, "duration_sec": 60.0, "narration_segment": "", "motion_desc": ""},
        {"index": 2, "duration_sec": 50.0, "narration_segment": "", "motion_desc": ""},
    ]}
    before = [sh["duration_sec"] for sh in script["shots"]]
    repairs = DurationBudgetConstraint().apply(ConstraintContext.from_script(script))
    assert repairs
    assert repairs[0].action == "detect", "v6.79 起只报告"
    assert [sh["duration_sec"] for sh in script["shots"]] == before, "时长不得被压缩"


def test_duration_budget_blocks_when_minimum_total_is_still_too_long():
    script = {"shots": [
        {"index": i, "duration_sec": 9.0,
         "narration_segment": "长台词", "motion_desc": "0-8秒：持续动作"}
        for i in range(1, 13)
    ]}
    ctx = ConstraintContext.from_script(script)
    repairs = DurationBudgetConstraint().apply(ctx)
    assert repairs == []
    assert any(v.constraint_name == "duration_budget" and not v.repaired
               for v in ctx.drain_violations())


def test_duration_budget_issue_is_blocking():
    issues = _duration_budget_issues({"shots": [{"duration_sec": 100}]})
    assert issues and issues[0].code == "duration_budget"
    assert issues[0].severity == "visual_risk"

def test_duration_budget_never_clamps_real_minimum_to_shot_cap():
    """单镜真实最低需求超过**生效上限** → HARD，且绝不压到最小需求以下.

    v6.97：动作窗口按**生效 cap**（`min(模型, 项目)`）构造，不再写死 12s——
    项目上限 8 → 15 之后，12s 的动作需求已**合法**，写死会让这条测试走不到它要测的分支。
    """
    from step01_script_gen.timecode import active_shot_cap

    over = active_shot_cap() + 2.0
    script = {"shots": [
        {"index": i, "duration_sec": 12.0,
         "narration_segment": "", "motion_desc": f"0-{over:g}秒：连续动作"}
        for i in range(1, 9)
    ]}
    ctx = ConstraintContext.from_script(script)
    repairs = DurationBudgetConstraint().apply(ctx)
    assert repairs == []
    assert all(float(s["duration_sec"]) == 12.0 for s in script["shots"])
    violations = [v for v in ctx.drain_violations()
                  if v.constraint_name == "duration_budget"]
    assert violations and not violations[0].repaired
    assert "单镜最低需求超过" in violations[0].message

def test_duration_budget_uses_model_cap_for_single_shot_limit(monkeypatch):
    import shared.model_caps as caps
    monkeypatch.setattr(caps, "active_video_duration_cap", lambda: 6)
    script = {"shots": [{
        "index": 1,
        "duration_sec": 8.0,
        "motion_desc": "0-7秒：连续动作",
        "narration_segment": "",
    }]}
    ctx = ConstraintContext.from_script(script)
    repairs = DurationBudgetConstraint().apply(ctx)
    # duration_budget is the total-project budget, not the model single-shot
    # capability guard.  It must leave this 8s shot alone.
    assert repairs == []
    assert [v for v in ctx.drain_violations()
            if v.constraint_name == "duration_budget"] == []