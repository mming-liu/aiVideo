"""D. KfPrePostConflictConstraint 单元测试.

测试 3 类场景：
  1. 修复：pre+post 矛盾 → 删 pre 词，t=0.5; 纯 pre/post 时间错位 → clamp
  2. 幂等：clamp 后值与原值相同 → 跳过
  3. 隔离：未命中 pre/post 模式 → 不改写
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_deletes_pre_when_laying_and_blood():
    """pre + 角色已躺血 → 删 pre 词保留 post."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 刺杀",
            ff_desc="",
            lf_desc="",
            duration_sec=4.0,
            keyframes=[
                {
                    "stage": "climax",
                    "t_sec": 2.0,
                    "desc": "刺客半身探出举刀，杨旭仰卧心口洇痕",
                },
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_pre_post_conflict import KfPrePostConflictConstraint

    repairs = KfPrePostConflictConstraint().apply(ctx)

    assert len(repairs) >= 1
    kf = script["shots"][0]["keyframes"][0]
    # pre 词应被删除
    assert "半身探出" not in kf["desc"]
    assert "举刀" not in kf["desc"]
    # post 应保留
    assert "仰卧" in kf["desc"] or "洇痕" in kf["desc"]
    # t 应为 0.5 × duration_sec
    assert abs(kf["t_sec"] - 2.0) < 0.01  # 4.0 × 0.5 = 2.0


def test_clamps_pure_pre_to_early_segment():
    """纯 pre 但 t>0.33×dur → 压到 0.2×dur."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="",
            ff_desc="",
            lf_desc="",
            duration_sec=4.0,
            keyframes=[
                {
                    "stage": "approach",
                    "t_sec": 3.0,  # 3.0 / 4.0 = 0.75 > 0.33
                    "desc": "刺客破水而出",  # 仅 pre 词
                },
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_pre_post_conflict import KfPrePostConflictConstraint

    repairs = KfPrePostConflictConstraint().apply(ctx)

    assert len(repairs) == 1
    assert repairs[0].action == "clamp"
    kf = script["shots"][0]["keyframes"][0]
    # 应 clamp 到 0.2 × 4.0 = 0.8
    assert abs(kf["t_sec"] - 0.8) < 0.01


def test_clamps_pure_post_to_late_segment():
    """纯 post 但 t<0.67×dur → 拉到 0.8×dur."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="",
            ff_desc="",
            lf_desc="",
            duration_sec=4.0,
            keyframes=[
                {
                    "stage": "after",
                    "t_sec": 0.5,  # 0.5 / 4.0 = 0.125 < 0.67
                    "desc": "杨旭已没入",  # 仅 post 词
                },
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_pre_post_conflict import KfPrePostConflictConstraint

    repairs = KfPrePostConflictConstraint().apply(ctx)

    assert len(repairs) == 1
    assert repairs[0].action == "clamp"
    kf = script["shots"][0]["keyframes"][0]
    # 应 clamp 到 0.8 × 4.0 = 3.2
    assert abs(kf["t_sec"] - 3.2) < 0.01


def test_is_idempotent_no_double_clamp():
    """clamp 后再跑 = 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="",
            ff_desc="",
            lf_desc="",
            duration_sec=4.0,
            keyframes=[
                {
                    "stage": "approach",
                    "t_sec": 3.0,
                    "desc": "刺客破水而出",
                },
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_pre_post_conflict import KfPrePostConflictConstraint

    c = KfPrePostConflictConstraint()
    repairs1 = c.apply(ctx)
    repairs2 = c.apply(ctx)

    assert len(repairs1) == 1
    assert len(repairs2) == 0


def test_no_change_when_neither_pre_nor_post():
    """无 pre/post 关键词 → 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="",
            ff_desc="",
            lf_desc="",
            duration_sec=4.0,
            keyframes=[
                {
                    "stage": "react",
                    "t_sec": 2.0,
                    "desc": "杨旭捂嘴惊叫",
                },
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_pre_post_conflict import KfPrePostConflictConstraint

    repairs = KfPrePostConflictConstraint().apply(ctx)
    assert repairs == []


def test_no_change_when_t_out_of_range():
    """t<=0 或 t>=dur → 跳过（无效 t）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="",
            ff_desc="",
            lf_desc="",
            duration_sec=4.0,
            keyframes=[
                {
                    "stage": "approach",
                    "t_sec": 0,  # 边界
                    "desc": "刺客破水而出",
                },
                {
                    "stage": "approach",
                    "t_sec": 4.0,  # 边界
                    "desc": "刺客破水而出",
                },
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_pre_post_conflict import KfPrePostConflictConstraint

    repairs = KfPrePostConflictConstraint().apply(ctx)
    assert repairs == []


def test_handles_hook():
    """hook kf 也应被处理."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script(
        shots=[make_shot(index=1, prompt="x", ff_desc="", lf_desc="")],
        hook={
            "prompt": "hook",
            "ff_desc": "",
            "lf_desc": "",
            "duration_sec": 4.0,
            "keyframes": [
                {
                    "stage": "approach",
                    "t_sec": 3.0,
                    "desc": "刺客破水而出",
                },
            ],
        },
    )
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_pre_post_conflict import KfPrePostConflictConstraint

    repairs = KfPrePostConflictConstraint().apply(ctx)

    # hook kf 应被 clamp
    assert len(repairs) == 1
    kf = script["hook"]["keyframes"][0]
    assert abs(kf["t_sec"] - 0.8) < 0.01


def test_constraint_attributes():
    """验证 Constraint 类属性."""
    from step01_script_gen.constraints.kf_pre_post_conflict import KfPrePostConflictConstraint
    from step01_script_gen.constraints.types import Severity

    c = KfPrePostConflictConstraint()
    assert c.name == "kf_temporal_consistency"
    assert c.severity == Severity.WARN
    assert c.fields == ()  # 不扫文本字段
