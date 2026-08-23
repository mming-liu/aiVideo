"""J2. CameraVarietyConstraint 单元测试.

测试 3 类场景：
  1. 修复：全片 < 3 种运镜 → 按角色补差异化运镜
  2. 幂等：连跑 2 次 = 第 2 次 0 改动
  3. 隔离：全片 ≥ 3 种运镜 / 单镜已有 ≥ 2 种运镜 → 不加
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_adds_movement_to_establishing_shot():
    """远景建立镜（无运镜词） → 应追加 '缓慢推近'."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[远景] 小舟在湖面", main_subject=""),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.camera_variety import CameraVarietyConstraint

    repairs = CameraVarietyConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert "推近" in script["shots"][0]["prompt"]


def test_is_idempotent():
    """连跑 2 次 = 第 2 次 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[远景] 小舟在湖面"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.camera_variety import CameraVarietyConstraint

    c = CameraVarietyConstraint()
    r1 = c.apply(ctx)
    r2 = c.apply(ctx)

    assert len(r1) >= 1
    assert len(r2) == 0


def test_skips_when_full_movement_set():
    """全片 ≥ 3 种运镜 → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[远景] 推近 镜头1"),
        make_shot(index=2, prompt="[中景] 跟拍 镜头2"),
        make_shot(index=3, prompt="[特写] 横移 镜头3"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.camera_variety import CameraVarietyConstraint

    repairs = CameraVarietyConstraint().apply(ctx)

    # 已有 3 种运镜
    assert repairs == []


def test_skips_when_shot_has_2_movements():
    """单镜已有 ≥ 2 种运镜 → 不再叠加."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[远景] 推近 横移 镜头1",  # 已有 2 个运镜词
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.camera_variety import CameraVarietyConstraint

    repairs = CameraVarietyConstraint().apply(ctx)

    assert repairs == []


def test_adds_movement_to_combat_shot():
    """打斗镜（main_subject 含 '刺杀'）→ 应追加 '快速跟拍'."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 刺客举刀",
            main_subject="刺客刺杀",
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.camera_variety import CameraVarietyConstraint

    repairs = CameraVarietyConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert "跟拍" in script["shots"][0]["prompt"]


def test_constraint_attributes():
    """验证 Constraint 类属性正确."""
    from step01_script_gen.constraints.camera_variety import CameraVarietyConstraint
    from step01_script_gen.constraints.types import Severity

    c = CameraVarietyConstraint()
    assert c.name == "camera_variety"
    assert c.severity == Severity.WARN
    assert c.skip_hook is True
