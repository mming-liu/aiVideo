"""W16 FrameLayoutConsistency 单元测试.

测试 4 类场景：
  1. 修复：ff_desc 写「坐」+ frame_layouts.ff.pose 写「仰卧」→ 改写 frame_layouts.ff.pose
  2. 幂等：已修复（pose 与 desc 同分类）→ 跳过
  3. 隔离：desc 含「前一刻/对比」对比措辞 → 不触发
  4. 通用性：新小说（主角=林平之）→ 自动跟随（不写死杨旭）
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_fixes_frame_layout_pose_to_match_ff_desc():
    """ff_desc 写「坐」+ frame_layouts.ff.pose 写「仰卧」→ 改写 frame_layouts.ff.pose."""
    from step01_script_gen.validation import _hard_fix_frame_layout_consistency
    hook = {
        "ff_desc": "杨旭赤脚坐于船舷侧，唇边带笑倦眼微阖，望向满天星斗。",
        "lf_desc": "杨旭仰倒于船板，胸口白袍一道暗红洇痕。",
        "motion_desc": "神秘人自船一侧水下破水跃出。",
        "frame_layouts": {
            "ff": [
                {"name": "杨旭", "on_screen": True, "position": "画右前景",
                 "pose": "袒腹仰卧，左手握钓杆垂于水面", "gaze_target": "画上方", "props": []},
                {"name": "听香", "on_screen": True, "position": "画左前景",
                 "pose": "侧坐船板深俯身", "gaze_target": "画下方", "props": []},
            ],
            "lf": [
                {"name": "杨旭", "on_screen": True, "position": "画右前景",
                 "pose": "仰倒船板，胸口洇痕扩大", "gaze_target": "画上", "props": []},
            ],
        },
    }

    n = _hard_fix_frame_layout_consistency([], hook)
    assert n >= 1, f"应至少改 1 项，实际 {n}"
    ff_yangxu = hook["frame_layouts"]["ff"][0]
    assert "仰卧" not in ff_yangxu["pose"], f"pose 应改写去掉仰卧: {ff_yangxu['pose']}"
    # 至少包含一个 POSE_REGISTRY['sitting'] 词
    from step01_script_gen.prompt_semantic.registry import POSE_REGISTRY
    assert any(w in ff_yangxu["pose"] for w in POSE_REGISTRY["sitting"]), \
        f"应替换为 sitting 类 pose: {ff_yangxu['pose']}"


def test_idempotent_after_fix():
    """已修复（pose 与 desc 同分类）后再次调用 → 0 改动（不累积）."""
    from step01_script_gen.validation import _hard_fix_frame_layout_consistency
    hook = {
        "ff_desc": "杨旭盘膝坐于船头，唇边带笑。",
        "lf_desc": "",
        "motion_desc": "",
        "frame_layouts": {
            "ff": [
                {"name": "杨旭", "on_screen": True, "position": "画右前景",
                 "pose": "盘膝坐于船舷", "gaze_target": "画上方", "props": []},
            ],
        },
    }

    n1 = _hard_fix_frame_layout_consistency([], hook)
    assert n1 == 0, f"已对齐应跳过，实际改 {n1}"


def test_no_trigger_when_desc_has_compare_hint():
    """ff_desc 含「前一刻/对比」对比措辞 → 不触发（用户故意写 lf 状态用于对比）."""
    from step01_script_gen.validation import _hard_fix_frame_layout_consistency
    hook = {
        "ff_desc": "对比前一刻的平静，此刻杨旭仰卧船头胸口插刀。",
        "lf_desc": "",
        "motion_desc": "",
        "frame_layouts": {
            "ff": [
                {"name": "杨旭", "on_screen": True, "position": "画右前景",
                 "pose": "仰卧船头", "gaze_target": "画上方", "props": []},
            ],
        },
    }

    n = _hard_fix_frame_layout_consistency([], hook)
    assert n == 0, f"对比措辞不应触发，实际改 {n}"


def test_works_on_shots_not_just_hook():
    """普通分镜 shots 列表也走 W16（不只 hook）— frame_layouts pose 与 desc 矛盾时改写."""
    from step01_script_gen.validation import _hard_fix_frame_layout_consistency
    shots = [
        {
            "ff_desc": "林平之端坐于大厅正中。",
            "lf_desc": "",
            "motion_desc": "",
            "frame_layouts": {
                "ff": [
                    {"name": "林平之", "on_screen": True, "position": "画中",
                     "pose": "仰面倒地", "gaze_target": "无", "props": []},
                ],
            },
        }
    ]

    n = _hard_fix_frame_layout_consistency(shots, None)
    assert n == 1, f"应改 1 项，实际 {n}"
    assert "仰面倒地" not in shots[0]["frame_layouts"]["ff"][0]["pose"]
    assert "端坐" in shots[0]["frame_layouts"]["ff"][0]["pose"] or "坐" in shots[0]["frame_layouts"]["ff"][0]["pose"]


def test_handles_missing_frame_layouts():
    """老章降级：frame_layouts 缺字段 → 跳过不报错."""
    from step01_script_gen.validation import _hard_fix_frame_layout_consistency
    shots = [{"ff_desc": "杨旭盘坐船头。"}]  # 无 frame_layouts

    n = _hard_fix_frame_layout_consistency(shots, None)
    assert n == 0, f"老章应跳过，实际改 {n}"


def test_reclining_priority_over_sitting():
    """reclining + sitting 同存时优先 reclining（08-30 hook ff 回归：仰卧+跪坐 共存时不应被改成「跪坐」）."""
    from step01_script_gen.validation import _hard_fix_frame_layout_consistency
    # ff_desc 同时含"仰卧"（reclining）和"跪坐"（sitting）——必须选"仰卧"
    hook = {
        "ff_desc": "杨旭白袍袒腹仰卧船头，听香春衫披发跪坐其侧。",
        "lf_desc": "",
        "motion_desc": "",
        "frame_layouts": {
            "ff": [
                # 旧版本 bug：会被改成"跪坐"——必须保留/改成"仰卧"
                {"name": "杨旭", "on_screen": True, "position": "画中偏左",
                 "pose": "跪坐", "gaze_target": "画上", "props": []},
            ],
        },
    }

    n = _hard_fix_frame_layout_consistency([], hook)
    assert n == 1, f"应改 1 项（跪坐→仰卧），实际 {n}"
    assert "仰卧" in hook["frame_layouts"]["ff"][0]["pose"], \
        f"reclining 优先，应改为仰卧: {hook['frame_layouts']['ff'][0]['pose']}"
    assert "跪坐" not in hook["frame_layouts"]["ff"][0]["pose"]


def test_per_character_pose_localization():
    """ff_desc 多角色多姿态：frame_layouts.ff[杨旭] 不被「听香跪坐」覆盖（08-30 多角色错位回归）."""
    from step01_script_gen.validation import _hard_fix_frame_layout_consistency
    hook = {
        "ff_desc": "杨旭白袍袒腹仰卧船头，听香春衫披发跪坐其侧，刺客潜伏水下。",
        "lf_desc": "",
        "motion_desc": "",
        "frame_layouts": {
            "ff": [
                {"name": "杨旭", "on_screen": True, "position": "画中偏左",
                 "pose": "站", "gaze_target": "画上", "props": []},  # 错填成"站"
                {"name": "听香", "on_screen": True, "position": "画左",
                 "pose": "站", "gaze_target": "画右", "props": []},  # 错填成"站"
                {"name": "刺客", "on_screen": True, "position": "画右水下",
                 "pose": "站", "gaze_target": "画中", "props": []},  # 错填成"站" + "潜伏"不在词表
            ],
        },
    }

    n = _hard_fix_frame_layout_consistency([], hook)
    # 杨旭名附近含"仰卧" → 改；听香名附近含"跪坐" → 改；刺客名附近"潜伏"不在 POSE_REGISTRY → 跳过
    assert n == 2, f"应改 2 项（杨旭+听香），刺客无 pose 词应跳过，实际 {n}"
    assert "仰卧" in hook["frame_layouts"]["ff"][0]["pose"]  # 杨旭 → 仰卧
    assert "跪坐" in hook["frame_layouts"]["ff"][1]["pose"]   # 听香 → 跪坐
    assert "站" in hook["frame_layouts"]["ff"][2]["pose"]     # 刺客保持原值
