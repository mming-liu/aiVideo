# -*- coding: utf-8 -*-
"""`temporal_plan` 迁移期统计（二次审核发现 6）.

决定 6：字段**继续保持只读**，不为了覆盖率强制启用结构写入；先在候选报告里统计
`present / valid / grounded`，积累真实样本，达到稳定阈值后再决定是否设为 schema
required 或让 shot-plan 消费它。本用例锁住「统计正确」与「统计不影响任何判定」。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _shot(index, plan=None, dur=4.0, motion="0-2秒：举刀；2-4秒：收刀"):
    s = {"index": index, "duration_sec": dur, "duration": dur,
         "motion_desc": motion, "narration_segment": "", "subtitle": "",
         "prompt": "p", "ff_desc": "f", "lf_desc": "l",
         "keyframes": [], "frame_layouts": {"ff": [], "lf": [], "kfs": []}}
    if plan is not None:
        s["temporal_plan"] = plan
    return s


# 引文取自本镜文本（motion_desc）→ grounded；description 与该段正文有共同内容 → valid
_OK_PLAN = {"events": [
    {"start_sec": 0.0, "end_sec": 2.0, "description": "举刀", "source_quote": "举刀"},
    {"start_sec": 2.0, "end_sec": 4.0, "description": "收刀", "source_quote": "收刀"},
]}
# 引文非空但**不在本镜文本内** → valid 但不算 grounded
_NO_QUOTE_PLAN = {"events": [
    {"start_sec": 0.0, "end_sec": 2.0, "description": "举刀", "source_quote": "他仰天长啸"},
    {"start_sec": 2.0, "end_sec": 4.0, "description": "收刀", "source_quote": "水面平静如镜"},
]}
_FAKE_PLAN = {"events": [
    {"start_sec": 0.0, "end_sec": 2.0, "description": "凭空倒地", "source_quote": "q"},
    {"start_sec": 2.0, "end_sec": 4.0, "description": "突然起火", "source_quote": "q"},
]}


def test_stats_count_present_valid_grounded():
    from step01_script_gen.main import _temporal_plan_stats

    script = {"hook": None, "shots": [
        _shot(1),                 # 无 plan
        _shot(2, _OK_PLAN),       # present + valid + grounded
        _shot(3, _NO_QUOTE_PLAN),  # present + valid，但有 quote 为空 → 不算 grounded
        _shot(4, _FAKE_PLAN),     # present 但 validate 不通过（与 motion 无共同内容）
    ]}
    st = _temporal_plan_stats(script)
    assert st == {"shots": 4, "present": 3, "valid": 2, "grounded": 1}


def test_hook_is_counted_too():
    from step01_script_gen.main import _temporal_plan_stats

    script = {"hook": _shot(0, _OK_PLAN), "shots": [_shot(1, _OK_PLAN)]}
    st = _temporal_plan_stats(script)
    assert st["shots"] == 2 and st["present"] == 2


def test_empty_plan_is_not_present():
    from step01_script_gen.main import _temporal_plan_stats

    script = {"hook": None, "shots": [_shot(1, {"events": []})]}
    assert _temporal_plan_stats(script)["present"] == 0


def test_stats_do_not_affect_any_verdict():
    """统计是**只读**的：加不加 plan，`structural_ok` / `integrity_ok` 都不得改变.

    决定 6 明确「不要为了覆盖率强制启用结构写入」——统计值低不能改变流水线行为。
    """
    from step01_script_gen.main import _candidate_quality_summary

    base = {"shots": [_shot(1), _shot(2)], "hook": None, "fps": 24,
            "total_duration": 8.0}
    with_plan = {"shots": [_shot(1, _OK_PLAN), _shot(2)], "hook": None, "fps": 24,
                 "total_duration": 8.0}
    a = _candidate_quality_summary(base, "")
    b = _candidate_quality_summary(with_plan, "")
    for key in ("structural_ok", "integrity_ok", "blocking_risk_count",
                "quality_checked"):
        assert a.get(key) == b.get(key), key
    assert a["temporal_plan_stats"]["present"] == 0
    assert b["temporal_plan_stats"]["present"] == 1
