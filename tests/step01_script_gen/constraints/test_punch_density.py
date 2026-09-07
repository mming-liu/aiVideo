"""L. PunchDensityConstraint 单元测试（v6.62 收敛后契约）.

v6.62 起该约束**不再给末镜 prompt 注入「局势骤变，N个爆点叠加」**，改为：
剥离历史注入残留（幂等）+ 密度不足 record_violation 上报（WARN，不改数据）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _make_constraint():
    from step01_script_gen.constraints.punch_density import PunchDensityConstraint
    return PunchDensityConstraint()


def test_shortage_records_violation_without_mutation():
    """90s 全片 0 爆点（应 3 个）→ 记 Violation，prompt 原样."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景] 凉亭", duration_sec=30.0),
        make_shot(index=2, prompt="[中景] 杨旭", duration_sec=30.0),
        make_shot(index=3, prompt="[中景] 听香", duration_sec=30.0),
    ])
    ctx = make_ctx(script)
    repairs = _make_constraint().apply(ctx)

    assert repairs == []
    violations = ctx.drain_violations()
    assert len(violations) == 1
    assert violations[0].constraint_name == "punch_density"
    assert "缺 3 个" in violations[0].message
    assert script["shots"][-1]["prompt"] == "[中景] 听香"


def test_strips_injected_residue_idempotent():
    """历史注入残留被剥离；重跑零新增（旧幂等语义的新形态）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景] 凉亭", duration_sec=30.0),
        make_shot(index=2, prompt="[中景] 杨旭", duration_sec=30.0),
        make_shot(index=3, prompt="末镜画面。局势骤变，2个爆点叠加", duration_sec=30.0),
    ])
    ctx = make_ctx(script)
    c = _make_constraint()
    r1 = c.apply(ctx)
    r2 = c.apply(ctx)

    assert len(r1) >= 1
    assert len(r2) == 0
    assert script["shots"][-1]["prompt"] == "末镜画面"


def test_skips_when_density_meets_target():
    """全片爆点达标 → 0 修复 0 违例."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    # 60s / 30s = 2 个目标，全片 2 爆点达标
    script = make_script([
        make_shot(index=1, prompt="[中景] 刺客刺杀", duration_sec=30.0),
        make_shot(index=2, prompt="[中景] 杨旭对峙", duration_sec=30.0),
    ])
    ctx = make_ctx(script)

    repairs = _make_constraint().apply(ctx)
    assert repairs == []
    assert ctx.drain_violations() == []


def test_constraint_attributes():
    from step01_script_gen.constraints.types import Severity

    c = _make_constraint()
    assert c.name == "punch_density"
    assert c.severity == Severity.WARN
    # v6.62：注入停用，repair_marker（"局势骤变"幂等锚）随之移除
    assert c.repair_marker is None
