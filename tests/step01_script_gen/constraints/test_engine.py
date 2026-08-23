"""约束引擎基础测试（Phase 1 退出标准）.

测试内容：
  1. Severity / Violation / RepairRecord / RepairReport 数据结构正确
  2. ConstraintContext 自动从 script 推导 shots/hook
  3. apply_constraints 在空 ALL_CONSTRAINTS 上正常工作
  4. HARD_FIX_USE_OLD=1 全局回退
  5. CONSTRAINT_<NAME>_ENABLED=0 per-Constraint 关闭
  6. Constraint 基类幂等辅助（_already_repaired）
  7. iter_shots_and_hook 正确迭代 shots + hook
"""
from __future__ import annotations

import pytest


# ============================================================================
# 数据结构
# ============================================================================


def test_severity_enum_values():
    from step01_script_gen.constraints import Severity

    assert Severity.INFO.value == 1
    assert Severity.WARN.value == 2
    assert Severity.HARD.value == 3


def test_violation_is_immutable():
    """Violation 是 frozen dataclass，不应被修改."""
    from step01_script_gen.constraints import Severity, Violation

    v = Violation(constraint_name="test", severity=Severity.WARN, message="hello")
    with pytest.raises(Exception):  # FrozenInstanceError
        v.constraint_name = "changed"


def test_repair_record_basic():
    from step01_script_gen.constraints import RepairRecord

    r = RepairRecord(
        constraint_name="test",
        shot_index=1,
        action="append",
        target="prompt",
        before="old",
        after="new",
    )
    assert r.constraint_name == "test"
    assert r.shot_index == 1
    assert r.action == "append"
    assert r.before == "old"
    assert r.after == "new"


def test_repair_report_hard_unresolved():
    """RepairReport.hard_unresolved 应只返回 HARD severity 的违例."""
    from step01_script_gen.constraints import (
        RepairReport,
        Severity,
        Violation,
    )

    v1 = Violation("a", Severity.WARN)
    v2 = Violation("b", Severity.HARD)
    v3 = Violation("c", Severity.HARD)
    report = RepairReport(violations_remaining=[v1, v2, v3])
    assert len(report.hard_unresolved) == 2
    assert {v.constraint_name for v in report.hard_unresolved} == {"b", "c"}


def test_repair_report_repairs_by_constraint():
    from step01_script_gen.constraints import RepairRecord, RepairReport

    r1 = RepairRecord("a", 1, "append", "prompt")
    r2 = RepairRecord("a", 2, "append", "prompt")
    r3 = RepairRecord("b", 3, "rewrite", "ff_desc")
    report = RepairReport(repairs=[r1, r2, r3])
    assert report.repairs_by_constraint == {"a": 2, "b": 1}


# ============================================================================
# ConstraintContext
# ============================================================================


def test_constraint_context_from_script_autopopulates():
    """from_script 应自动从 script 推导 shots/hook 引用."""
    from step01_script_gen.constraints import ConstraintContext

    script = {
        "shots": [{"index": 1, "prompt": "x"}],
        "hook": {"index": 0, "prompt": "h"},
    }
    ctx = ConstraintContext.from_script(script)
    assert ctx.shots is script["shots"]  # live view，不是 copy
    assert ctx.hook is script["hook"]
    assert len(ctx.shots) == 1


def test_constraint_context_no_hook():
    """hook=None 时 ctx.hook 应为 None."""
    from step01_script_gen.constraints import ConstraintContext

    script = {"shots": [], "hook": None}
    ctx = ConstraintContext.from_script(script)
    assert ctx.hook is None


def test_constraint_context_empty_markers_default():
    """markers=None 时 from_script 应自动创建空 ElementMarkers."""
    from step01_script_gen.constraints import ConstraintContext

    ctx = ConstraintContext.from_script({"shots": []})
    assert ctx.markers is not None
    assert ctx.markers.injuries == {}
    assert ctx.markers.props == {}


def test_constraint_context_record_and_drain():
    """record_repair / record_violation / drain_* 应工作正常."""
    from step01_script_gen.constraints import (
        ConstraintContext,
        Severity,
        Violation,
    )

    ctx = ConstraintContext.from_script({"shots": []})
    ctx.record_repair("c1", shot_index=1, action="append", target="prompt", after="new")
    ctx.record_violation(Violation("c1", Severity.WARN, message="test"))

    repairs = ctx.drain_repairs()
    violations = ctx.drain_violations()
    assert len(repairs) == 1
    assert repairs[0].constraint_name == "c1"
    assert repairs[0].action == "append"
    assert len(violations) == 1
    assert violations[0].constraint_name == "c1"

    # 第二次 drain 应为空
    assert ctx.drain_repairs() == []
    assert ctx.drain_violations() == []


