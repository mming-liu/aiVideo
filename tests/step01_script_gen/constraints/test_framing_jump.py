"""W10. FramingJumpConstraint 单元测试（G2 裁决后：只报不改）.

测试 3 类场景：
  1. 报告：相邻景别跨度 > 2 级 → 返回 warn 记录，但**不改写** prompt；
  2. 幂等/安全：连跑 2 次都不改稿；
  3. 隔离：跨度 ≤ 2 / 场景切换 / 打斗镜 → 不报告.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_reports_jump_without_rewrite():
    """跨度 > 2 级 → 只报 warn，剧本 prompt 一个字不动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    # 全景(d=5) → 中近景(d=2) |5-2|=3 > 2
    script = make_script([
        make_shot(index=1, prompt="[全景] 镜头1", location="小舟"),
        make_shot(index=2, prompt="[中近景] 镜头2", location="小舟"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.framing_jump import FramingJumpConstraint

    repairs = FramingJumpConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert repairs[0].action == "warn"
    assert repairs[0].before == repairs[0].after
    # 原稿景别必须保持原样（G2：只报不改）
    assert script["shots"][0]["prompt"] == "[全景] 镜头1"
    assert script["shots"][1]["prompt"] == "[中近景] 镜头2"


def test_never_mutates_on_repeated_runs():
    """连跑 2 次都不改稿（warn 记录可重复出现）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[全景] 镜头1", location="小舟"),
        make_shot(index=2, prompt="[中近景] 镜头2", location="小舟"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.framing_jump import FramingJumpConstraint

    c = FramingJumpConstraint()
    r1 = c.apply(ctx)
    r2 = c.apply(ctx)

    assert len(r1) >= 1 and len(r2) >= 1
    assert script["shots"][0]["prompt"] == "[全景] 镜头1"
    assert script["shots"][1]["prompt"] == "[中近景] 镜头2"


def test_skips_when_span_le_2():
    """跨度 ≤ 2 级 → 0 记录."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    # 中景(d=3) → 中近景(d=2) |3-2|=1 ≤ 2
    script = make_script([
        make_shot(index=1, prompt="[中景] 镜头1", location="小舟"),
        make_shot(index=2, prompt="[中近景] 镜头2", location="小舟"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.framing_jump import FramingJumpConstraint

    repairs = FramingJumpConstraint().apply(ctx)

    assert repairs == []


def test_skips_when_scene_switch():
    """场景切换豁免 → 0 记录."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    # 跨度 > 2 但场景切换
    script = make_script([
        make_shot(index=1, prompt="[全景] 小舟", location="小舟"),
        make_shot(index=2, prompt="[中近景] 岸边", location="岸边"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.framing_jump import FramingJumpConstraint

    repairs = FramingJumpConstraint().apply(ctx)

    assert repairs == []


def test_constraint_attributes():
    """验证 Constraint 类属性正确."""
    from step01_script_gen.constraints.framing_jump import FramingJumpConstraint
    from step01_script_gen.constraints.types import Severity

    c = FramingJumpConstraint()
    assert c.name == "framing_jump"
    assert c.severity == Severity.WARN
    assert c.skip_hook is True
