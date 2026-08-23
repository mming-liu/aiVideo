"""W3. CombatImpactConstraint 单元测试.

测试 3 类场景：
  1. 修复：打斗镜缺反馈词 → 按场景类型追加
  2. 幂等：连跑 2 次 = 第 2 次 0 改动
  3. 隔离：非打斗镜 / 已含反馈词 → 不追加
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_appends_impact_to_water_combat():
    """水上打斗 → 应追加 '水花四溅，水波激荡'."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 刺客在水面打斗",
            motion_desc="破水刺杀",
            location="小舟",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.combat_impact import CombatImpactConstraint

    repairs = CombatImpactConstraint().apply(ctx)

    assert len(repairs) >= 1
    prompt = script["shots"][0]["prompt"]
    assert "水花" in prompt or "水波" in prompt


def test_appends_impact_to_land_combat():
    """陆地打斗 → 应追加 '衣袂翻飞，碎屑飞溅'."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 山林战斗",
            motion_desc="剑士搏杀",
            location="山林",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.combat_impact import CombatImpactConstraint

    repairs = CombatImpactConstraint().apply(ctx)

    assert len(repairs) >= 1
    prompt = script["shots"][0]["prompt"]
    assert "衣袂" in prompt or "碎屑" in prompt


def test_is_idempotent():
    """连跑 2 次 = 第 2 次 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 刺客刺杀",
            motion_desc="举刀",
            location="小舟",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.combat_impact import CombatImpactConstraint

    c = CombatImpactConstraint()
    r1 = c.apply(ctx)
    r2 = c.apply(ctx)

    assert len(r1) >= 1
    assert len(r2) == 0


def test_skips_non_combat_shot():
    """非打斗镜（无 _COMBAT_KEYWORDS）→ 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 杨旭举杯喝酒",
            narration_segment="夜宴场景",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.combat_impact import CombatImpactConstraint

    repairs = CombatImpactConstraint().apply(ctx)

    assert repairs == []


def test_skips_when_already_has_impact_words():
    """已含 _IMPACT_WORDS 任一词 → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 刺客刺杀，水花四溅",
            motion_desc="破水",
            location="小舟",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.combat_impact import CombatImpactConstraint

    repairs = CombatImpactConstraint().apply(ctx)

    assert repairs == []


def test_appends_via_narration_combat():
    """narration 含 _COMBAT_KEYWORDS → 也应被识别为打斗镜."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 听香尖叫",
            narration_segment="救我！有人刺杀我！",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.combat_impact import CombatImpactConstraint

    repairs = CombatImpactConstraint().apply(ctx)

    assert len(repairs) >= 1


def test_constraint_attributes():
    """验证 Constraint 类属性正确."""
    from step01_script_gen.constraints.combat_impact import CombatImpactConstraint
    from step01_script_gen.constraints.types import Severity

    c = CombatImpactConstraint()
    assert c.name == "combat_impact"
    assert c.severity == Severity.WARN
    assert c.repair_marker is not None