# ============================================================================
# apply_constraints 引擎
# ============================================================================


def test_apply_constraints_empty_registry():
    """空 ALL_CONSTRAINTS: 跑通，返回空 report."""
    from step01_script_gen.constraints import (
        ALL_CONSTRAINTS,
        ConstraintContext,
        apply_constraints,
    )

    original = list(ALL_CONSTRAINTS)
    ALL_CONSTRAINTS.clear()
    try:
        script = {"shots": []}
        ctx = ConstraintContext.from_script(script)
        report = apply_constraints(ctx)
        assert report.repairs == []
        assert report.violations_remaining == []
        assert report.duration_ms >= 0
    finally:
        ALL_CONSTRAINTS[:] = original


def test_apply_constraints_runs_registered(monkeypatch):
    """ALL_CONSTRAINTS 注册的 Constraint 应被调用."""
    from step01_script_gen.constraints import (
        ALL_CONSTRAINTS,
        Constraint,
        ConstraintContext,
        apply_constraints,
    )

    class MockConstraint(Constraint):
        name = "mock_runs"
        severity = None
        call_count = 0

        def apply(self, ctx):
            MockConstraint.call_count += 1
            return []

    original = list(ALL_CONSTRAINTS)
    ALL_CONSTRAINTS.append(MockConstraint())
    try:
        ctx = ConstraintContext.from_script({"shots": []})
        apply_constraints(ctx)
        assert MockConstraint.call_count == 1

        # 第二次调用也应该执行（不是幂等的——这是 mock 的特性）
        apply_constraints(ctx)
        assert MockConstraint.call_count == 2
    finally:
        ALL_CONSTRAINTS[:] = original


def test_apply_constraints_hard_fix_use_old_env(monkeypatch):
    """HARD_FIX_USE_OLD=1 应跳过所有 Constraint."""
    monkeypatch.setenv("HARD_FIX_USE_OLD", "1")
    from step01_script_gen.constraints import (
        ALL_CONSTRAINTS,
        Constraint,
        ConstraintContext,
        apply_constraints,
    )

    class MockConstraint(Constraint):
        name = "mock_skipped"
        severity = None
        call_count = 0

        def apply(self, ctx):
            MockConstraint.call_count += 1
            return []

    original = list(ALL_CONSTRAINTS)
    ALL_CONSTRAINTS.append(MockConstraint())
    try:
        ctx = ConstraintContext.from_script({"shots": []})
        report = apply_constraints(ctx)
        assert report.repairs == []
        assert MockConstraint.call_count == 0
    finally:
        ALL_CONSTRAINTS[:] = original


def test_apply_constraints_per_constraint_flag_disabled(monkeypatch):
    """CONSTRAINT_<NAME>_ENABLED=0 应关闭单个 Constraint."""
    monkeypatch.setenv("CONSTRAINT_MOCK_DISABLED_ENABLED", "0")
    from step01_script_gen.constraints import (
        ALL_CONSTRAINTS,
        Constraint,
        ConstraintContext,
        apply_constraints,
    )

    class MockConstraint(Constraint):
        name = "mock_disabled"
        severity = None
        call_count = 0

        def apply(self, ctx):
            MockConstraint.call_count += 1
            return []

    original = list(ALL_CONSTRAINTS)
    ALL_CONSTRAINTS.append(MockConstraint())
    try:
        ctx = ConstraintContext.from_script({"shots": []})
        apply_constraints(ctx)
        assert MockConstraint.call_count == 0
    finally:
        ALL_CONSTRAINTS[:] = original


def test_apply_constraints_per_constraint_flag_enabled_by_default():
    """默认 per-Constraint 应启用（无 env var）."""
    from step01_script_gen.constraints.base import Constraint

    class C(Constraint):
        name = "default_enabled"
        severity = None

        def apply(self, ctx):
            return []

    c = C()
    # 没设 env，应启用
    import os

    os.environ.pop("CONSTRAINT_DEFAULT_ENABLED_ENABLED", None)
    assert c.is_enabled() is True


