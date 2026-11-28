# -*- coding: utf-8 -*-
from __future__ import annotations

import json


def test_metrics_dump_contains_calls_repair_and_quality(tmp_path):
    from step01_script_gen import main
    main._timing_start()
    main._metrics_inc("calls.script_generation")
    main._metrics_inc("calls.repair_round")
    main._metrics_inc("repair.accepted")
    main._metrics_set("duration.budget_status", "BLOCK")
    out = tmp_path / "step01_metrics.json"
    main._metrics_dump(out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["calls"]["script_generation"] == 1
    assert data["calls"]["repair_round"] == 1
    assert data["repair"]["accepted"] == 1
    assert data["duration"]["budget_status"] == "BLOCK"

def test_timing_records_cumulative_and_phase_duration():
    from step01_script_gen import main
    main._timing_start()
    main._STEP_TIMING_T0 -= 5
    main._STEP_LAST_MARK_T -= 5
    main._timing_mark("phase_a")
    main._timing_mark("phase_b")
    a, b = main._STEP_TIMING[-2:]
    assert "phase_duration_sec" in a
    assert "elapsed_since_start_sec" in a
    assert "elapsed_sec" in a
    assert a["phase_duration_sec"] >= 0
    assert b["phase_duration_sec"] >= 0
    assert b["elapsed_since_start_sec"] >= a["elapsed_since_start_sec"]

def test_commit_block_projects_to_the_run_terminal(tmp_path):
    """复核 2026-10-04 C1：提交 BLOCK 必须落到运行终局（metrics/timing 不再报 ok）.

    实测形态（ch2，`run_id 20261004T012042-33676`）：`step01_commit.json` = BLOCK（5 条
    blocking、修复轮零改善），而 metrics/timing 记的是 `status=ok`、`outcome_code=""`。
    提交点是**唯一的正式产物出口**，它 BLOCK 就是这一轮失败。
    """
    from step01_script_gen import main

    # `_timing_start()` 会重置运行终局（它是 run 起点）——测试按**生产顺序**先起表再投影
    main._timing_start()
    main._project_commit_outcome({"status": "BLOCK", "detail": "5 条 blocking，修复轮零改善"})
    assert main._RUN_OUTCOME["status"] == "failed"
    assert main._RUN_OUTCOME["code"] == "commit_blocked"
    assert "5 条 blocking" in main._RUN_OUTCOME["detail"]
    main._timing_mark("commit", status="BLOCK")
    out = tmp_path / "step01_timing.json"
    main._timing_dump(out)
    _t = json.loads(out.read_text(encoding="utf-8"))
    assert _t["status"] == "failed" and _t["outcome_code"] == "commit_blocked"
    assert [p for p in _t["phases"] if p["phase"] == "_total"][0]["status"] == "failed"

    # 合法对照：COMMITTED ⇒ ok（审计自身的 `audit_status=ok` 不参与这一层）
    main._run_outcome_reset()
    main._project_commit_outcome({"status": "COMMITTED", "audit_status": "ok"})
    assert main._RUN_OUTCOME["status"] == "ok" and main._RUN_OUTCOME["code"] == ""

    # 失败优先：早先的失败不被后来的 COMMITTED 覆盖
    main._run_outcome_reset()
    main._run_finish("failed", "PlanInfeasibleError")
    main._project_commit_outcome({"status": "COMMITTED"})
    assert main._RUN_OUTCOME["status"] == "failed"
    assert main._RUN_OUTCOME["code"] == "PlanInfeasibleError"


def test_both_commit_returns_share_the_same_projection():
    """两处提交返回（缓存命中 / 落盘前）都走同一投影与同一 timing 标记.

    "一处改一处漏"正是这条缺陷的老形态：缓存路径此前只打一行 console，metrics/timing
    仍报 ok，两条出口的运行终局不一致。
    """
    from pathlib import Path
    from tests.verification_helpers import step01_source_text
    _text = step01_source_text()
    assert _text.count("_project_commit_outcome(_commit_summary)") == 2, "两处提交返回都要投影终局"
    assert _text.count('_timing_mark("commit"') == 2, "两处提交返回都要打 timing 标记"
