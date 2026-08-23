"""L. PunchDensityConstraint 单元测试."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_appends_when_density_below_target():
    """90s 全片 0 爆点（应 3 个）→ 末镜追加."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景] 凉亭", duration_sec=30.0),
        make_shot(index=2, prompt="[中景] 杨旭", duration_sec=30.0),
        make_shot(index=3, prompt="[中景] 听香", duration_sec=30.0),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.punch_density import PunchDensityConstraint

    repairs = PunchDensityConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert "局势骤变" in script["shots"][-1]["prompt"]


def test_is_idempotent_no_duplicate():
    """末镜已含'局势骤变' → 跳过."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景] 凉亭", duration_sec=30.0),
        make_shot(index=2, prompt="[中景] 杨旭", duration_sec=30.0),
        make_shot(index=3, prompt="局势骤变", duration_sec=30.0),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.punch_density import PunchDensityConstraint

    c = PunchDensityConstraint()
    repairs1 = c.apply(ctx)
    repairs2 = c.apply(ctx)
    assert len(repairs1) == 0
    assert len(repairs2) == 0


def test_skips_when_density_meets_target():
    """全片爆点达标 → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    # 60s / 30s = 2 个目标，全片 2 爆点达标
    script = make_script([
        make_shot(index=1, prompt="[中景] 刺客刺杀", duration_sec=30.0),
        make_shot(index=2, prompt="[中景] 杨旭对峙", duration_sec=30.0),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.punch_density import PunchDensityConstraint

    repairs = PunchDensityConstraint().apply(ctx)
    assert repairs == []


def test_constraint_attributes():
    from step01_script_gen.constraints.punch_density import PunchDensityConstraint
    from step01_script_gen.constraints.types import Severity

    c = PunchDensityConstraint()
    assert c.name == "punch_density"
    assert c.severity == Severity.WARN
    assert c.repair_marker == "局势骤变"
