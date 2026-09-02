# -*- coding: utf-8 -*-
"""S6. FrameSpecConsistency 道具状态交叉校验单元测试.

对应 08-31 hook 实拍图发现：ff_desc 写"酒壶已脱手、钓杆垂于水面"，
但 frame_layouts.ff 杨旭 props=["酒壶","钓杆"] + pose="右手握酒壶，左手执钓杆"
——道具时序倒流，导致生图 kf0 杨旭坐起来举酒壶。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot


def _make_shot_with_spec(
    ff_desc: str,
    lf_desc: str = "",
    ff_spec: list | None = None,
    lf_spec: list | None = None,
    kfs_spec: list | None = None,
    keyframes: list | None = None,
) -> dict:
    """构造带 frame_layouts 的 shot，便于 props 一致性测试."""
    shot = make_shot(
        index=1,
        ff_desc=ff_desc,
        lf_desc=lf_desc,
        keyframes=keyframes or [],
    )
    shot["frame_layouts"] = {
        "ff": ff_spec or [],
        "lf": lf_spec or [],
        "kfs": kfs_spec or [],
    }
    return shot


# ── 1. ff 道具已脱手 + spec 仍含 → 违例 ──

def test_ff_dropped_prop_still_in_spec_flagged():
    """ff_desc 写"酒壶已脱手"，但 ff props=["酒壶"] → WARN 违例."""
    from step01_script_gen.constraints.frame_spec_consistency import (
        FrameSpecConsistencyConstraint,
    )
    from step01_script_gen.constraints.types import Severity

    shot = _make_shot_with_spec(
        ff_desc="杨旭仰卧船头，酒壶已脱手，钓杆垂于水面",
        ff_spec=[{
            "name": "杨旭", "on_screen": True,
            "position": "画左", "pose": "仰卧",
            "gaze_target": "无", "props": ["酒壶", "钓杆"],
        }],
    )
    ctx = make_ctx(make_script([shot]))
    FrameSpecConsistencyConstraint().apply(ctx)

    viols = [v for v in ctx.drain_violations()
             if v.constraint_name == "frame_spec_consistency"
             and "props" in (v.field or "")]
    assert len(viols) >= 2, f"应检出 2 个 props 违例（酒壶+钓杆），实测 {len(viols)}"
    # severity 是 WARN（进修复轮，不直接 regen）
    assert all(v.severity == Severity.WARN for v in viols)


# ── 2. ff 道具在手中 + spec 含 → 正常 ──

def test_ff_holding_prop_ok():
    """ff_desc 写"手握酒壶"，ff props=["酒壶"] → 不违例."""
    from step01_script_gen.constraints.frame_spec_consistency import (
        FrameSpecConsistencyConstraint,
    )

    shot = _make_shot_with_spec(
        ff_desc="杨旭盘坐船头，右手提着酒壶，神态悠然",
        ff_spec=[{
            "name": "杨旭", "on_screen": True,
            "position": "画中", "pose": "盘坐提壶",
            "gaze_target": "无", "props": ["酒壶"],
        }],
    )
    ctx = make_ctx(make_script([shot]))
    FrameSpecConsistencyConstraint().apply(ctx)

    viols = [v for v in ctx.drain_violations()
             if v.constraint_name == "frame_spec_consistency"
             and "props" in (v.field or "")]
    assert viols == [], f"手持道具不应报 props 违例，实测 {len(viols)} 条"


# ── 3. ff pose 含握动作 + desc 写脱手 → 违例 ──

def test_ff_pose_hold_vs_desc_dropped_flagged():
    """ff_desc 写"酒壶已脱手"，但 pose="右手握酒壶" → pose 违例."""
    from step01_script_gen.constraints.frame_spec_consistency import (
        FrameSpecConsistencyConstraint,
    )

    shot = _make_shot_with_spec(
        ff_desc="杨旭仰卧船头，酒壶已脱手",
        ff_spec=[{
            "name": "杨旭", "on_screen": True,
            "position": "画左", "pose": "仰卧，右手握酒壶",
            "gaze_target": "无", "props": [],  # props 空但 pose 还写握
        }],
    )
    ctx = make_ctx(make_script([shot]))
    FrameSpecConsistencyConstraint().apply(ctx)

    viols = [v for v in ctx.drain_violations()
             if v.constraint_name == "frame_spec_consistency"
             and ".pose" in (v.field or "")
             and "酒壶" in v.message]
    assert len(viols) >= 1, f"pose 含握酒壶但 desc 已脱手应违例，实测 {len(viols)} 条"


# ── 4. lf 道具倾倒在旁 + props 已空 → 正常 ──

def test_lf_dropped_prop_empty_props_ok():
    """lf_desc 写"酒壶倾倒在船板上"，lf props=[] → 不违例."""
    from step01_script_gen.constraints.frame_spec_consistency import (
        FrameSpecConsistencyConstraint,
    )

    shot = _make_shot_with_spec(
        ff_desc="",
        lf_desc="杨旭仰卧船头，酒壶倾倒在一旁，钓杆滑落到船板上",
        ff_spec=[],
        lf_spec=[{
            "name": "杨旭", "on_screen": True,
            "position": "画左", "pose": "仰卧不动",
            "gaze_target": "无", "props": [],
        }],
    )
    ctx = make_ctx(make_script([shot]))
    FrameSpecConsistencyConstraint().apply(ctx)

    viols = [v for v in ctx.drain_violations()
             if v.constraint_name == "frame_spec_consistency"
             and "props" in (v.field or "")]
    assert viols == [], f"倾倒在旁+props空不应报违例，实测 {len(viols)} 条"


# ── 5. kf 道具持有态 → 正常 ──

def test_kf_holding_prop_ok():
    """kf desc 写"右手提酒壶"，kf props=["酒壶"] → 正常."""
    from step01_script_gen.constraints.frame_spec_consistency import (
        FrameSpecConsistencyConstraint,
    )

    shot = _make_shot_with_spec(
        ff_desc="",
        keyframes=[{"desc": "杨旭仰卧船头，右手提着酒壶，左手握着钓杆",
                    "t_sec": 1.5, "stage": "pre"}],
        kfs_spec=[[{
            "name": "杨旭", "on_screen": True,
            "position": "画左", "pose": "仰卧提壶",
            "gaze_target": "无", "props": ["酒壶", "钓杆"],
        }]],
    )
    ctx = make_ctx(make_script([shot]))
    FrameSpecConsistencyConstraint().apply(ctx)

    viols = [v for v in ctx.drain_violations()
             if v.constraint_name == "frame_spec_consistency"
             and "props" in (v.field or "")]
    assert viols == [], f"kf 持有态不应报 props 违例，实测 {len(viols)} 条"


# ── 6. 多状态词覆盖：滑落/坠落/掉 ──

def test_various_drop_words_flagged():
    """各种离手状态词都应检出：滑落 / 坠落 / 掉在 / 放下 / 垂于."""
    from step01_script_gen.constraints.frame_spec_consistency import (
        FrameSpecConsistencyConstraint,
    )

    # (desc 片段, 道具名) — 道具名必须是 desc 里出现的真实词
    cases = [
        ("酒壶滑落水中", "酒壶"),
        ("酒杯坠落地板", "酒杯"),
        ("宝剑掉在地上", "宝剑"),
        ("钓杆垂于水面", "钓杆"),
        ("匕首脱手飞出", "匕首"),
    ]
    for desc_tail, prop_name in cases:
        shot = _make_shot_with_spec(
            ff_desc=f"杨旭站在船头，{desc_tail}",
            ff_spec=[{
                "name": "杨旭", "on_screen": True,
                "position": "画中", "pose": "站立",
                "gaze_target": "无",
                "props": [prop_name],
            }],
        )
        ctx = make_ctx(make_script([shot]))
        FrameSpecConsistencyConstraint().apply(ctx)

        viols = [v for v in ctx.drain_violations()
                 if v.constraint_name == "frame_spec_consistency"
                 and "props" in (v.field or "")]
        assert len(viols) >= 1, (
            f"desc='{desc_tail}' prop='{prop_name}' 应检出 props 违例，"
            f"实测 {len(viols)} 条"
        )


# ── 7. hook 也受检 ──

def test_hook_props_consistency_checked():
    """hook 的 frame_layouts 也受 props 一致性检查."""
    from step01_script_gen.constraints.frame_spec_consistency import (
        FrameSpecConsistencyConstraint,
    )

    hook_shot = _make_shot_with_spec(
        ff_desc="冷月之下，杨旭仰卧船头，酒壶已脱手，钓杆垂于水面",
        ff_spec=[{
            "name": "杨旭", "on_screen": True,
            "position": "画左", "pose": "仰卧握酒壶",
            "gaze_target": "无", "props": ["酒壶"],
        }],
    )
    script = make_script([], hook=hook_shot)
    ctx = make_ctx(script)
    FrameSpecConsistencyConstraint().apply(ctx)

    viols = [v for v in ctx.drain_violations()
             if v.constraint_name == "frame_spec_consistency"
             and "props" in (v.field or "")]
    assert len(viols) >= 1, f"hook 也应受 props 校验，实测 {len(viols)} 条"


# ── 8. 无 frame_layouts（老章）降级 → 不崩也不报 ──

def test_legacy_no_frame_layouts_no_crash():
    """老章无 frame_layouts → 不崩，也不报 props 违例."""
    from step01_script_gen.constraints.frame_spec_consistency import (
        FrameSpecConsistencyConstraint,
    )

    shot = make_shot(
        index=1,
        ff_desc="杨旭仰卧船头，酒壶已脱手",
        # 无 frame_layouts
    )
    ctx = make_ctx(make_script([shot]))
    # 不应抛异常
    FrameSpecConsistencyConstraint().apply(ctx)

    viols = [v for v in ctx.drain_violations()
             if "props" in (v.field or "")]
    # 老章只报"缺 frame_layouts"WARN，不应有 props 级违例
    assert not any("props" in str(v.field) for v in viols)
