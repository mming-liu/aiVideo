"""F4. KfPoseAnchorConstraint 单元测试（v6.49, 08-28）.

08-28 chapter_001 跑批实测 4 处关键帧质量告警：
  - 3 个中间 kf desc 缺角色姿态+位置
  - 3 个中间 kf 威胁源未写朝向目标

修法：缺姿态+位置 → 追加「本帧各角色姿态位置见首帧静态定格」；
威胁源无朝向 → 追加「威胁源侧身朝目标，紧盯目标」。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_kf_no_pose_no_position_appends_anchor():
    """中间 kf 缺姿态+位置 → 追加本帧各角色锚."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            duration_sec=6.0,
            keyframes=[
                {"stage": "climax", "t_sec": 2.5, "desc": "刺客破水而出"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_pose_anchor import KfPoseAnchorConstraint

    repairs = KfPoseAnchorConstraint().apply(ctx)

    assert len(repairs) >= 1
    kf = script["shots"][0]["keyframes"][0]
    assert "本帧各角色" in kf["desc"]


def test_kf_with_pose_and_position_skipped():
    """中间 kf 已含姿态+位置 → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            duration_sec=6.0,
            keyframes=[
                {"stage": "climax", "t_sec": 2.5, "desc": "刺客仰卧船头，杨旭跪坐船尾"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_pose_anchor import KfPoseAnchorConstraint

    repairs = KfPoseAnchorConstraint().apply(ctx)

    assert len(repairs) == 0


def test_threat_no_orientation_appends_orientation():
    """威胁源 kf 缺朝向 + 缺姿态 → 追加威胁源侧身朝目标."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    # 用 ASCII-safe 测试：threat word 选「潜伏」+「半身探出」等动作态词
    # 缺姿态词「仰卧/跪坐/站」+ 缺位置词「船头/船尾」
    script = make_script([
        make_shot(
            index=1,
            duration_sec=6.0,
            keyframes=[
                {"stage": "pre", "t_sec": 1.0, "desc": "刺客半身探出，匕首高举"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_pose_anchor import KfPoseAnchorConstraint

    repairs = KfPoseAnchorConstraint().apply(ctx)

    assert len(repairs) >= 1
    kf = script["shots"][0]["keyframes"][0]
    assert "侧身朝目标" in kf["desc"]


def test_threat_with_orientation_skipped():
    """威胁源已含朝向词 → 不重复追加."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            duration_sec=6.0,
            keyframes=[
                {"stage": "pre", "t_sec": 1.0, "desc": "刺客潜伏水下，侧身朝杨旭，紧盯杨旭"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_pose_anchor import KfPoseAnchorConstraint

    repairs = KfPoseAnchorConstraint().apply(ctx)

    # 仍可能追加本帧各角色（无姿态词），但不重复加朝向
    kf = script["shots"][0]["keyframes"][0]
    # 侧身朝目标 只追加一次
    assert kf["desc"].count("侧身朝目标") <= 1


def test_idempotent_with_repair_marker():
    """已含本帧各角色锚 + 威胁源侧身朝目标 → 0 修复（幂等）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            duration_sec=6.0,
            keyframes=[
                {
                    "stage": "climax",
                    "t_sec": 2.5,
                    "desc": "刺客破水而出。（本帧各角色姿态位置见首帧静态定格）威胁源侧身朝目标，紧盯目标。",
                },
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_pose_anchor import KfPoseAnchorConstraint

    repairs = KfPoseAnchorConstraint().apply(ctx)

    assert len(repairs) == 0


def test_hook_processed():
    """hook 分镜也参与 kf 锚定."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    hook = {
        "index": 0,
        "duration_sec": 4.0,
        "is_hook": True,
        "keyframes": [
            {"stage": "climax", "t_sec": 1.5, "desc": "刺客破水"},
        ],
    }
    script = make_script(shots=[make_shot(index=1, duration_sec=4.0, keyframes=[])], hook=hook)
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_pose_anchor import KfPoseAnchorConstraint

    repairs = KfPoseAnchorConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert "本帧各角色" in hook["keyframes"][0]["desc"]


def test_no_threat_no_orientation_no_append():
    """无威胁源+无姿态/位置 → 只追加本帧各角色（不追加朝向）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            duration_sec=6.0,
            keyframes=[
                {"stage": "after", "t_sec": 4.5, "desc": "夜色深沉，灯火阑珊"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_pose_anchor import KfPoseAnchorConstraint

    repairs = KfPoseAnchorConstraint().apply(ctx)

    kf = script["shots"][0]["keyframes"][0]
    # 无威胁词 → 不应追加「威胁源侧身朝目标」
    assert "侧身朝目标" not in kf["desc"]
    # 缺姿态 → 应追加本帧各角色
    assert "本帧各角色" in kf["desc"]


def test_constraint_attributes():
    """验证 Constraint 类属性."""
    from step01_script_gen.constraints.kf_pose_anchor import KfPoseAnchorConstraint
    from step01_script_gen.constraints.types import Severity

    c = KfPoseAnchorConstraint()
    assert c.name == "kf_pose_anchor"
    assert c.severity == Severity.WARN
