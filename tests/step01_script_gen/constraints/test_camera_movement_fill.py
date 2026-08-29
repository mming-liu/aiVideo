"""J3. CameraMovementFillConstraint 单元测试.

覆盖：
  1. 修复：无运镜语言 → 按角色补运镜（打斗镜→快速跟拍）
  2. 修复：未命中角色 → 兜底「固定机位」
  3. 幂等：已含运镜/机位/静态语言 → 跳过
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_fills_combat_shot_with_quick_follow():
    """打斗/刺杀镜无运镜语言 → 补「快速跟拍」."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="刺客破水而出，匕首刺向杨旭心口",
            motion_desc="刺客跃上船舷，一刀刺杀",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.camera_movement_fill import CameraMovementFillConstraint

    repairs = CameraMovementFillConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert "跟拍" in script["shots"][0]["prompt"]


def test_fills_unmatched_shot_with_static():
    """未命中任何角色 registry → 兜底「固定机位」."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="夜色中水湾荷花，月光洒在水面",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.camera_movement_fill import CameraMovementFillConstraint

    repairs = CameraMovementFillConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert "固定机位" in script["shots"][0]["prompt"]


def test_skips_when_camera_language_present():
    """prompt 已含运镜词（推近）→ 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="镜头缓慢推近杨旭面部",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.camera_movement_fill import CameraMovementFillConstraint

    repairs = CameraMovementFillConstraint().apply(ctx)

    assert repairs == []


def test_is_idempotent():
    """补填后再跑 = 0 修复（已含运镜词）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="夜色中水湾荷花，月光洒在水面",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.camera_movement_fill import CameraMovementFillConstraint

    c = CameraMovementFillConstraint()
    r1 = c.apply(ctx)
    r2 = c.apply(ctx)

    assert len(r1) >= 1
    assert len(r2) == 0


def test_constraint_attributes():
    from step01_script_gen.constraints.camera_movement_fill import CameraMovementFillConstraint
    from step01_script_gen.constraints.types import Severity

    c = CameraMovementFillConstraint()
    assert c.name == "camera_movement_fill"
    assert c.severity == Severity.WARN
    assert "prompt" in c.fields
