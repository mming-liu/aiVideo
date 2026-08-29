"""F3. FfMotionOverlapConstraint 单元测试（v6.49, 08-28）.

08-28 跑批实测：镜 6 ff_desc「背对画面后仰翻入水中」与 motion_desc
「刺客倒纵入水消失不见…」动作完全重叠，模型会把动作做两遍。

修法：调 style_prefix.normalize_ff_desc 重写为前一刻。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_pre_action_in_ff_triggers_rewrite():
    """ff 含进行态动作（破水/翻入）→ 调 normalize_ff_desc 重写."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    ff_text = "刺客背对画面后仰翻入水中，胸口有血"
    shot = make_shot(
        index=1,
        duration_sec=6.0,
        ff_desc=ff_text,
        motion_desc="刺客倒纵入水消失不见，杨旭握杆左手垂落船舷",
    )
    script = make_script([shot])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.ff_motion_overlap import FfMotionOverlapConstraint

    repairs = FfMotionOverlapConstraint().apply(ctx)

    # 至少 1 处修复
    assert len(repairs) >= 1
    # 改写后 ff_desc 应不含「后仰翻入」进行态动词
    new_ff = script["shots"][0]["ff_desc"]
    assert new_ff != ff_text
    # 应含「前一刻」信号词（潜伏/即将/欲/未）
    assert any(m in new_ff for m in ("潜伏", "即将", "欲", "未"))


def test_static_ff_skipped():
    """ff_desc 已是静态定格（无动作进行态）→ 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    ff_text = "[中景] 听香跪坐船头，杨旭静坐未察，夕阳余晖照在水面"
    shot = make_shot(
        index=1,
        duration_sec=6.0,
        ff_desc=ff_text,
        motion_desc="刺客从水下跃出，匕首刺向杨旭心口",
    )
    script = make_script([shot])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.ff_motion_overlap import FfMotionOverlapConstraint

    repairs = FfMotionOverlapConstraint().apply(ctx)

    assert len(repairs) == 0
    assert script["shots"][0]["ff_desc"] == ff_text


def test_idempotent_with_pre_moment_marker():
    """ff_desc 已含前一刻信号词（即将/潜伏/欲）→ 0 修复（幂等）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    ff_text = "刺客潜伏水下、水面微澜；杨旭静坐；听香仰头欲呼"
    shot = make_shot(
        index=1,
        duration_sec=6.0,
        ff_desc=ff_text,
        motion_desc="刺客破水而出，匕首刺向杨旭",
    )
    script = make_script([shot])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.ff_motion_overlap import FfMotionOverlapConstraint

    repairs = FfMotionOverlapConstraint().apply(ctx)

    assert len(repairs) == 0
    assert script["shots"][0]["ff_desc"] == ff_text


def test_empty_ff_skipped():
    """ff_desc 为空 → 跳过（不是问题）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    shot = make_shot(
        index=1,
        duration_sec=6.0,
        ff_desc="",
        motion_desc="刺客破水",
    )
    script = make_script([shot])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.ff_motion_overlap import FfMotionOverlapConstraint

    repairs = FfMotionOverlapConstraint().apply(ctx)

    assert len(repairs) == 0


def test_hook_processed():
    """hook 分镜也参与 ff_desc 归一化."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    hook = {
        "index": 0,
        "duration_sec": 4.0,
        "is_hook": True,
        "ff_desc": "刺客背对画面后仰翻入水中",
        "motion_desc": "刺客倒纵入水，匕首刺向杨旭心口",
    }
    script = make_script(shots=[make_shot(index=1, duration_sec=4.0, ff_desc="")], hook=hook)
    ctx = make_ctx(script)
    from step01_script_gen.constraints.ff_motion_overlap import FfMotionOverlapConstraint

    repairs = FfMotionOverlapConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert "后仰翻入" not in hook["ff_desc"]


def test_constraint_attributes():
    """验证 Constraint 类属性."""
    from step01_script_gen.constraints.ff_motion_overlap import FfMotionOverlapConstraint
    from step01_script_gen.constraints.types import Severity

    c = FfMotionOverlapConstraint()
    assert c.name == "ff_motion_overlap"
    assert c.severity == Severity.WARN
