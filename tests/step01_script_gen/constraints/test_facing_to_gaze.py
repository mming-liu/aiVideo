"""T2. FacingToGazeConstraint 单元测试.

测试 4 类场景：
  1. 修复：char_layout「听香 面向杨旭」+ prompt 缺 gaze → 追加「目光凝望杨旭」
  2. 幂等：prompt 已含「目光凝望杨旭」→ 跳过
  3. 隔离：facing 是方向词（画左/画右）→ 0 改动
  4. 多 shot + keyframes
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_appends_gaze_after_facing_target_name():
    """char_layout 写「听香 面向杨旭」+ prompt 缺 gaze → 追加「目光凝望杨旭」."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 听香撑油纸伞立于身侧",
            char_layout=[
                {"name": "听香", "facing": "面向杨旭"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.facing_to_gaze import FacingToGazeConstraint

    repairs = FacingToGazeConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert "目光凝望杨旭" in script["shots"][0]["prompt"]


def test_is_idempotent_when_already_has_gaze():
    """prompt 已含「目光凝望杨旭」→ 跳过（幂等）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 听香撑油纸伞立于身侧，目光凝望杨旭",
            char_layout=[
                {"name": "听香", "facing": "面向杨旭"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.facing_to_gaze import FacingToGazeConstraint

    c = FacingToGazeConstraint()
    repairs1 = c.apply(ctx)
    repairs2 = c.apply(ctx)

    assert len(repairs1) == 0
    assert len(repairs2) == 0
    # 不会重复追加
    assert script["shots"][0]["prompt"].count("目光凝望杨旭") == 1


def test_skips_when_facing_is_direction_word():
    """facing 是方向词（画左/画右）→ 不追加 gaze."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 听香撑油纸伞",
            char_layout=[
                {"name": "听香", "facing": "面向画左"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.facing_to_gaze import FacingToGazeConstraint

    repairs = FacingToGazeConstraint().apply(ctx)
    assert repairs == []
    # 不应追加
    assert "目光凝望" not in script["shots"][0]["prompt"]


def test_no_change_when_no_facing_in_layout():
    """char_layout 无 facing 字段 → 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 听香撑油纸伞",
            char_layout=[
                {"name": "听香"},  # 无 facing
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.facing_to_gaze import FacingToGazeConstraint

    repairs = FacingToGazeConstraint().apply(ctx)
    assert repairs == []


def test_appends_gaze_to_keyframes_desc():
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
                    "desc": "[中景] 听香立于身侧",
                }
            ],
            char_layout=[
                {"name": "听香", "facing": "面向杨旭"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.facing_to_gaze import FacingToGazeConstraint

    repairs = FacingToGazeConstraint().apply(ctx)

    assert len(repairs) >= 1
    kf_desc = script["shots"][0]["keyframes"][0]["desc"]
    assert "目光凝望杨旭" in kf_desc


def test_handles_multiple_facing_pairs():
    """多对 facing（听香面向杨旭 + 杨旭面向听香）→ 只注入首个，不互注矛盾.

    08-27 修复：逐对注入会互注出「目光凝望杨旭，目光凝望听香」式同主语双向看
    （镜4 kf 实测根因）。首对注入后，第二对在分句边界后已是 gaze 短语 → 跳过。
    """
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 听香与杨旭对视",
            char_layout=[
                {"name": "听香", "facing": "面向杨旭"},
                {"name": "杨旭", "facing": "面向听香"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.facing_to_gaze import FacingToGazeConstraint

    repairs = FacingToGazeConstraint().apply(ctx)

    # 有注入发生，但绝无相邻互斥视线
    assert len(repairs) >= 1
    p = script["shots"][0]["prompt"]
    assert "目光凝望杨旭" in p
    assert "目光凝望听香，目光凝望杨旭" not in p
    assert "目光凝望杨旭，目光凝望听香" not in p


def test_skips_when_subject_is_gaze_object():
    """x_name 已是视线宾语（「目光凝望杨旭」中的杨旭）→ 不注入新 gaze."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 刺客目光凝望杨旭，举刀逼近",
            char_layout=[
                {"name": "杨旭", "facing": "面向刺客"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.facing_to_gaze import FacingToGazeConstraint

    repairs = FacingToGazeConstraint().apply(ctx)

    assert repairs == []
    assert "目光凝望杨旭，目光凝望刺客" not in script["shots"][0]["prompt"]


def test_skips_when_subject_has_own_gaze():
    """x_name 后已紧跟视线动词（该角色已有 gaze）→ 不重复注入."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 杨旭目光凝望听香",
            char_layout=[
                {"name": "杨旭", "facing": "面向张十三"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.facing_to_gaze import FacingToGazeConstraint

    repairs = FacingToGazeConstraint().apply(ctx)

    assert repairs == []
    assert script["shots"][0]["prompt"].count("目光凝望") == 1


def test_processes_hook():
    """hook 分镜也应被处理."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script(
        shots=[make_shot(index=1, prompt="[中景] 场景")],
        hook={
            "prompt": "[特写] 听香撑伞",
            "char_layout": [{"name": "听香", "facing": "面向杨旭"}],
        },
    )
    ctx = make_ctx(script)
    from step01_script_gen.constraints.facing_to_gaze import FacingToGazeConstraint

    FacingToGazeConstraint().apply(ctx)

    assert "目光凝望杨旭" in script["hook"]["prompt"]


def test_constraint_attributes():
    """验证 Constraint 类属性."""
    from step01_script_gen.constraints.facing_to_gaze import FacingToGazeConstraint
    from step01_script_gen.constraints.types import Severity

    c = FacingToGazeConstraint()
    assert c.name == "facing_to_gaze"
    assert c.severity == Severity.WARN
    assert "prompt" in c.fields


# ============================================================================
# v6.50（08-28）：动作子句分策略——强动作动词子句插「身体朝向」非「目光凝望」
# ============================================================================

def test_action_clause_inserts_body_orientation():
    """「倒纵入水」动作子句 → 身体朝向（后空翻中「目光凝望」物理荒谬）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            motion_desc="刺客双腿并拢倒纵入水，身影半没入水面",
            char_layout=[{"name": "刺客", "position": "画右前景", "facing": "面向杨旭", "role": "主"}],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.facing_to_gaze import FacingToGazeConstraint

    FacingToGazeConstraint().apply(ctx)

    out = script["shots"][0]["motion_desc"]
    assert "身体朝向杨旭" in out
    assert "目光凝望" not in out


def test_static_clause_still_inserts_gaze():
    """静态子句（跪坐）→ 仍插「目光凝望」表达视线."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            motion_desc="听香跪坐船板，双手撑地，语无伦次",
            char_layout=[{"name": "听香", "position": "画左", "facing": "面向张十三", "role": "次"}],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.facing_to_gaze import FacingToGazeConstraint

    FacingToGazeConstraint().apply(ctx)

    assert "目光凝望张十三" in script["shots"][0]["motion_desc"]


def test_action_clause_insert_idempotent():
    """身体朝向注入后二次运行不重复追加（幂等标记覆盖两个 marker）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            motion_desc="刺客双腿并拢倒纵入水",
            char_layout=[{"name": "刺客", "position": "画右", "facing": "面向杨旭", "role": "主"}],
        ),
    ])
    from step01_script_gen.constraints.facing_to_gaze import FacingToGazeConstraint

    c = FacingToGazeConstraint()
    c.apply(make_ctx(script))
    once = script["shots"][0]["motion_desc"]
    repairs2 = c.apply(make_ctx(script))

    assert repairs2 == []
    assert script["shots"][0]["motion_desc"] == once
    assert once.count("身体朝向") == 1
