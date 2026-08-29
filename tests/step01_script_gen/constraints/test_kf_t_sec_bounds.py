"""F. KfTSecBoundsConstraint 单元测试."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_clamps_t_above_90_percent():
    """t > 0.9 × dur → 按 stage 语义位置推导（climax = 0.42 × dur）."""
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
    assert abs(kf["t_sec"] - 1.68) < 0.01  # climax = 0.42 × 4.0


def test_clamps_t_below_0_1s():
    """t < 0.1 且 dur > 0.2 → 按 stage 推导（pre = 0.1 × dur）."""
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
    assert abs(kf["t_sec"] - 0.4) < 0.001  # pre = 0.10 × 4.0


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


def test_distributes_collapsed_kfs_by_stage():
    """全镜 kf 同值塌缩（各 stage 同一 t_sec）→ 按 stage 顺序摊到 [0.1, 0.9]×dur."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="",
            duration_sec=5.0,
            keyframes=[
                {"stage": "pre", "t_sec": 4.5, "desc": "潜伏"},
                {"stage": "approach", "t_sec": 4.5, "desc": "破水"},
                {"stage": "climax", "t_sec": 4.5, "desc": "刺入"},
                {"stage": "react", "t_sec": 4.5, "desc": "闷哼"},
                {"stage": "after", "t_sec": 4.5, "desc": "已倒"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_t_sec_bounds import KfTSecBoundsConstraint

    repairs = KfTSecBoundsConstraint().apply(ctx)

    # 5 个 kf 摊开（5 个 distribute + clamp 越界 4.5>4.5? 0.9×5=4.5 → 不 clamp）
    kfs = script["shots"][0]["keyframes"]
    times = [k["t_sec"] for k in kfs]
    # 摊开后按 stage 顺序严格递增：pre=0.5, approach=1.5, climax=2.5, react=3.5, after=4.5
    assert times == sorted(times)
    assert times == [0.5, 1.5, 2.5, 3.5, 4.5]
    # 首段 pre 靠前，末段 after 靠尾
    assert abs(kfs[0]["t_sec"] - 0.5) < 0.01
    assert abs(kfs[-1]["t_sec"] - 4.5) < 0.01


def test_distribute_idempotent_second_run():
    """摊开后时刻已分散 → 再跑 0 修复（幂等）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="",
            duration_sec=5.0,
            keyframes=[
                {"stage": "pre", "t_sec": 4.5, "desc": "潜伏"},
                {"stage": "approach", "t_sec": 4.5, "desc": "破水"},
                {"stage": "after", "t_sec": 4.5, "desc": "已倒"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_t_sec_bounds import KfTSecBoundsConstraint

    c = KfTSecBoundsConstraint()
    repairs1 = c.apply(ctx)
    repairs2 = c.apply(ctx)

    assert len(repairs1) >= 1
    assert len(repairs2) == 0


def test_no_distribute_when_times_already_distinct():
    """kf 时刻已分散 → 不摊开（保持原值）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="",
            duration_sec=5.0,
            keyframes=[
                {"stage": "pre", "t_sec": 0.5, "desc": "潜伏"},
                {"stage": "climax", "t_sec": 2.5, "desc": "刺入"},
                {"stage": "after", "t_sec": 4.5, "desc": "已倒"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_t_sec_bounds import KfTSecBoundsConstraint

    repairs = KfTSecBoundsConstraint().apply(ctx)

    assert repairs == []
    assert script["shots"][0]["keyframes"][0]["t_sec"] == 0.5
    assert script["shots"][0]["keyframes"][2]["t_sec"] == 4.5


def test_no_distribute_when_all_same_stage():
    """全镜同 stage（如 2 个 climax）→ 越界按 stage 推导同值，不摊开（同一语义时刻）. """
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="",
            duration_sec=5.0,
            keyframes=[
                {"stage": "climax", "t_sec": 4.9, "desc": "刺入"},
                {"stage": "climax", "t_sec": 4.9, "desc": "溅起"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_t_sec_bounds import KfTSecBoundsConstraint

    repairs = KfTSecBoundsConstraint().apply(ctx)

    # 两个越界 → climax 推导到 0.42×5=2.1；同 stage 不摊开（保持同时刻）
    assert len(repairs) == 2
    assert abs(script["shots"][0]["keyframes"][0]["t_sec"] - 2.1) < 0.01
    assert abs(script["shots"][0]["keyframes"][1]["t_sec"] - 2.1) < 0.01


def test_constraint_attributes():
    from step01_script_gen.constraints.kf_t_sec_bounds import KfTSecBoundsConstraint
    from step01_script_gen.constraints.types import Severity

    c = KfTSecBoundsConstraint()
    assert c.name == "kf_t_sec_bounds"
    assert c.severity == Severity.WARN
    assert c.fields == ()
