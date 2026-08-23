"""W11. AxisJumpConstraint 单元测试.

测试 3 类场景：
  1. 修复：同场景 画左↔画右 → 后镜统一方向
  2. 幂等：连跑 2 次 = 第 2 次 0 改动
  3. 隔离：场景切换 / 方向一致 → 不改
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_unifies_direction_when_left_right_conflict():
    """同场景 画左 → 画右 → 后镜应被统一到 画左."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 杨旭在画左",
            location="小舟",
        ),
        make_shot(
            index=2,
            prompt="[中景] 刺客在画右",
            location="小舟",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.axis_jump import AxisJumpConstraint

    repairs = AxisJumpConstraint().apply(ctx)

    assert len(repairs) >= 1
    # shot 2 画右 应被改为 画左
    assert "画右" not in script["shots"][1]["prompt"]


def test_unifies_char_layout_position():
    """同场景 画左 → 画右 → 后镜 char_layout 画右 position 应被改."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 杨旭在画左",
            location="小舟",
            char_layout=[{"name": "杨旭", "position": "画左"}],
        ),
        make_shot(
            index=2,
            prompt="[中景] 刺客在画右",
            location="小舟",
            char_layout=[{"name": "刺客", "position": "画右"}],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.axis_jump import AxisJumpConstraint

    repairs = AxisJumpConstraint().apply(ctx)

    assert len(repairs) >= 1
    # shot 2 char_layout 应被改
    layout = script["shots"][1].get("char_layout", [])
    positions = [item.get("position", "") for item in layout]
    assert "画右" not in positions


def test_is_idempotent_after_unified():
    """连跑 2 次 = 第 2 次 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景] 杨旭在画左", location="小舟"),
        make_shot(index=2, prompt="[中景] 刺客在画右", location="小舟"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.axis_jump import AxisJumpConstraint

    c = AxisJumpConstraint()
    r1 = c.apply(ctx)
    r2 = c.apply(ctx)

    assert len(r1) >= 1
    assert len(r2) == 0


def test_skips_when_directions_consistent():
    """方向一致 → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景] 杨旭在画左", location="小舟"),
        make_shot(index=2, prompt="[中景] 听香在画左", location="小舟"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.axis_jump import AxisJumpConstraint

    repairs = AxisJumpConstraint().apply(ctx)

    assert repairs == []


def test_skips_when_scene_switch():
    """场景切换 → 0 修复（豁免）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景] 杨旭在画左", location="小舟"),
        make_shot(index=2, prompt="[中景] 刺客在画右", location="岸边"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.axis_jump import AxisJumpConstraint

    repairs = AxisJumpConstraint().apply(ctx)

    assert repairs == []


def test_constraint_attributes():
    """验证 Constraint 类属性正确 (severity=HARD)."""
    from step01_script_gen.constraints.axis_jump import AxisJumpConstraint
    from step01_script_gen.constraints.types import Severity

    c = AxisJumpConstraint()
    assert c.name == "axis_jump"
    assert c.severity == Severity.HARD
    assert c.skip_hook is True
