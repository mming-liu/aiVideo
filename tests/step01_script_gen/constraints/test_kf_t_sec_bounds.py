"""F. KfTSecBoundsConstraint 单元测试（v6.76 起为只报告语义）.

v6.76（2026-09-19）语义变更：越界不再按 stage 推导位置，全镜同值塌缩不再
按 stage 摊开，一律只上报并保留原值。理由见
constraints/kf_t_sec_bounds.py 模块 docstring 与
docs/step01-temporal-writers-audit-2026-09-19.md。

本文件断言的是「检测到 + 上报 + **字段原封不动**」。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_reports_t_above_90_percent():
    """t > 0.9 × dur → 上报，但保留原值（不再按 stage 推导到 1.68）."""
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
    assert repairs[0].action == "warn"
    kf = script["shots"][0]["keyframes"][0]
    assert kf["t_sec"] == 4.5  # 原值保留


def test_reports_t_below_0_1s():
    """t < 0.1 且 dur > 0.2 → 上报，但保留原值（不再按 stage 推导到 0.4）."""
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
    assert repairs[0].action == "warn"
    assert script["shots"][0]["keyframes"][0]["t_sec"] == 0.05


def test_report_only_is_idempotent():
    """只报告语义天然幂等：连跑两次都是同一条 warn，字段始终不变."""
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
    assert len(repairs2) == 1
    assert script["shots"][0]["keyframes"][0]["t_sec"] == 4.5


def test_no_report_when_t_in_range():
    """t 在 [0.1, 0.9×dur] → 0 上报."""
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


def test_reports_collapsed_kfs_by_stage():
    """全镜 kf 同值（各 stage 同一 t_sec）→ 上报塌缩，保留原值不摊开."""
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

    # 0.9×5.0 = 4.5，4.5 > 4.5 为假 → 不算越界；只有塌缩一条上报
    assert len(repairs) == 1
    assert repairs[0].action == "warn"
    kfs = script["shots"][0]["keyframes"]
    assert [k["t_sec"] for k in kfs] == [4.5] * 5  # 全部保持原值


def test_collapse_report_idempotent():
    """塌缩上报同样幂等：第二次仍是一模一样的报告，字段不变."""
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

    assert len(repairs1) == 1
    assert len(repairs2) == 1
    assert [k["t_sec"] for k in script["shots"][0]["keyframes"]] == [4.5, 4.5, 4.5]


def test_no_report_when_times_already_distinct():
    """kf 时刻已分散 → 既无越界也无塌缩，0 上报."""
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


def test_no_collapse_report_when_all_same_stage():
    """全镜同 stage（2 个 climax）→ 只按越界上报 1 条，不当作塌缩."""
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

    # 两个 kf 都越界（4.9 > 4.5），聚合成 1 条 warn；stage 只有 1 种 → 不报塌缩
    assert len(repairs) == 1
    assert repairs[0].action == "warn"
    assert "keyframes[0]" in repairs[0].before and "keyframes[1]" in repairs[0].before
    kfs = script["shots"][0]["keyframes"]
    assert [k["t_sec"] for k in kfs] == [4.9, 4.9]


def test_constraint_attributes():
    from step01_script_gen.constraints.kf_t_sec_bounds import KfTSecBoundsConstraint
    from step01_script_gen.constraints.types import Severity

    c = KfTSecBoundsConstraint()
    assert c.name == "kf_t_sec_bounds"
    assert c.severity == Severity.WARN
    assert c.fields == ()
