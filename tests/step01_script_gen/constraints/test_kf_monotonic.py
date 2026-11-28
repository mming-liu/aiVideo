"""F1. KfMonotonicConstraint 单元测试（v6.43 新增；v6.76 改只报告）.

8-23 跑批实测：
  - shot4 两 kf 同 t=2.0 → 后段动作不展开
  - shot5 climax t=3.5 > after t=3.0 → 物理时刻倒装

v6.76（2026-09-19）语义变更：**只报告，不再重排**。
理由见 constraints/kf_monotonic.py 模块 docstring 与
docs/step01-temporal-writers-audit-2026-09-19.md。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_reports_descending_pair_without_reshaping():
    """climax t=3.5 > after t=3.0（shot5 实测）→ 上报，但保留原值不重排."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="",
            duration_sec=4.0,
            keyframes=[
                {"stage": "climax", "t_sec": 3.5, "desc": "刀刺入"},
                {"stage": "after", "t_sec": 3.0, "desc": "血涌出"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_monotonic import KfMonotonicConstraint

    repairs = KfMonotonicConstraint().apply(ctx)
    # 只报告：一条 warn，不是逐步 clamp
    assert len(repairs) == 1
    assert repairs[0].action == "warn"
    assert repairs[0].target == "keyframes[*].t_sec"
    # 关键：原值必须原封不动（v6.76 起不再重排）
    kfs = script["shots"][0]["keyframes"]
    assert kfs[0]["t_sec"] == 3.5
    assert kfs[1]["t_sec"] == 3.0


def test_equal_t_pair_no_repair():
    """shot4 两 kf 同 t=2.0：非严格降序 → 不触发（v6.43 决策：仅严格降序改写）.

    严格降序才会破坏「后段动作不展开」；相等 t 已是边界情况，跳过以免
    误改「同帧分镜」（如「刀刺入/血涌出」实际上希望在同一时刻）。如需
    修复相等 t，由 LLM regen 而非硬修复层处理。
    """
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="",
            duration_sec=4.0,
            keyframes=[
                {"stage": "pre", "t_sec": 2.0, "desc": "起手"},
                {"stage": "climax", "t_sec": 2.0, "desc": "刀刺入"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_monotonic import KfMonotonicConstraint

    repairs = KfMonotonicConstraint().apply(ctx)
    assert repairs == []
    kfs = script["shots"][0]["keyframes"]
    assert kfs[0]["t_sec"] == 2.0
    assert kfs[1]["t_sec"] == 2.0


def test_no_change_when_already_monotonic():
    """t 已升序 → 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="",
            duration_sec=4.0,
            keyframes=[
                {"stage": "pre", "t_sec": 1.0, "desc": "起手"},
                {"stage": "climax", "t_sec": 2.5, "desc": "刀刺入"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_monotonic import KfMonotonicConstraint

    repairs = KfMonotonicConstraint().apply(ctx)
    assert repairs == []


def test_no_change_for_single_kf():
    """N=1 kf → 不构成降序对，跳过."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="",
            duration_sec=4.0,
            keyframes=[{"stage": "climax", "t_sec": 2.0, "desc": "刀刺入"}],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_monotonic import KfMonotonicConstraint

    repairs = KfMonotonicConstraint().apply(ctx)
    assert repairs == []


def test_report_only_is_idempotent():
    """只报告语义天然幂等：连跑两次都不改字段，结果一致."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="",
            duration_sec=4.0,
            keyframes=[
                {"stage": "climax", "t_sec": 3.5, "desc": "刀刺入"},
                {"stage": "after", "t_sec": 3.0, "desc": "血涌出"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_monotonic import KfMonotonicConstraint

    c = KfMonotonicConstraint()
    before = [dict(kf) for kf in script["shots"][0]["keyframes"]]
    repairs1 = c.apply(ctx)
    repairs2 = c.apply(ctx)
    after = [dict(kf) for kf in script["shots"][0]["keyframes"]]
    # 两次都上报同一条 warn，且字段始终不变
    assert len(repairs1) == 1
    assert len(repairs2) == 1
    assert repairs1[0].action == "warn" and repairs2[0].action == "warn"
    assert before == after


def test_constraint_attributes():
    from step01_script_gen.constraints.kf_monotonic import KfMonotonicConstraint
    from step01_script_gen.constraints.types import Severity

    c = KfMonotonicConstraint()
    assert c.name == "kf_monotonic"
    assert c.severity == Severity.WARN
    assert c.fields == ()
