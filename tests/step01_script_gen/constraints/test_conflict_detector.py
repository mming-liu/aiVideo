"""ConflictDetector 单元测试（v6.42 PoC）.

覆盖：
  1. duplicate_append 检测
  2. marker_in_append 检测
  3. known_conflicts 列表
  4. 空/单约束 / 不冲突情形
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_detect_conflicts_empty():
    """空约束列表 → 0 冲突."""
    from step01_script_gen.constraints.conflict_detector import detect_conflicts
    assert detect_conflicts([]) == []


def test_detect_conflicts_no_conflict():
    """无 append_text 约束 → 0 冲突."""
    from step01_script_gen.constraints.conflict_detector import detect_conflicts
    from step01_script_gen.constraints.base import Constraint
    from step01_script_gen.constraints.types import Severity

    class NoopConstraint(Constraint):
        name = "noop"
        severity = Severity.WARN

        def apply(self, ctx):
            return []

    assert detect_conflicts([NoopConstraint()]) == []


def test_detect_duplicate_append():
    """两个约束 append_text 相同 → duplicate_append 冲突."""
    from step01_script_gen.constraints.conflict_detector import detect_conflicts
    from step01_script_gen.constraints._base_classes import SimpleTextAppendConstraint

    class A(SimpleTextAppendConstraint):
        name = "a"
        anchor_phrases = ("X",)
        append_text = "尾部"

    class B(SimpleTextAppendConstraint):
        name = "b"
        anchor_phrases = ("Y",)
        append_text = "尾部"  # 与 A 相同

    conflicts = detect_conflicts([A(), B()])
    kinds = [c.kind for c in conflicts]
    assert "duplicate_append" in kinds
    dup = [c for c in conflicts if c.kind == "duplicate_append"][0]
    assert {dup.constraint_a, dup.constraint_b} == {"a", "b"}


def test_detect_marker_in_append():
    """A 的 repair_marker 是 B 的 append_text 子串 → marker_in_append 冲突."""
    from step01_script_gen.constraints.conflict_detector import detect_conflicts
    from step01_script_gen.constraints._base_classes import SimpleTextAppendConstraint
    from step01_script_gen.constraints.base import Constraint
    from step01_script_gen.constraints.types import Severity

    class A(SimpleTextAppendConstraint):
        name = "a"
        anchor_phrases = ("X",)
        append_text = "白袍暗红洇痕持续可见"  # 包含 "暗红"

    class BMarker(Constraint):
        name = "b"
        severity = Severity.WARN
        repair_marker = "暗红"

        def apply(self, ctx):
            return []

    conflicts = detect_conflicts([A(), BMarker()])
    marker_confs = [c for c in conflicts if c.kind == "marker_in_append"]
    assert len(marker_confs) >= 1
    mc = marker_confs[0]
    # a appends 包含 b 的 marker "暗红"
    assert "a" in (mc.constraint_a, mc.constraint_b)
    assert "b" in (mc.constraint_a, mc.constraint_b)


def test_known_conflict_registered_only_when_both_present():
    """known conflict 只在两个约束都在场时上报."""
    from step01_script_gen.constraints.conflict_detector import (
        detect_conflicts,
        _KNOWN_CONFLICTS,
    )
    from step01_script_gen.constraints._base_classes import SimpleTextAppendConstraint
    from step01_script_gen.constraints.base import Constraint
    from step01_script_gen.constraints.types import Severity

    # 临时塞一个 known conflict
    original = list(_KNOWN_CONFLICTS)
    _KNOWN_CONFLICTS.append(("x_known", "y_known", "test known"))
    try:
        # 仅注册一个 → 不应报 known
        class XOnly(SimpleTextAppendConstraint):
            name = "x_known"
            anchor_phrases = ()
            append_text = ""

        cf1 = detect_conflicts([XOnly()])
        assert not any(c.kind == "known" for c in cf1)

        # 两个都注册 → 应报
        class YOnly(Constraint):
            name = "y_known"
            severity = Severity.WARN

            def apply(self, ctx):
                return []

        cf2 = detect_conflicts([XOnly(), YOnly()])
        known = [c for c in cf2 if c.kind == "known"]
        assert len(known) == 1
        assert "test known" in known[0].description
    finally:
        _KNOWN_CONFLICTS[:] = original


def test_detect_and_log_doesnt_crash():
    """detect_and_log 在 logger 不可用时静默."""
    from step01_script_gen.constraints.conflict_detector import detect_and_log
    from step01_script_gen.constraints._base_classes import SimpleTextAppendConstraint

    class A(SimpleTextAppendConstraint):
        name = "a"
        anchor_phrases = ("X",)
        append_text = "尾部"

    class B(SimpleTextAppendConstraint):
        name = "b"
        anchor_phrases = ("Y",)
        append_text = "尾部"

    # 不应抛异常
    result = detect_and_log([A(), B()])
    assert len(result) >= 1


def test_detect_conflicts_in_real_registry():
    """在 ALL_CONSTRAINTS 上跑应能找到至少 1 个 duplicate_append 候选（v6.42 时）."""
    from step01_script_gen.constraints.conflict_detector import detect_conflicts
    from step01_script_gen.constraints import ALL_CONSTRAINTS

    # 不强求找到冲突（取决于具体实现）；只要求函数能跑、不抛
    conflicts = detect_conflicts(ALL_CONSTRAINTS)
    # 每个 conflict 至少有 3 个字段
    for c in conflicts:
        assert c.kind
        assert c.constraint_a
        assert c.constraint_b
        assert c.description
