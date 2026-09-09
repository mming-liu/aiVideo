"""v6.62.6/v6.62.7（09-09）Constraint 引擎 warn 记录**可见性**回归测试.

背景：`action='warn'` 的 RepairRecord 此前只存在于内存 report 里，引擎只打聚合
计数与 HARD 未解决项——于是 v6.62.4「W6 拉伸倍数过大只告警、不触发重生成」的
用户决策成了死功能（09-08 实测正片拉伸 1.39x / 阈值 1.20，零告警输出）。
告警是「只观测不干预」类决策的**唯一交付物**，所以它的输出本身需要测试守住。

两条最容易再退化回静默的路径：
  1. `get_logger()` 返回 None（pytest / 非 main 调用）——直接 log.warn(...) 抛
     AttributeError，被 run_hard_fix 的宽 except 吞掉，告警又消失。
  2. 高频约束刷屏——分组前 frame_layouts_required 一类能占满输出，把稀有且
     高价值的告警挤掉。
"""
from __future__ import annotations

import pytest


def _script():
    return {
        "shots": [
            {"index": 1, "prompt": "近景，人物立于船头", "duration_sec": 4.0},
            {"index": 2, "prompt": "全景，湖面开阔", "duration_sec": 4.0},
        ]
    }


def _warn_records():
    """构造一批 warn 记录：1 条稀有高价值 + 5 条高频噪声（同一约束）."""
    from step01_script_gen.constraints import RepairRecord

    recs = [RepairRecord(
        constraint_name="runtime_extend",
        shot_index=None,
        action="warn",
        target="script",
        detail="拉伸倍数 1.39x 超阈 1.20",
    )]
    recs += [
        RepairRecord(
            constraint_name="frame_layouts_required",
            shot_index=i,
            action="warn",
            target="frame_layouts",
            detail=f"补齐 shot{i} 布局",
        )
        for i in range(1, 6)
    ]
    return recs


@pytest.fixture
def _patched_engine(monkeypatch):
    """让 apply_constraints 返回带 warn 记录的固定 report."""
    from step01_script_gen.constraints import engine as eng
    from step01_script_gen.constraints import RepairReport

    report = RepairReport()
    report.repairs.extend(_warn_records())
    monkeypatch.setattr(eng, "apply_constraints", lambda ctx: report)
    monkeypatch.setattr(eng, "_maybe_run_conflict_detect_once", lambda: None)
    return eng


def test_warns_visible_without_logger(_patched_engine, capsys, monkeypatch):
    """get_logger() 为 None 时**必须**兜底 print，而不是静默.

    这是本功能最脆的一环：AttributeError 会被 run_hard_fix 的宽 except 吞掉，
    症状与「功能没做」完全一致，不会有任何报错提示。
    """
    from shared import tee_logger

    monkeypatch.setattr(tee_logger, "get_logger", lambda *a, **k: None)
    _patched_engine.run_hard_fix(_script())

    out = capsys.readouterr().out
    assert "[CONSTRAINT-WARN]" in out, "无 logger 时告警不能消失"
    assert "runtime_extend" in out
    assert "1.39x" in out, "detail 必须打出来，否则只知道类别不知道数值"


def test_high_frequency_constraint_does_not_crowd_out_rare_warn(_patched_engine, capsys, monkeypatch):
    """按约束分组后，稀有告警与高频告警**各保底 1 条**，并给出总数.

    分组前是「取前 N 条记录」，同一轮 18 条里 14 条来自 frame_layouts_required，
    真正要看的 runtime_extend 正好排在被截断的位置。
    """
    from shared import tee_logger

    monkeypatch.setattr(tee_logger, "get_logger", lambda *a, **k: None)
    _patched_engine.run_hard_fix(_script())

    out = capsys.readouterr().out
    warn_lines = [ln for ln in out.splitlines() if "[CONSTRAINT-WARN]" in ln]
    assert any("runtime_extend" in ln for ln in warn_lines), "稀有告警不得被高频约束挤掉"
    hi = [ln for ln in warn_lines if "frame_layouts_required" in ln]
    assert len(hi) == 1, "高频约束只打 1 条样例"
    assert "另 4 处" in hi[0], "折叠掉的条数要报出来"
    assert "合计 6 条告警 / 2 类约束" in out


def test_logger_path_receives_warns(_patched_engine, monkeypatch):
    """有 logger 时走 log.warn（不是 print），保证落进 step01.log."""
    from shared import tee_logger

    calls: list[str] = []

    class _FakeLogger:
        def info(self, msg):
            calls.append(f"INFO {msg}")

        def warn(self, msg):
            calls.append(f"WARN {msg}")

    monkeypatch.setattr(tee_logger, "get_logger", lambda *a, **k: _FakeLogger())
    _patched_engine.run_hard_fix(_script())

    warn_lines = [c for c in calls if c.startswith("WARN") and "CONSTRAINT-WARN" in c]
    assert warn_lines, "有 logger 时告警必须走 log.warn 落盘"
    assert any("runtime_extend" in c for c in warn_lines)