def test_apply_constraints_collects_repairs_and_violations():
    """apply_constraints 应收集 Constraint 上报的 repairs + violations."""
    from step01_script_gen.constraints import (
        ALL_CONSTRAINTS,
        Constraint,
        ConstraintContext,
        Severity,
        Violation,
        apply_constraints,
    )

    class ProducingConstraint(Constraint):
        name = "producer"
        severity = Severity.WARN

        def apply(self, ctx):
            ctx.record_repair("producer", shot_index=1, action="append", target="prompt")
            ctx.record_violation(Violation("producer", Severity.WARN, message="x"))
            return [ctx.drain_repairs()[0]]  # 仍主动返回 repairs

    original = list(ALL_CONSTRAINTS)
    # 替换为仅 ProducingConstraint，确保不受已注册约束干扰
    ALL_CONSTRAINTS[:] = [ProducingConstraint()]
    try:
        ctx = ConstraintContext.from_script({"shots": [{"index": 1, "prompt": "x"}]})
        report = apply_constraints(ctx)
        assert len(report.repairs) == 1
        assert report.repairs[0].constraint_name == "producer"
        assert len(report.violations_remaining) == 1
    finally:
        ALL_CONSTRAINTS[:] = original


def test_apply_constraints_exception_is_caught():
    """Constraint 抛异常不应阻断其他 Constraint."""
    from step01_script_gen.constraints import (
        ALL_CONSTRAINTS,
        Constraint,
        ConstraintContext,
        apply_constraints,
    )

    class BrokenConstraint(Constraint):
        name = "broken"
        severity = None

        def apply(self, ctx):
            raise RuntimeError("intentional failure")

    class WorkingConstraint(Constraint):
        name = "working"
        severity = None
        call_count = 0

        def apply(self, ctx):
            WorkingConstraint.call_count += 1
            return []

    original = list(ALL_CONSTRAINTS)
    ALL_CONSTRAINTS.extend([BrokenConstraint(), WorkingConstraint()])
    try:
        ctx = ConstraintContext.from_script({"shots": []})
        report = apply_constraints(ctx)
        # WorkingConstraint 应仍被执行
        assert WorkingConstraint.call_count == 1
        # report 应为空（无 repairs）
        assert report.repairs == []
    finally:
        ALL_CONSTRAINTS[:] = original


# ============================================================================
# Constraint 基类辅助
# ============================================================================


def test_constraint_already_repaired_via_marker():
    """_already_repaired: repair_marker 在 fields 中应识别为已修."""
    from step01_script_gen.constraints.base import Constraint

    class TestConstraint(Constraint):
        name = "test"
        severity = None
        repair_marker = "ANCHOR_已修"

        def apply(self, ctx):
            return []

    c = TestConstraint()
    shot_unrepaired = {
        "prompt": "no marker",
        "ff_desc": "no marker",
        "lf_desc": "no marker",
    }
    shot_repaired = {
        "prompt": "with ANCHOR_已修 marker",
        "ff_desc": "",
        "lf_desc": "",
    }

    assert c._already_repaired(shot_unrepaired) is False
    assert c._already_repaired(shot_repaired) is True


def test_constraint_already_repaired_no_marker():
    """无 repair_marker 时 _already_repaired 应永远返回 False."""
    from step01_script_gen.constraints.base import Constraint

    class TestConstraint(Constraint):
        name = "test"
        severity = None
        repair_marker = None

        def apply(self, ctx):
            return []

    c = TestConstraint()
    shot_with_random_text = {"prompt": "anything"}
    assert c._already_repaired(shot_with_random_text) is False


def test_constraint_iter_shots_and_hook():
    """iter_shots_and_hook 应 yield shots + hook."""
    from step01_script_gen.constraints.base import Constraint

    class TestConstraint(Constraint):
        name = "test"
        severity = None

        def apply(self, ctx):
            return []

    ctx = type(
        "Ctx",
        (),
        {
            "shots": [{"index": 1, "prompt": "a"}, {"index": 2, "prompt": "b"}],
            "hook": {"prompt": "h"},
        },
    )()

    c = TestConstraint()
    items = list(c.iter_shots_and_hook(ctx))
    assert len(items) == 3
    assert items[0] == (ctx.shots[0], False, 1)
    assert items[1] == (ctx.shots[1], False, 2)
    assert items[2] == (ctx.hook, True, None)


def test_constraint_iter_shots_and_hook_skip_hook():
    """skip_hook=True 时 hook 不被 yield."""
    from step01_script_gen.constraints.base import Constraint

    class TestConstraint(Constraint):
        name = "test"
        severity = None
        skip_hook = True

        def apply(self, ctx):
            return []

    ctx = type(
        "Ctx",
        (),
        {
            "shots": [{"index": 1}],
            "hook": {"prompt": "h"},
        },
    )()

    c = TestConstraint()
    items = list(c.iter_shots_and_hook(ctx))
    assert len(items) == 1
    assert items[0] == (ctx.shots[0], False, 1)


