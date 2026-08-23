"""F1. KfMonotonicConstraint 单元测试（v6.43 新增）.

8-23 跑批实测：
  - shot4 两 kf 同 t=2.0 → 后段动作不展开
  - shot5 climax t=3.5 > after t=3.0 → 物理时刻倒装

修法：检测 t_sec 序列是否升序 → 不升序则均匀分布到 (0.1, 0.9×dur)。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_reshapes_descending_pair():
    """climax t=3.5 > after t=3.0（shot5 实测）→ 重新均匀分布."""
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
    assert len(repairs) == 2
    kfs = script["shots"][0]["keyframes"]
    # 均匀分布 N=2 → (lo=0.1, hi=3.6) 区间内 1/3, 2/3 处
    # t_0 = 0.1 + 3.5 × 1/3 ≈ 1.267, t_1 = 0.1 + 3.5 × 2/3 ≈ 2.433
    assert kfs[0]["t_sec"] < kfs[1]["t_sec"]
    assert abs(kfs[0]["t_sec"] - 1.267) < 0.01
    assert abs(kfs[1]["t_sec"] - 2.433) < 0.01


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


def test_is_idempotent_no_double_reshape():
    """重分布后再次运行 = 0 改动."""
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
    repairs1 = c.apply(ctx)
    repairs2 = c.apply(ctx)
    assert len(repairs1) == 2
    assert repairs2 == []


def test_constraint_attributes():
    from step01_script_gen.constraints.kf_monotonic import KfMonotonicConstraint
    from step01_script_gen.constraints.types import Severity

    c = KfMonotonicConstraint()
    assert c.name == "kf_monotonic"
    assert c.severity == Severity.WARN
    assert c.fields == ()
