"""test_threat_facing.py — Rule 5（threat_source facing 背对→侧身朝目标）单测."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from step01_script_gen.constraints.v6_43_prompt_enforcement import (
    V643PromptEnforcementConstraint,
    _fix_threat_facing,
    _is_threat_role,
)


def _ctx(shots):
    class _Ctx:
        def __init__(self, shots):
            self.shots = shots
            self.hook = None
    return _Ctx(shots)


def _run(shots):
    return V643PromptEnforcementConstraint().apply(_ctx(shots))


# ============================================================================
# 1. _is_threat_role —— 威胁源角色识别
# ============================================================================

def test_threat_by_role_field():
    assert _is_threat_role({"name": "刺客", "role": "威胁"})
    assert _is_threat_role({"name": "黑衣", "role": "threat"})


def test_threat_by_is_threat_field():
    assert _is_threat_role({"name": "路人甲", "is_threat": True})


def test_threat_by_name_keyword():
    assert _is_threat_role({"name": "刺客张三"})
    assert _is_threat_role({"name": "神秘人"})
    assert _is_threat_role({"name": "黑衣人"})
    assert _is_threat_role({"name": "蒙面剑客"})


def test_not_threat():
    assert not _is_threat_role({"name": "杨旭", "role": "主"})
    assert not _is_threat_role({"name": "听香", "role": "次"})
    assert not _is_threat_role({})


# ============================================================================
# 2. _fix_threat_facing —— 背对 → 侧身朝目标
# ============================================================================

def test_threat_back_to_camera_rewritten():
    shot = {
        "char_layout": [
            {"name": "杨旭", "facing": "面向画左"},
            {"name": "刺客", "facing": "背对镜头"},
        ]
    }
    fixed = _fix_threat_facing(shot)
    assert fixed == 1
    assert shot["char_layout"][1]["facing"] == "侧身朝目标"


def test_threat_back_to_target_rewritten():
    shot = {
        "char_layout": [
            {"name": "刺客", "facing": "背对目标"},
        ]
    }
    _fix_threat_facing(shot)
    assert shot["char_layout"][0]["facing"] == "侧身朝目标"


def test_threat_already_side_skipped():
    shot = {
        "char_layout": [
            {"name": "刺客", "facing": "侧身朝目标"},
        ]
    }
    fixed = _fix_threat_facing(shot)
    assert fixed == 0
    assert shot["char_layout"][0]["facing"] == "侧身朝目标"


def test_threat_already_face_skipped():
    shot = {
        "char_layout": [
            {"name": "刺客", "facing": "面向杨旭"},
        ]
    }
    fixed = _fix_threat_facing(shot)
    assert fixed == 0


def test_threat_empty_facing_skipped():
    shot = {
        "char_layout": [
            {"name": "刺客", "facing": ""},
        ]
    }
    fixed = _fix_threat_facing(shot)
    assert fixed == 0


def test_non_threat_not_touched():
    shot = {
        "char_layout": [
            {"name": "杨旭", "facing": "背对镜头"},
        ]
    }
    fixed = _fix_threat_facing(shot)
    assert fixed == 0
    assert shot["char_layout"][0]["facing"] == "背对镜头"  # 非威胁不动


def test_threat_idempotent():
    shot = {
        "char_layout": [
            {"name": "刺客", "facing": "背对镜头"},
        ]
    }
    _fix_threat_facing(shot)
    facing_after_first = shot["char_layout"][0]["facing"]
    _fix_threat_facing(shot)
    assert shot["char_layout"][0]["facing"] == facing_after_first


# ============================================================================
# 3. apply 集成：Rule 5 在主流程里跑
# ============================================================================

def test_apply_runs_rule5():
    shot = {
        "ff_desc": "夜色下，刺客潜伏",
        "char_layout": [
            {"name": "刺客", "facing": "背对镜头"},
        ]
    }
    fixed = _run([shot])
    assert len(fixed) >= 1
    assert shot["char_layout"][0]["facing"] == "侧身朝目标"


def test_apply_no_layout_no_crash():
    shot = {"ff_desc": "无 char_layout 也不崩"}
    fixed = _run([shot])
    assert len(fixed) == 0  # 不崩


def test_apply_mysteriyname_threat_fixed():
    """「神秘人」也是威胁源（关键词匹配）."""
    shot = {
        "ff_desc": "",
        "char_layout": [
            {"name": "神秘人", "facing": "背对画左"},
        ]
    }
    _run([shot])
    assert shot["char_layout"][0]["facing"] == "侧身朝目标"
