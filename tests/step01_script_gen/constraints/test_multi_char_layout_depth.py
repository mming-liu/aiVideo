"""W5. MultiCharLayoutDepthConstraint 单元测试.

测试 3 类场景：
  1. 修复：全同侧（画左/画右）无纵深 → 次要角色改"画中甲板/船舷"
  2. 幂等：连跑 2 次 = 第 2 次 0 改动
  3. 隔离：已有纵深 / 单一角色 / 主角色 → 不改
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_adds_depth_to_secondary_chars():
    """全同侧（画左）+ 多角色 → 次要角色应被改纵深."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            scene="小舟",
            char_layout=[
                {"name": "杨旭", "role": "主", "position": "画左"},
                {"name": "听香", "role": "次", "position": "画左"},
                {"name": "刺客", "role": "次", "position": "画左"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.multi_char_layout_depth import MultiCharLayoutDepthConstraint

    repairs = MultiCharLayoutDepthConstraint().apply(ctx)

    # 次要角色 position 应被改
    layout = script["shots"][0]["char_layout"]
    # 横向场景（船）取 anchor 第 2 个（船中）—— 期望 "船中" 而非"画中甲板"
    positions = [item.get("position", "") for item in layout]
    assert any("船中" in p or "甲板" in p for p in positions), f"应有船中或甲板: {positions}"


def test_is_idempotent():
    """连跑 2 次 = 第 2 次 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            scene="小舟",
            char_layout=[
                {"name": "杨旭", "role": "主", "position": "画左"},
                {"name": "听香", "role": "次", "position": "画左"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.multi_char_layout_depth import MultiCharLayoutDepthConstraint

    c = MultiCharLayoutDepthConstraint()
    r1 = c.apply(ctx)
    r2 = c.apply(ctx)

    # 第一次应至少有 1 次修复
    assert len(r1) >= 1
    # 第二次不应再修复（已有纵深）
    assert len(r2) == 0


def test_skips_when_has_depth():
    """已有纵深词 → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            scene="小舟",
            char_layout=[
                {"name": "杨旭", "role": "主", "position": "画左"},
                {"name": "听香", "role": "次", "position": "画中甲板"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.multi_char_layout_depth import MultiCharLayoutDepthConstraint

    repairs = MultiCharLayoutDepthConstraint().apply(ctx)

    assert repairs == []


def test_skips_single_char():
    """单一角色 → 0 修复（len(layout) < 2）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            scene="小舟",
            char_layout=[
                {"name": "杨旭", "role": "主", "position": "画左"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.multi_char_layout_depth import MultiCharLayoutDepthConstraint

    repairs = MultiCharLayoutDepthConstraint().apply(ctx)

    assert repairs == []


def test_keeps_main_role_position():
    """主角色（role=主）position 不应被改."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            scene="小舟",
            char_layout=[
                {"name": "杨旭", "role": "主", "position": "画左"},
                {"name": "听香", "role": "次", "position": "画左"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.multi_char_layout_depth import MultiCharLayoutDepthConstraint

    repairs = MultiCharLayoutDepthConstraint().apply(ctx)

    # 主角色 position 保持 画左
    main = script["shots"][0]["char_layout"][0]
    assert main["position"] == "画左"


def test_constraint_attributes():
    """验证 Constraint 类属性正确."""
    from step01_script_gen.constraints.multi_char_layout_depth import MultiCharLayoutDepthConstraint
    from step01_script_gen.constraints.types import Severity

    c = MultiCharLayoutDepthConstraint()
    assert c.name == "multi_char_layout_depth"
    assert c.severity == Severity.WARN
    assert c.skip_hook is True


# === v6.49 扩展测试（08-28 跑批实测 镜3 漏修） ===

def test_adds_depth_when_no_secondary_role_v6_49():
    """v6.49 扩展：所有角色都无 role=次/背景，全画左 → 强制第 2 个改纵深.

    08-28 chapter_001 镜3 实测：[('杨旭', '画左'), ('听香', '画左')]，两个
    角色都没标 role=次，原约束只改 role=次/背景 → 漏修。本版扩展：
    无次/背景角色 + 同侧 2+ → 强制第 2 个起改纵深。
    """
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            scene="小舟",
            char_layout=[
                {"name": "杨旭", "role": "主", "position": "画左"},
                {"name": "听香", "role": "主", "position": "画左"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.multi_char_layout_depth import MultiCharLayoutDepthConstraint

    repairs = MultiCharLayoutDepthConstraint().apply(ctx)

    assert len(repairs) >= 1
    positions = [item.get("position", "") for item in script["shots"][0]["char_layout"]]
    # 第 2 个角色（听香）应被改为船中/甲板/画中
    assert any("船中" in p or "甲板" in p or "画中" in p for p in positions[1:]), \
        f"expected depth in positions[1:], got {positions}"


def test_keeps_first_char_unchanged_v6_49():
    """v6.49 扩展：第 1 个角色保持画左不动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            scene="小舟",
            char_layout=[
                {"name": "杨旭", "role": "主", "position": "画左"},
                {"name": "听香", "role": "主", "position": "画左"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.multi_char_layout_depth import MultiCharLayoutDepthConstraint

    MultiCharLayoutDepthConstraint().apply(ctx)

    # 第 1 个角色（杨旭）应保持画左
    assert script["shots"][0]["char_layout"][0]["position"] == "画左"
