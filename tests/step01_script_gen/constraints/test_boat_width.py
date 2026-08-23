"""T1. BoatWidthConstraint 单元测试.

测试 4 类场景：
  1. 修复：「长X米窄身」→「长X米宽1.4米」
  2. 修复：单独「窄身」无"长X米" → 整段删"窄身"
  3. 修复：「长X米无篷」无"宽"字 → 在"无篷"前插"宽1.4米"
  4. 幂等：已含"宽X米"/"宽身" → 跳过
  5. 隔离：无命中 → 0 改动
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_replaces_long_narrow_with_long_wide():
    """「长6米窄身」→「长6米宽1.4米」."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 一叶小舟（长6米窄身无篷舴艋舟）",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.boat_width import BoatWidthConstraint

    repairs = BoatWidthConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert "长6米宽1.4米" in script["shots"][0]["prompt"]
    assert "窄身" not in script["shots"][0]["prompt"]


def test_strips_bare_narrow():
    """「窄身」前后无"长X米" → 整段删除."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 一叶窄身小舟",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.boat_width import BoatWidthConstraint

    repairs = BoatWidthConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert "窄身" not in script["shots"][0]["prompt"]


def test_inserts_width_before_no_canopy():
    """「长X米无篷」无"宽"字 → 在"无篷"前插"宽1.4米"."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 一叶小舟（长5米无篷舴艋舟）",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.boat_width import BoatWidthConstraint

    repairs = BoatWidthConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert "长5米宽1.4米无篷" in script["shots"][0]["prompt"]


def test_is_idempotent_when_already_has_width():
    """已含「宽X米」→ 跳过（幂等）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 一叶小舟（长6米宽1.4米无篷舴艋舟）",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.boat_width import BoatWidthConstraint

    c = BoatWidthConstraint()
    repairs1 = c.apply(ctx)
    repairs2 = c.apply(ctx)

    assert len(repairs1) == 0
    assert len(repairs2) == 0
    # 不会重复插入
    assert script["shots"][0]["prompt"].count("宽1.4米") == 1


def test_no_change_when_no_boat_size_phrase():
    """无「窄身/长X米」→ 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 杨旭坐在船头",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.boat_width import BoatWidthConstraint

    repairs = BoatWidthConstraint().apply(ctx)
    assert repairs == []


def test_processes_keyframes_desc():
    """kf desc 也应被处理."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 场景",
            keyframes=[
                {
                    "stage": "pre",
                    "t_sec": 0.5,
                    "desc": "[中景] 一叶小舟（长6米窄身无篷舴艋舟）",
                }
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.boat_width import BoatWidthConstraint

    repairs = BoatWidthConstraint().apply(ctx)

    assert len(repairs) >= 1
    kf_desc = script["shots"][0]["keyframes"][0]["desc"]
    assert "长6米宽1.4米" in kf_desc
    assert "窄身" not in kf_desc


def test_handles_multiple_fields():
    """prompt/ff_desc/lf_desc/motion_desc 都应被处理."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="一叶小舟（长6米窄身）",
            ff_desc="窄身小舟",
            lf_desc="",
            motion_desc="小舟（长5米无篷）漂在水面",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.boat_width import BoatWidthConstraint

    repairs = BoatWidthConstraint().apply(ctx)

    # 3 字段命中（prompt / ff_desc / motion_desc）
    assert len(repairs) == 3


def test_processes_hook():
    """hook 分镜也应被处理."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script(
        shots=[make_shot(index=1, prompt="[中景] 场景")],
        hook={"prompt": "[特写] 一叶小舟（长6米窄身）"},
    )
    ctx = make_ctx(script)
    from step01_script_gen.constraints.boat_width import BoatWidthConstraint

    BoatWidthConstraint().apply(ctx)

    assert "宽1.4米" in script["hook"]["prompt"]


def test_constraint_attributes():
    """验证 Constraint 类属性."""
    from step01_script_gen.constraints.boat_width import BoatWidthConstraint
    from step01_script_gen.constraints.types import Severity

    c = BoatWidthConstraint()
    assert c.name == "boat_width"
    assert c.severity == Severity.WARN
    assert "prompt" in c.fields
    assert "ff_desc" in c.fields
