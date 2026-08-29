"""test_anchor_ambiguity.py — system 50a 锚点失真 4 类 hard_fix 单测."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from step01_script_gen.constraints.v6_43_prompt_enforcement import (
    V643PromptEnforcementConstraint,
    _fix_temporal_collapse, _fix_handle_ambiguity,
    _fix_vague_anchor, _fix_vague_direction,
)


def _ctx(shots, hook=None):
    class _Ctx:
        def __init__(self, shots, hook):
            self.shots = shots
            self.hook = hook
    return _Ctx(shots, hook)


def _run(shots, hook=None):
    return V643PromptEnforcementConstraint().apply(_ctx(shots, hook))


# Rule 6: 时序塌缩
def test_temporal_collapse_lying_plus_attack():
    shot = {
        "ff_desc": "杨旭仰卧船头。刺客破水而出，直刺杨旭心口，杨旭闷哼后仰，心口洇开暗红。",
        "prompt": "",
    }
    fixed = _fix_temporal_collapse(shot)
    assert fixed >= 1
    assert "破水而出" not in shot["ff_desc"]
    assert "心口洇" not in shot["ff_desc"]
    assert "仰卧船头" in shot["ff_desc"]


def test_temporal_collapse_hook_strict():
    shot = {
        "ff_desc": "杨旭仰卧船头。刺客破水而出直刺。",
        "index": 0,
        "is_hook": True,
        "prompt": "",
    }
    fixed = _fix_temporal_collapse(shot)
    assert fixed >= 1


def test_temporal_collapse_no_attack_kept():
    shot = {
        "ff_desc": "杨旭仰卧船头，荷塘碧叶环绕。",
        "prompt": "",
    }
    fixed = _fix_temporal_collapse(shot)
    assert fixed == 0
    assert "荷塘碧叶环绕" in shot["ff_desc"]


# Rule 7: 道具主谓歧义
def test_handle_ambiguity_left_hand_rod():
    shot = {"ff_desc": "杨旭仰卧船头，左手钓杆横陈", "prompt": ""}
    fixed = _fix_handle_ambiguity(shot)
    assert fixed >= 1
    assert "钓杆已脱手" in shot["ff_desc"]


def test_handle_ambiguity_no_posture_kept():
    shot = {"ff_desc": "杨旭盘膝坐船头，左手钓杆横陈", "prompt": ""}
    fixed = _fix_handle_ambiguity(shot)
    assert fixed == 0


def test_handle_ambiguity_multiple_props():
    shot = {"ff_desc": "杨旭仰卧船头，左手钓杆横陈，右手酒壶垂落", "prompt": ""}
    fixed = _fix_handle_ambiguity(shot)
    assert fixed >= 2
    assert "钓杆已脱手" in shot["ff_desc"]
    assert "酒壶已脱手" in shot["ff_desc"]


# Rule 8: 锚点位置约束
def test_vague_anchor_dagger_tip():
    shot = {"ff_desc": "潜伏水下，一点刀尖微露水面。", "prompt": ""}
    fixed = _fix_vague_anchor(shot)
    assert fixed >= 1
    assert "独立物体" in shot["ff_desc"]


def test_vague_anchor_only_eyes():
    shot = {"ff_desc": "刺客潜伏水下，仅露双眼。", "prompt": ""}
    fixed = _fix_vague_anchor(shot)
    assert fixed >= 1
    assert "不是上半身出水" in shot["ff_desc"]


def test_vague_anchor_lurking_with_distance():
    shot = {"ff_desc": "潜伏水下。", "prompt": ""}
    fixed = _fix_vague_anchor(shot)
    assert fixed >= 1
    assert "约 2cm" in shot["ff_desc"]


# Rule 9: 模糊方位词替换
def test_vague_direction_huishou_replaced():
    shot = {
        "ff_desc": "听香跪坐船梢，披发回首。",
        "char_layout": [
            {"name": "杨旭", "role": "主", "position": "船头"},
            {"name": "听香", "role": "次", "position": "船梢"},
        ],
        "prompt": "",
    }
    fixed = _fix_vague_direction(shot)
    assert fixed >= 1
    assert "侧头凝望" in shot["ff_desc"]
    assert "回首" not in shot["ff_desc"]


def test_vague_direction_taitou_for_lying():
    shot = {
        "ff_desc": "杨旭仰卧船头，抬头看天。",
        "char_layout": [{"name": "杨旭", "role": "主", "position": "船头"}],
        "prompt": "",
    }
    fixed = _fix_vague_direction(shot)
    assert fixed >= 1
    assert "仰面朝上双目紧闭" in shot["ff_desc"]


def test_vague_direction_no_match_kept():
    shot = {
        "ff_desc": "听香跪坐船梢，目光凝望杨旭。",
        "char_layout": [{"name": "杨旭", "role": "主", "position": "船头"}],
        "prompt": "",
    }
    fixed = _fix_vague_direction(shot)
    assert fixed == 0


# 集成
def test_apply_integrates_all_4_rules():
    shot = {
        "ff_desc": "杨旭仰卧船头，左手钓杆横陈，右手酒壶垂落。听香披发回首。刺客破水而出，一点刀尖。",
        "prompt": "刺客潜伏水下，仅露双眼。",
        "char_layout": [
            {"name": "杨旭", "role": "主", "position": "船头"},
            {"name": "听香", "role": "次", "position": "船梢"},
            {"name": "刺客", "role": "主", "position": "水面下"},
        ],
    }
    fixed = _run([shot])
    assert len(fixed) >= 4
    assert "仰卧船头" in shot["ff_desc"]
    assert "破水而出" not in shot["ff_desc"]
    assert "钓杆已脱手" in shot["ff_desc"]
    assert "酒壶已脱手" in shot["ff_desc"]
    assert "回首" not in shot["ff_desc"]
    assert "侧头凝望" in shot["ff_desc"]
    assert "约 2cm" in shot["prompt"]
    assert "不是上半身" in shot["prompt"]


def test_apply_idempotent():
    shot = {
        "ff_desc": "杨旭仰卧船头，左手钓杆横陈。",
        "char_layout": [{"name": "杨旭", "role": "主", "position": "船头"}],
        "prompt": "",
    }
    c = V643PromptEnforcementConstraint()
    c.apply(_ctx([shot]))
    ff1 = shot["ff_desc"]
    c.apply(_ctx([shot]))
    assert shot["ff_desc"] == ff1
