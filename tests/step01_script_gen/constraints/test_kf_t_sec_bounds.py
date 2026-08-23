"""F. KfTSecBoundsConstraint 单元测试."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_clamps_t_above_90_percent():
    """t > 0.9 × dur → clamp 到 0.9 × dur."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="",
            duration_sec=4.0,
            keyframes=[
                {"stage": "climax", "t_sec": 4.5, "desc": "刀刺入"},  # 4.5 > 0.9×4=3.6
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_t_sec_bounds import KfTSecBoundsConstraint

    repairs = KfTSecBoundsConstraint().apply(ctx)

    assert len(repairs) == 1
    kf = script["shots"][0]["keyframes"][0]
    assert abs(kf["t_sec"] - 3.6) < 0.01  # 0.9 × 4.0


def test_clamps_t_below_0_1s():
    """t < 0.1 且 dur > 0.2 → 拉到 0.1s."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="",
            duration_sec=4.0,
            keyframes=[
                {"stage": "pre", "t_sec": 0.05, "desc": "潜伏"},  # 0.05 < 0.1
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_t_sec_bounds import KfTSecBoundsConstraint

    repairs = KfTSecBoundsConstraint().apply(ctx)

    assert len(repairs) == 1
    kf = script["shots"][0]["keyframes"][0]
    assert abs(kf["t_sec"] - 0.1) < 0.001


def test_is_idempotent_no_double_clamp():
    """clamp 后再跑 = 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="",
            duration_sec=4.0,
            keyframes=[
                {"stage": "climax", "t_sec": 4.5, "desc": "刀刺入"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_t_sec_bounds import KfTSecBoundsConstraint

    c = KfTSecBoundsConstraint()
    repairs1 = c.apply(ctx)
    repairs2 = c.apply(ctx)

    assert len(repairs1) == 1
    assert len(repairs2) == 0


def test_no_repair_when_t_in_range():
    """t 在 [0.1, 0.9×dur] → 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="",
            duration_sec=4.0,
            keyframes=[
                {"stage": "climax", "t_sec": 2.0, "desc": "刀刺入"},  # 2.0 在 [0.1, 3.6]
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_t_sec_bounds import KfTSecBoundsConstraint

    repairs = KfTSecBoundsConstraint().apply(ctx)
    assert repairs == []


def test_constraint_attributes():
    from step01_script_gen.constraints.kf_t_sec_bounds import KfTSecBoundsConstraint
    from step01_script_gen.constraints.types import Severity

    c = KfTSecBoundsConstraint()
    assert c.name == "kf_t_sec_bounds"
    assert c.severity == Severity.WARN
    assert c.fields == ()
