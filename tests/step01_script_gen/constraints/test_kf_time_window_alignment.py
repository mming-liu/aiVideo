"""E. KfTimeWindowAlignmentConstraint 单元测试."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_appends_kf_for_missing_segment():
    """motion_desc 显式分 2 段，kfs 只覆盖段 1 → 应补段 2 的 kf."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 刺客刺杨旭",
            ff_desc="完整小舟上，杨旭端坐",
            motion_desc="前2秒：刺客破水+举刀下刺；2-4秒：杨旭仰卧闷哼",
            duration_sec=4.0,
            keyframes=[
                {
                    "stage": "approach",
                    "t_sec": 1.0,  # 段 1 (0-2s) 覆盖
                    "desc": "刺客破水",
                },
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_time_window_alignment import (
        KfTimeWindowAlignmentConstraint,
    )

    repairs = KfTimeWindowAlignmentConstraint().apply(ctx)

    # 段 2 (2-4s) 应被派生补帧
    assert len(repairs) >= 1
    kfs = script["shots"][0]["keyframes"]
    # 至少 2 个 kf（原 1 + 补 1）
    assert len(kfs) >= 2
    # 补的 kf 物理时刻应在中点 (2+4)/2 = 3.0
    new_kf = kfs[-1]
    assert abs(new_kf["t_sec"] - 3.0) < 0.01


def test_no_repair_when_all_segments_covered():
    """全部段都被 kf 覆盖 → 0 改动（幂等）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="",
            ff_desc="",
            motion_desc="前2秒：破水；2-4秒：仰卧",
            duration_sec=4.0,
            keyframes=[
                {"stage": "approach", "t_sec": 1.0, "desc": "破水"},  # 段 1
                {"stage": "after", "t_sec": 3.0, "desc": "仰卧"},  # 段 2
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_time_window_alignment import (
        KfTimeWindowAlignmentConstraint,
    )

    repairs = KfTimeWindowAlignmentConstraint().apply(ctx)
    assert repairs == []


def test_no_motion_desc_no_repair():
    """motion_desc 为空 → 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 杨旭坐",
            motion_desc="",  # 空
            duration_sec=4.0,
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_time_window_alignment import (
        KfTimeWindowAlignmentConstraint,
    )

    repairs = KfTimeWindowAlignmentConstraint().apply(ctx)
    assert repairs == []


def test_no_duration_no_repair():
    """duration_sec 缺失且 timecode 无效 → 0 改动."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            prompt="[中景] 杨旭坐",
            motion_desc="前2秒：破水；2-4秒：仰卧",
            duration_sec=0,  # 无效
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_time_window_alignment import (
        KfTimeWindowAlignmentConstraint,
    )

    repairs = KfTimeWindowAlignmentConstraint().apply(ctx)
    assert repairs == []


def test_constraint_attributes():
    from step01_script_gen.constraints.kf_time_window_alignment import (
        KfTimeWindowAlignmentConstraint,
    )
    from step01_script_gen.constraints.types import Severity

    c = KfTimeWindowAlignmentConstraint()
    assert c.name == "kf_time_window_alignment"
    assert c.severity == Severity.WARN
    assert c.fields == ()  # 不扫文本字段
