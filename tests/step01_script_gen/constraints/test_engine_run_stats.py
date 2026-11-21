# -*- coding: utf-8 -*-
"""精简包 S06：约束引擎逐约束计数 → step01_metrics.json["constraints"]（离线桩约束）."""
from __future__ import annotations

import json
import threading

import pytest

from step01_script_gen.constraints import engine
from step01_script_gen.constraints.base import Constraint
from step01_script_gen.constraints.context import ConstraintContext
from step01_script_gen.constraints.types import RepairRecord, Severity, Violation


class _Fixer(Constraint):
    name = "stub_fixer"

    def apply(self, ctx):
        return [RepairRecord("stub_fixer", 1, "rewrite", "prompt"),
                RepairRecord("stub_fixer", 2, "append", "ff_desc"),
                RepairRecord("stub_fixer", 2, "warn", "lf_desc")]


class _Detector(Constraint):
    name = "stub_detector"

    def apply(self, ctx):
        ctx.record_violation(Violation("stub_detector", Severity.HARD, shot_index=1))
        return []


class _Quiet(Constraint):
    name = "stub_quiet"

    def apply(self, ctx):
        return []


class _Broken(Constraint):
    name = "stub_broken"

    def apply(self, ctx):
        raise ValueError("logic bug")


class _Disabled(Constraint):
    name = "stub_disabled"

    def is_enabled(self):
        return False

    def apply(self, ctx):  # pragma: no cover - never called
        raise AssertionError("disabled constraint must not run")


class _EnvFault(Constraint):
    name = "stub_env_fault"

    def apply(self, ctx):
        raise ImportError("missing dependency")


def _ctx():
    return ConstraintContext(script={"shots": [{"index": 1}, {"index": 2}]})


@pytest.fixture()
def registry(monkeypatch):
    engine.drain_constraint_stats()          # 不继承其他用例在本进程留下的计数
    monkeypatch.setattr(engine, "ALL_CONSTRAINTS",
                        [_Fixer(), _Detector(), _Quiet(), _Broken(), _Disabled()])
    yield
    engine.drain_constraint_stats()


def test_counts_per_constraint_and_drain_clears(registry):
    engine.apply_constraints(_ctx())
    stats = engine.drain_constraint_stats()
    zero = dict.fromkeys(engine.CONSTRAINT_STAT_KEYS, 0)
    assert stats["stub_fixer"] == {**zero, "ran": 1, "repair": 2, "warn": 1}
    assert stats["stub_detector"] == {**zero, "ran": 1, "violation": 1}
    # 跑了但没命中：ran=1、其余为 0 ——与「根本没跑」（不出现）可区分
    assert stats["stub_quiet"] == {**zero, "ran": 1}
    assert stats["stub_broken"] == {**zero, "ran": 1, "error": 1}
    assert "stub_disabled" not in stats
    assert engine.drain_constraint_stats() == {}


def test_only_filter_counts_only_executed(registry):
    engine.apply_constraints(_ctx(), only=(_Quiet,))
    assert set(engine.drain_constraint_stats()) == {"stub_quiet"}


def test_parallel_candidates_do_not_lose_counts(registry):
    threads = [threading.Thread(target=lambda: [engine.apply_constraints(_ctx())
                                                for _ in range(25)])
               for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stats = engine.drain_constraint_stats()
    assert stats["stub_fixer"]["ran"] == 100
    assert stats["stub_fixer"]["repair"] == 200
    assert stats["stub_detector"]["violation"] == 100


def test_env_fault_is_counted_and_still_raised(monkeypatch):
    engine.drain_constraint_stats()
    monkeypatch.setattr(engine, "ALL_CONSTRAINTS", [_EnvFault()])
    with pytest.raises(ImportError):
        engine.apply_constraints(_ctx())
    assert engine.drain_constraint_stats()["stub_env_fault"]["error"] == 1


def test_metrics_dump_writes_constraints_node(registry, tmp_path):
    from step01_script_gen import main
    main._timing_start()
    engine.apply_constraints(_ctx())
    out = tmp_path / "step01_metrics.json"
    main._metrics_dump(out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["constraints"]["stub_fixer"]["repair"] == 2
    assert data["constraints"]["stub_quiet"]["ran"] == 1
    assert "constraints_error" not in data
    # 落盘即取走：下一次落盘不重复计数
    main._metrics_dump(out)
    assert json.loads(out.read_text(encoding="utf-8"))["constraints"] == {}


def test_metrics_dump_records_stats_failure_explicitly(registry, tmp_path, monkeypatch):
    from step01_script_gen import main
    from step01_script_gen.orchestrator import runtime

    def _boom():
        raise RuntimeError("stats unavailable")

    monkeypatch.setattr(runtime, "_constraint_stats", _boom)
    main._timing_start()
    out = tmp_path / "step01_metrics.json"
    main._metrics_dump(out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "constraints" not in data
    assert data["constraints_error"] == "RuntimeError: stats unavailable"
    assert "run_id" in data                  # 其余指标照常落盘
