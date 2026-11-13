# -*- coding: utf-8 -*-
"""S02：后处理不得编造无来源的内容（内容无关性）.

2026-11-13 计划 S02 的验收用例。四个已确认的自动补写/删除行为：

1. 船宽缺规格时补 `宽1.4米`、容量补「可容3人」——尺寸与载客量都没有来源；
2. 「窄身」被整段删除，不区分该字段说的是不是船（「窄身机甲」也会被删）；
3. 船体裁切短语（「船尾在画外」）被直接删除——裁切可能是输入里的合法构图；
4. 「潜伏水下」被追加固定露头高度（约 2cm）——替输入决定姿态。

修法：没有输入规格就不改写；形制词只在字段确实在说船时处理；裁切只报不改；
水下高度只在输入已给出时才补说明。
"""
from __future__ import annotations

from step01_script_gen.constraints.boat_crop import BoatCropConstraint
from step01_script_gen.constraints.boat_width import BoatWidthConstraint
from step01_script_gen.constraints.context import ConstraintContext
from step01_script_gen.constraints.v6_43_prompt_enforcement import (
    V643PromptEnforcementConstraint,
    _fix_vague_anchor,
)
from shared.scene_objects_bank import boat_width_text, build_boat_replacement

_BOAT_SPEC = {"小舟": {"category": "船", "length_m": 6, "width_m": 1.4}}


def _ctx(shots: list, scene_objects: dict | None = None) -> ConstraintContext:
    script = {"shots": shots, "characters": [],
              "scene_objects": scene_objects or {}}
    return ConstraintContext.from_script(script)


# ---------------------------------------------------------------------------
# 1. 没有规格就不写尺寸/载客量
# ---------------------------------------------------------------------------
def test_width_is_not_invented_without_a_spec():
    assert boat_width_text({"prompt": "一叶小舟"}, {}) == ""
    assert build_boat_replacement({"prompt": "一叶小舟"}, {}) == ""


def test_width_comes_from_the_input_spec_when_present():
    shot = {"prompt": "一叶小舟（长6米窄身无篷舴艋舟）", "location": "小舟"}
    assert boat_width_text(shot, _BOAT_SPEC) == "宽1.4米"

    ctx = _ctx([shot], _BOAT_SPEC)
    BoatWidthConstraint().apply(ctx)
    assert "长6米宽1.4米" in shot["prompt"]


def test_no_rewrite_when_the_shot_has_no_spec():
    shot = {"prompt": "一叶小舟（长6米窄身无篷舴艋舟）", "location": "小舟"}
    before = shot["prompt"]
    BoatWidthConstraint().apply(_ctx([shot]))
    assert shot["prompt"] == before, "没有输入规格时不得补宽度"


# ---------------------------------------------------------------------------
# 2. 「窄身」只在能证明属于船时处理
# ---------------------------------------------------------------------------
def test_narrow_body_of_a_non_boat_object_is_preserved():
    shot = {"prompt": "窄身机甲立于废墟", "location": "废墟"}
    before = shot["prompt"]
    BoatWidthConstraint().apply(_ctx([shot], _BOAT_SPEC))
    assert shot["prompt"] == before, "「窄身机甲」不是船，形制词不得被删"


def test_narrow_body_of_another_object_sharing_the_frame_is_preserved():
    """2026-11-13 复核反例：同框有船，也不得把别的实体的「窄身」删掉."""
    shot = {"prompt": "窄身机甲站在小舟旁。", "location": "小舟"}
    before = shot["prompt"]
    repairs = BoatWidthConstraint().apply(_ctx([shot], _BOAT_SPEC))
    assert shot["prompt"] == before, "「窄身机甲」与船同框仍属于机甲，不得删除"
    assert any(r.action == "warn" for r in repairs), "归属不明应记录待评阅项"


def test_narrow_body_of_a_boat_is_still_rewritten_with_a_spec():
    shot = {"prompt": "一叶小舟（长6米窄身无篷）", "location": "小舟"}
    BoatWidthConstraint().apply(_ctx([shot], _BOAT_SPEC))
    assert "窄身" not in shot["prompt"]
    assert "宽1.4米" in shot["prompt"]


# ---------------------------------------------------------------------------
# 3. 裁切只报不改
# ---------------------------------------------------------------------------
def test_crop_phrase_is_reported_not_deleted():
    shot = {"prompt": "[中景] 小舟漂在湖面，船身向画面右方延伸出画外"}
    before = shot["prompt"]
    repairs = BoatCropConstraint().apply(_ctx([shot]))
    assert shot["prompt"] == before, "合法裁切不得被删除"
    assert repairs and repairs[0].action == "warn"


def test_crop_phrase_absence_reports_nothing():
    shot = {"prompt": "[中景] 小舟完整漂在湖面"}
    assert BoatCropConstraint().apply(_ctx([shot])) == []


# ---------------------------------------------------------------------------
# 4. 水下姿态不编造高度
# ---------------------------------------------------------------------------
def test_underwater_pose_without_a_height_is_preserved():
    shot = {"ff_desc": "潜伏水下。", "prompt": ""}
    _fix_vague_anchor(shot)
    assert shot["ff_desc"] == "潜伏水下。"
    assert "约 2cm" not in shot["ff_desc"]


def test_underwater_pose_with_an_input_height_gets_the_note():
    shot = {"ff_desc": "潜伏水下，仅头顶约2cm露出。", "prompt": ""}
    _fix_vague_anchor(shot)
    assert "不是上半身出水" in shot["ff_desc"]


def test_underwater_pose_keeps_the_input_height_instead_of_forcing_2cm():
    """2026-11-13 复核反例：输入已给 5cm，不得再写「约 2cm」与之矛盾."""
    shot = {"ff_desc": "潜伏水下，仅头顶约5cm露出", "prompt": ""}
    _fix_vague_anchor(shot)
    assert "5cm" in shot["ff_desc"]
    assert "2cm" not in shot["ff_desc"]
    assert "不是上半身出水" in shot["ff_desc"]


def test_underwater_cm_value_of_another_entity_is_not_borrowed():
    """2026-11-13 复核反例：10cm 属于探针，不能当成露头高度."""
    shot = {"ff_desc": "潜水员潜伏水下，手持长10cm的探针", "prompt": ""}
    warns = []
    _fix_vague_anchor(shot, warnings=warns)
    assert shot["ff_desc"] == "潜水员潜伏水下，手持长10cm的探针"
    assert "露出水面" not in shot["ff_desc"]
    assert warns and warns[0].action == "warn", "归属不明应记录待评阅项"


# ---------------------------------------------------------------------------
# 5. 船只替换不再无条件生效
# ---------------------------------------------------------------------------
def test_boat_replacement_is_skipped_without_a_spec():
    shot = {"ff_desc": "一叶小舟静浮水面", "prompt": "一叶小舟静泊"}
    V643PromptEnforcementConstraint().apply(_ctx([shot]))
    assert "一叶小舟" in shot["ff_desc"]
    assert "敞口木舟" not in shot["ff_desc"]


def test_boat_replacement_preserves_the_input_name_and_form():
    """2026-11-13 复核反例：subtype=封闭式救生艇 不得被改写成「敞口木舟」."""
    spec = {"救生艇": {"category": "船", "subtype": "封闭式救生艇",
                       "length_m": 6, "width_m": 2}}
    text = build_boat_replacement(
        {"prompt": "小舟靠岸", "location": "救生艇"}, spec)
    assert text.startswith("封闭式救生艇")
    assert "长6米" in text and "宽2米" in text
    assert "敞口木舟" not in text
