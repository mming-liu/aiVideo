"""F2. KfBloodMonotonicConstraint 单元测试（v6.49, 08-28）.

跑批实测 6 处血状态回退（镜头 1/2/6/7/8）。
修法：prev 有血 + cur 无血 → 追加 INJURY_ANCHOR_TAIL。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_prev_blood_cur_no_blood_appends_tail():
    """prev 含血词 + cur 不含 → cur 追加 INJURY_ANCHOR_TAIL."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            duration_sec=6.0,
            keyframes=[
                {"stage": "climax", "t_sec": 2.5, "desc": "刀刺入杨旭心口，胸口洇开暗红"},
                {"stage": "after", "t_sec": 4.5, "desc": "杨旭仰卧船头，双手垂落"},  # 血丢了
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_blood_monotonic import KfBloodMonotonicConstraint

    repairs = KfBloodMonotonicConstraint().apply(ctx)

    assert len(repairs) == 1
    kf1 = script["shots"][0]["keyframes"][1]
    assert "伤处白袍心口一片暗红血迹洇开" in kf1["desc"]


def test_cur_already_has_blood_no_repair():
    """cur 已含血词 → 不重复追加."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            duration_sec=6.0,
            keyframes=[
                {"stage": "climax", "t_sec": 2.5, "desc": "刀刺入心口，胸口暗红"},
                {"stage": "after", "t_sec": 4.5, "desc": "杨旭仰卧，胸口血痕仍清晰可见"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_blood_monotonic import KfBloodMonotonicConstraint

    repairs = KfBloodMonotonicConstraint().apply(ctx)

    assert len(repairs) == 0


def test_no_blood_in_any_kf_no_repair():
    """全镜无血词 → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            duration_sec=6.0,
            keyframes=[
                {"stage": "pre", "t_sec": 0.5, "desc": "听香跪坐船头"},
                {"stage": "after", "t_sec": 4.5, "desc": "杨旭仰卧"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_blood_monotonic import KfBloodMonotonicConstraint

    repairs = KfBloodMonotonicConstraint().apply(ctx)

    assert len(repairs) == 0


def test_is_idempotent_no_double_append():
    """已含 INJURY_ANCHOR_TAIL → 第二次跑 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            duration_sec=6.0,
            keyframes=[
                {"stage": "climax", "t_sec": 2.5, "desc": "胸口洇开暗红"},
                {"stage": "after", "t_sec": 4.5, "desc": "仰卧。伤处白袍心口一片暗红血迹洇开，血痕清晰可见"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_blood_monotonic import KfBloodMonotonicConstraint

    c = KfBloodMonotonicConstraint()
    repairs1 = c.apply(ctx)
    repairs2 = c.apply(ctx)

    assert len(repairs1) == 0  # 幂等：已修则跳过
    assert len(repairs2) == 0


def test_blood_appears_in_middle_later_kfs_kept():
    """中段 kf 含血 + 后续 kf 无血 → 后续追加 tail."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            duration_sec=8.0,
            keyframes=[
                {"stage": "pre", "t_sec": 0.5, "desc": "刺客潜伏"},
                {"stage": "climax", "t_sec": 3.0, "desc": "刀刺入，胸口中刀"},
                {"stage": "react", "t_sec": 4.5, "desc": "杨旭仰卧"},  # 血丢了
                {"stage": "after", "t_sec": 6.5, "desc": "听香跪坐"},  # 血丢了
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_blood_monotonic import KfBloodMonotonicConstraint

    repairs = KfBloodMonotonicConstraint().apply(ctx)

    assert len(repairs) == 2  # react + after 各一处
    kfs = script["shots"][0]["keyframes"]
    assert "伤处白袍心口一片暗红血迹洇开" in kfs[2]["desc"]
    assert "伤处白袍心口一片暗红血迹洇开" in kfs[3]["desc"]


def test_hook_processed():
    """hook 分镜也参与血单调校验."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    hook = {
        "index": 0,
        "duration_sec": 4.0,
        "is_hook": True,
        "keyframes": [
            {"stage": "climax", "t_sec": 1.5, "desc": "刺客匕首已刺入杨旭心口，胸口暗红洇开"},
            {"stage": "after", "t_sec": 3.0, "desc": "杨旭仰卧"},  # 血丢了
        ],
    }
    script = make_script(shots=[make_shot(index=1, duration_sec=4.0, keyframes=[])], hook=hook)
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_blood_monotonic import KfBloodMonotonicConstraint

    repairs = KfBloodMonotonicConstraint().apply(ctx)

    assert len(repairs) == 1
    assert "伤处白袍心口一片暗红血迹洇开" in hook["keyframes"][1]["desc"]


def test_skips_single_kf_shot():
    """单 kf 镜头 → 跳过（无 prev/cur 对）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            duration_sec=4.0,
            keyframes=[
                {"stage": "climax", "t_sec": 2.0, "desc": "刀刺入，胸口暗红"},
            ],
        ),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.kf_blood_monotonic import KfBloodMonotonicConstraint

    repairs = KfBloodMonotonicConstraint().apply(ctx)

    assert len(repairs) == 0
