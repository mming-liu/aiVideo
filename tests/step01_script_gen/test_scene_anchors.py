"""test_scene_anchors.py — scene_anchors 模块单测（单一事实源）."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from step01_script_gen.scene_anchors import (
    BASE_POS_WORDS,
    BASE_FACING_WORDS,
    SCENE_ANCHORS,
    HORIZONTAL_SCENES,
    CONTAINING_SCENES,
    get_scene_type,
    scene_anchors_for,
    is_horizontal_scene,
    is_containing_scene,
    is_valid_position,
    is_valid_facing,
    has_lateral_depth,
)


# ============================================================================
# 1. 常量完整性
# ============================================================================

def test_base_pos_words_complete():
    """画X + 景深 8 个基础词必须全有."""
    expected = {"画左", "画右", "画中", "画上方", "画下方", "前景", "中景", "后景"}
    assert set(BASE_POS_WORDS) == expected


def test_base_facing_words_complete():
    """6 个朝向词."""
    expected = {"面向", "背对", "侧身", "正对", "面朝", "朝向"}
    assert set(BASE_FACING_WORDS) == expected


def test_scene_anchors_has_core_scenes():
    """8+ 个常用场景必须有 anchor."""
    must_have = {"船", "桥", "街道", "马车", "厅堂", "院落", "室内", "殿"}
    assert must_have.issubset(SCENE_ANCHORS.keys())


def test_horizontal_and_containing_disjoint():
    """横向和围合场景不能重叠（一个 location 只能是一种类型）."""
    overlap = HORIZONTAL_SCENES & CONTAINING_SCENES
    assert not overlap, f"横向/围合场景重叠: {overlap}"


# ============================================================================
# 2. get_scene_type —— location 字符串解析
# ============================================================================

def test_get_scene_type_boat_synonyms():
    """舟/舴艋/渔舟/画舫 等都识别为船场景."""
    for s in ("舴艋舟", "一叶扁舟", "渔舟唱晚", "画舫游船", "小舟"):
        assert get_scene_type(s) == "船", f"应识别为船: {s}"


def test_get_scene_type_street():
    """街/巷/路/道都识别为街道场景."""
    for s in ("长安大街", "青石小巷", "山路", "城门口大道"):
        assert get_scene_type(s) == "街道", f"应识别为街道: {s}"


def test_get_scene_type_indoor():
    """屋/室/房/阁/楼都识别为室内场景."""
    for s in ("密室", "客栈二楼", "绣楼", "暖阁", "内室"):
        assert get_scene_type(s) == "室内", f"应识别为室内: {s}"


def test_get_scene_type_yard():
    """院/舍/庄/府都识别为院落场景."""
    for s in ("贾府", "将军府", "田庄", "院落"):
        assert get_scene_type(s) == "院落", f"应识别为院落: {s}"


def test_get_scene_type_palace():
    """殿/宫殿/金殿都识别为殿场景."""
    for s in ("太和殿", "金殿", "宫殿深处"):
        assert get_scene_type(s) == "殿", f"应识别为殿: {s}"


def test_get_scene_type_unknown():
    """未知场景返回空字符串（走通用画X 判定）."""
    assert get_scene_type("") == ""
    assert get_scene_type("荒野") == ""
    assert get_scene_type("山巅") == "山道"  # 兜底匹配


# ============================================================================
# 3. is_valid_position —— 校验器用
# ============================================================================

def test_is_valid_position_base_words():
    """基础 8 词合法."""
    for p in ("画左", "画右", "画中", "前景", "中景", "后景", "画左前景"):
        assert is_valid_position(p, ""), f"应合法: {p}"


def test_is_valid_position_boat_anchors():
    """船场景下：船头/船中/船尾/船舷 都合法."""
    for p in ("船头", "船中", "船尾", "船舷", "船舷左", "前甲板"):
        assert is_valid_position(p, "舴艋舟"), f"船场景下应合法: {p}"


def test_is_valid_position_street_anchors():
    """街道场景：街口/巷尾 合法."""
    for p in ("街口", "街中", "路中", "巷口"):
        assert is_valid_position(p, "长安大街"), f"街道场景下应合法: {p}"


def test_is_valid_position_indoor_anchors():
    """室内场景：桌前/窗前 合法."""
    for p in ("桌前", "案前", "床前", "窗前"):
        assert is_valid_position(p, "密室"), f"室内场景下应合法: {p}"


def test_is_valid_position_wrong_anchors_in_other_scene():
    """跨场景词只要 pos 自带任一 scene anchor 就算合法（兜底）.

    严格按 location 查表是失效模式：location 写「云河镇照月湾」但 pos 是「船头」时
    会误报。这里接受任一 anchor 出现即可.
    """
    # 兜底：pos 出现「船头/船中/船尾」即使 location 写密室也算合法
    assert is_valid_position("船头", "密室"), "pos 自带船 anchor 兜底合法"
    # 真正非 anchor 词才非法（任何 SCENE_ANCHORS/BASE_POS_WORDS 都没收录）
    assert not is_valid_position("桌子旁边", "密室"), "桌子旁边不是任何 anchor"
    assert not is_valid_position("河边", "密室"), "河边不是 anchor（应写画左/前景等）"


def test_is_valid_position_empty():
    """空字符串视为合法（LLM 暂未填）."""
    assert is_valid_position("", "舴艋舟")
    assert is_valid_position("", "")


# ============================================================================
# 4. is_valid_facing —— 校验器用
# ============================================================================

def test_is_valid_facing_all_words():
    """6 个朝向词全合法."""
    for f in ("面向", "背对", "侧身", "正对", "面朝", "朝向", "面向画左", "背对镜头"):
        assert is_valid_facing(f), f"应合法: {f}"


def test_is_valid_facing_invalid():
    """「低头」「抬头」等不是朝向词."""
    for f in ("低头", "抬头", "怒视", "点头"):
        assert not is_valid_facing(f), f"应非法: {f}"


def test_is_valid_facing_empty():
    """空字符串视为合法."""
    assert is_valid_facing("")


# ============================================================================
# 5. has_lateral_depth —— 多人在画左/画右同侧无纵深判定
# ============================================================================

def test_has_lateral_depth_horizontal_with_anchors():
    """船场景：船头+船尾 算有纵深（即使全在画左/画右位置）."""
    # 注意：纯「船头+船尾」就够，不需要画X
    assert has_lateral_depth(["船头", "船尾"], "舴艋舟")
    assert has_lateral_depth(["船头", "船中", "船尾"], "舴艋舟")


def test_has_lateral_depth_horizontal_same_anchor():
    """船场景：两个都「船头」仍算无纵深."""
    assert not has_lateral_depth(["船头", "船头"], "舴艋舟")


def test_has_lateral_depth_containing_needs_explicit():
    """厅堂场景：必须显式前/中/后景."""
    # 「正厅 + 侧厅」不算纵深（都是具体位置，非景深词）
    assert not has_lateral_depth(["正厅", "侧厅"], "花厅")
    # 「正厅 + 前景」算有纵深
    assert has_lateral_depth(["正厅", "前景"], "花厅")


def test_has_lateral_depth_explicit_景深_anywhere():
    """任何场景：含前/中/后景就算有纵深."""
    for scene in ("", "舴艋舟", "花厅", "密室", "荒野"):
        assert has_lateral_depth(["画左", "前景"], scene), f"含前景的应算纵深: {scene}"


def test_has_lateral_depth_empty_or_insufficient():
    """空列表或只有 1 个 position 不算有纵深."""
    assert not has_lateral_depth([], "舴艋舟")
    assert not has_lateral_depth(["画左"], "舴艋舟")
    assert not has_lateral_depth(["画左", "画右"], "荒野")  # 横向拉开但荒野无 anchor


# ============================================================================
# 6. 场景类型辅助
# ============================================================================

def test_is_horizontal_scene():
    assert is_horizontal_scene("舴艋舟")
    assert is_horizontal_scene("长安大街")
    assert not is_horizontal_scene("花厅")
    assert not is_horizontal_scene("")


def test_is_containing_scene():
    assert is_containing_scene("花厅")
    assert is_containing_scene("密室")
    assert is_containing_scene("将军府")
    assert not is_containing_scene("舴艋舟")


def test_scene_anchors_for_unknown():
    """未知 location 返回空 tuple."""
    assert scene_anchors_for("") == ()
    assert scene_anchors_for("荒野") == ()


# ============================================================================
# 7. 幂等
# ============================================================================

def test_idempotent_pure_function():
    """调用一次 vs 调用两次结果应相同（is_valid_position 是纯函数）."""
    pos = "画左"
    loc = "舴艋舟"
    r1 = is_valid_position(pos, loc)
    r2 = is_valid_position(pos, loc)
    assert r1 == r2