def test_constraint_iter_no_hook():
    """ctx.hook=None 时不应 yield hook."""
    from step01_script_gen.constraints.base import Constraint

    class TestConstraint(Constraint):
        name = "test"
        severity = None

        def apply(self, ctx):
            return []

    ctx = type("Ctx", (), {"shots": [{"index": 1}], "hook": None})()

    c = TestConstraint()
    items = list(c.iter_shots_and_hook(ctx))
    assert len(items) == 1
    assert items[0] == (ctx.shots[0], False, 1)


def test_constraint_append_to_field():
    """_append_to_field 应正确追加文本（去重 + sep 拼接）."""
    from step01_script_gen.constraints.base import Constraint

    class TestConstraint(Constraint):
        name = "test"
        severity = None

        def apply(self, ctx):
            return []

    c = TestConstraint()
    shot = {"prompt": "原有内容"}

    # 首次追加
    assert c._append_to_field(shot, "prompt", "新内容") is True
    assert shot["prompt"] == "原有内容。新内容"

    # 重复追加应返回 False
    assert c._append_to_field(shot, "prompt", "新内容") is False
    assert shot["prompt"] == "原有内容。新内容"

    # 不同文本
    assert c._append_to_field(shot, "prompt", "再新") is True
    assert shot["prompt"] == "原有内容。新内容。再新"


def test_constraint_rewrite_field():
    """_rewrite_field 应正确替换字段."""
    from step01_script_gen.constraints.base import Constraint

    class TestConstraint(Constraint):
        name = "test"
        severity = None

        def apply(self, ctx):
            return []

    c = TestConstraint()
    shot = {"prompt": "old"}

    assert c._rewrite_field(shot, "prompt", "new") is True
    assert shot["prompt"] == "new"

    # 相同文本应返回 False
    assert c._rewrite_field(shot, "prompt", "new") is False


# ============================================================================
# ElementMarkers
# ============================================================================


def test_element_markers_injury_register_and_get():
    from step01_script_gen.constraints.element_markers import (
        ElementMarkers,
        InjuryMarker,
    )

    em = ElementMarkers()
    marker = InjuryMarker(
        victim="杨旭",
        anchor_phrases=("心口暗红洇痕", "白袍血痕"),
        continuity_phrase="伤处白袍暗红洇痕持续可见",
        location="小舟",
    )
    em.register_injury(marker)
    assert em.get_injury("杨旭") is marker
    # 重复注册不覆盖
    em.register_injury(InjuryMarker(
        victim="杨旭", anchor_phrases=("其他",), continuity_phrase="其他"
    ))
    assert em.get_injury("杨旭") is marker


def test_element_markers_prop_register_and_get():
    from step01_script_gen.constraints.element_markers import (
        ElementMarkers,
        PropMarker,
    )

    em = ElementMarkers()
    marker = PropMarker(prop="炭炉", canonical_position="画中甲板", scene_hints=("甲板",))
    em.register_prop(marker)
    assert em.get_prop("炭炉") is marker


def test_element_markers_to_from_dict_roundtrip():
    from step01_script_gen.constraints.element_markers import ElementMarkers

    em = ElementMarkers()
    em.register_injury(_mk_injury("杨旭"))
    em.register_prop(_mk_prop("炭炉"))

    data = em.to_dict()
    em2 = ElementMarkers.from_dict(data)
    assert em2.get_injury("杨旭").continuity_phrase == em.get_injury("杨旭").continuity_phrase
    assert em2.get_prop("炭炉").canonical_position == "画中甲板"


def test_element_markers_from_empty_dict():
    """from_dict({}) 应返回空 ElementMarkers，不报错."""
    from step01_script_gen.constraints.element_markers import ElementMarkers

    em = ElementMarkers.from_dict({})
    assert em.injuries == {}
    assert em.props == {}


# 内部 helper
def _mk_injury(victim: str):
    from step01_script_gen.constraints.element_markers import InjuryMarker

    return InjuryMarker(
        victim=victim,
        anchor_phrases=("锚",),
        continuity_phrase="持续可见",
    )


def _mk_prop(prop: str):
    from step01_script_gen.constraints.element_markers import PropMarker

    return PropMarker(prop=prop, canonical_position="画中甲板", scene_hints=("甲板",))
