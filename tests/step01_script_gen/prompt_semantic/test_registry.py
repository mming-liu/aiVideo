"""test_registry.py — 数据注册表（PROP_REGISTRY/POSE_REGISTRY/get_surfaces）单测."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from step01_script_gen.prompt_semantic import (
    PROP_REGISTRY, PropSpec, get_prop_spec, is_known_prop,
    POSE_REGISTRY, classify_pose,
    get_surfaces,
    THREAT_KEYWORDS, WEAPON_KEYWORDS,
    is_threat_name, is_weapon_name,
)


# ============================================================================
# PROP_REGISTRY
# ============================================================================

def test_prop_registry_has_core_props():
    """核心 prop 都在册."""
    for p in ("酒壶", "钓杆", "油纸伞", "竹箫", "灯盏", "灯笼", "炭炉",
              "刺客匕首", "匕首", "步摇", "剑"):
        assert p in [name for spec in PROP_REGISTRY.values() for name in spec.names], \
            f"prop {p!r} 未登记"


def test_get_prop_spec_alias_lookup():
    """中文别名能查到 PropSpec."""
    spec = get_prop_spec("酒壶")
    assert spec is not None
    assert spec.default_owner == "杨旭"
    assert spec.can_float is True
    assert spec.can_be_dropped is True

    # 匕首 = 刺客匕首（同一 spec，多别名）
    spec1 = get_prop_spec("匕首")
    spec2 = get_prop_spec("刺客匕首")
    assert spec1 is spec2


def test_is_known_prop_true_false():
    assert is_known_prop("酒壶") is True
    assert is_known_prop("紫霞宝剑") is False  # 未登记
    assert is_known_prop("") is False


def test_sword_cannot_be_dropped():
    """武器类 prop can_be_dropped=False."""
    sword = get_prop_spec("剑")
    assert sword is not None
    assert sword.can_be_dropped is False
    dagger = get_prop_spec("刺客匕首")
    assert dagger.can_be_dropped is False


# ============================================================================
# POSE_REGISTRY
# ============================================================================

def test_classify_pose_reclining():
    assert classify_pose("杨旭仰卧船头") == "reclining"
    assert classify_pose("听香昏迷") == "reclining"
    assert classify_pose("装死") == "reclining"


def test_classify_pose_sitting_standing():
    assert classify_pose("盘坐") == "sitting"
    assert classify_pose("跪坐船梢") == "sitting"
    assert classify_pose("站立画右") == "standing"
    assert classify_pose("立于画左") == "standing"


def test_classify_pose_in_motion_priority():
    """in_motion 优先级最高（即使同时含 reclining 词）."""
    # "仰卧+破水" 应当判为 in_motion（破水是动作中）
    assert classify_pose("仰卧+破水") == "in_motion"


def test_classify_pose_unknown():
    assert classify_pose("走") == ""  # "走" 不在 POSE_REGISTRY
    assert classify_pose("") == ""


# ============================================================================
# get_surfaces（按 scene 派生承重面）
# ============================================================================

def test_get_surfaces_horizontal_scene():
    """船/桥/街道等横向场景：从 scene_anchors 派生."""
    surfaces = get_surfaces("舴艋舟")
    # 横向场景应包含船头/船中/船尾
    assert "船头" in surfaces
    assert "船中" in surfaces
    assert "船尾" in surfaces
    # 排除船舷（边缘不可承重）
    assert "船舷" not in surfaces
    # 兜底包含甲板
    assert "甲板" in surfaces


def test_get_surfaces_containing_scene():
    """厅堂/院落/室内 → 案上/桌上/地面."""
    surfaces = get_surfaces("花厅")
    assert "案上" in surfaces
    assert "地面" in surfaces


def test_get_surfaces_abstract():
    """无 scene → 通用承重面."""
    surfaces = get_surfaces("")
    assert "地面" in surfaces
    assert "甲板" in surfaces  # 兜底


# ============================================================================
# THREAT_KEYWORDS / WEAPON_KEYWORDS
# ============================================================================

def test_threat_name_detection():
    assert is_threat_name("刺客") is True
    assert is_threat_name("神秘人") is True
    assert is_threat_name("黑衣人") is True
    assert is_threat_name("杨旭") is False
    assert is_threat_name("听香") is False


def test_weapon_name_detection():
    assert is_weapon_name("刀尖") is True
    assert is_weapon_name("匕首") is True
    assert is_weapon_name("酒壶") is False
