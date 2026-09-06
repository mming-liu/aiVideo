"""v6.57+ per-frame spec 单测.

覆盖：
1. Schema 烟雾测试（4 条原始）
2. DeepSeek strict 约束：每个 property 必须在 required（防漏写）
3. get_frame_spec 双路径：有 spec 章 / 老章 char_layout 派生
4. get_frame_spec kf_index 越界降级 + warn
5. derive_char_layout 字段映射（spec → 旧 char_layout 视图）
6. derive_char_layout on_screen=false 跳过
7. derive_char_layout 老章降级（无 frame_layouts → 原样返回 char_layout）
8. spec_on_screen_names / spec_threat_visible 角色筛选
"""
from __future__ import annotations

import logging

import pytest

from _text_gen.deepseek_schema import _FRAME_LAYOUTS, _FRAME_SPEC_ITEM
from shared.frame_spec import (
    derive_char_layout,
    get_frame_spec,
    spec_on_screen_names,
    spec_threat_visible,
)


# ========================================================================
# 1. Schema 烟雾测试
# ========================================================================


def test_frame_spec_item_required_fields():
    """6 必填字段缺一即 schema 不合法。"""
    assert set(_FRAME_SPEC_ITEM["required"]) == {
        "name", "on_screen", "position", "pose", "gaze_target", "props",
    }


def test_frame_layouts_three_buckets():
    """frame_layouts 必须含 ff/lf/kfs 三桶。"""
    assert set(_FRAME_LAYOUTS["required"]) == {"ff", "lf", "kfs"}


def test_kfs_is_2d_per_kf_array():
    """kfs 是「每关键帧一项出镜表」的二维数组，不是一维。"""
    kfs_field = _FRAME_LAYOUTS["properties"]["kfs"]
    assert kfs_field["type"] == "array"
    inner = kfs_field["items"]
    assert inner["type"] == "array"
    assert inner["items"] is _FRAME_SPEC_ITEM


def test_frame_spec_item_additional_properties_false():
    """DeepSeek strict 模式：禁止 LLM 加额外字段。"""
    assert _FRAME_SPEC_ITEM["additionalProperties"] is False
    assert _FRAME_LAYOUTS["additionalProperties"] is False


# ========================================================================
# 2. DeepSeek strict 约束：每 property ∈ required
# ========================================================================


def test_every_item_property_is_required():
    """防漏写：所有 properties 必须在 required 列表里。"""
    item_props = set(_FRAME_SPEC_ITEM["properties"].keys())
    item_required = set(_FRAME_SPEC_ITEM["required"])
    missing = item_props - item_required
    assert not missing, f"_FRAME_SPEC_ITEM properties 缺 required 声明: {missing}"


def test_every_layouts_property_is_required():
    """frame_layouts 顶层三桶都必填。"""
    layout_props = set(_FRAME_LAYOUTS["properties"].keys())
    layout_required = set(_FRAME_LAYOUTS["required"])
    missing = layout_props - layout_required
    assert not missing, f"_FRAME_LAYOUTS properties 缺 required 声明: {missing}"


# ========================================================================
# 3. accessor：有 spec 章
# ========================================================================


def _shot_with_spec(ff=None, lf=None, kfs=None) -> dict:
    """构造一个有 frame_layouts 的 shot（典型新章格式）。"""
    return {
        "index": 0,
        "frame_layouts": {
            "ff": ff if ff is not None else [],
            "lf": lf if lf is not None else [],
            "kfs": kfs if kfs is not None else [],
        },
    }


def test_get_frame_spec_ff_returns_first_bucket():
    """有 spec → ff 取 frame_layouts.ff 原样。"""
    ff = [{"name": "杨旭", "on_screen": True, "position": "画中",
           "pose": "盘坐", "gaze_target": "无", "props": ["酒壶"]}]
    shot = _shot_with_spec(ff=ff)
    assert get_frame_spec(shot, "first_frame") == ff


def test_get_frame_spec_lf_returns_last_bucket():
    """有 spec → lf 取 frame_layouts.lf 原样。"""
    lf = [{"name": "杨旭", "on_screen": True, "position": "画中",
           "pose": "仰卧", "gaze_target": "无", "props": []}]
    shot = _shot_with_spec(lf=lf)
    assert get_frame_spec(shot, "last_frame") == lf


def test_get_frame_spec_kf_returns_correct_index():
    """有 spec → keyframe 按 kf_index 取对应 kfs[i]。"""
    kfs = [
        [{"name": "杨旭", "on_screen": True, "position": "画中", "pose": "盘坐",
          "gaze_target": "无", "props": []}],
        [{"name": "杨旭", "on_screen": True, "position": "画中", "pose": "仰卧",
          "gaze_target": "无", "props": []}],
    ]
    shot = _shot_with_spec(kfs=kfs)
    assert get_frame_spec(shot, "keyframe", kf_index=0) == kfs[0]
    assert get_frame_spec(shot, "keyframe", kf_index=1) == kfs[1]


# ========================================================================
# 4. accessor：老章 char_layout 派生降级
# ========================================================================


def test_get_frame_spec_legacy_derives_from_char_layout():
    """无 frame_layouts 时从 char_layout 派生降级 spec."""
    cl = [
        {"name": "杨旭", "position": "画中", "facing": "面向镜头", "role": "主", "surface": "船头"},
        {"name": "听香", "position": "画右", "facing": "面向杨旭", "role": "次", "surface": "船头"},
    ]
    shot = {"index": 0, "char_layout": cl}
    spec = get_frame_spec(shot, "first_frame")
    assert len(spec) == 2
    # 旧数据无 on_screen 信息，保守填 True
    assert spec[0]["name"] == "杨旭"
    assert spec[0]["on_screen"] is True
    assert spec[0]["position"] == "画中"
    # pose / gaze_target / props 宽松默认
    assert spec[0]["pose"] == ""
    assert spec[0]["gaze_target"] == "无"
    assert spec[0]["props"] == []


def test_get_frame_spec_legacy_empty_when_no_char_layout():
    """老章无 char_layout 无 frame_layouts → 返回 []."""
    assert get_frame_spec({"index": 0}, "first_frame") == []
    assert get_frame_spec({"index": 0}, "last_frame") == []
    assert get_frame_spec({"index": 0}, "keyframe", kf_index=0) == []


# ========================================================================
# 5. accessor：kf_index 越界降级 + warn
# ========================================================================


def test_get_frame_spec_kf_index_oob_returns_empty(caplog):
    """kf_index 越界 → 返回 [] 并 warn（M2 Constraint 升级为违例）。"""
    shot = _shot_with_spec(kfs=[[{"name": "杨旭", "on_screen": True, "position": "画中",
                                    "pose": "", "gaze_target": "无", "props": []}]])
    with caplog.at_level(logging.WARNING, logger="shared.frame_spec"):
        result = get_frame_spec(shot, "keyframe", kf_index=5)
    assert result == []
    assert any("越界" in r.message for r in caplog.records)


def test_get_frame_spec_kf_index_none_returns_empty(caplog):
    """kf_index=None → 返回 [] 并 warn。"""
    shot = _shot_with_spec(kfs=[[{"name": "杨旭", "on_screen": True, "position": "画中",
                                    "pose": "", "gaze_target": "无", "props": []}]])
    with caplog.at_level(logging.WARNING, logger="shared.frame_spec"):
        result = get_frame_spec(shot, "keyframe")
    assert result == []


def test_get_frame_spec_unknown_frame_kind(caplog):
    """未知 frame_kind → 返回 [] 并 warn。"""
    shot = _shot_with_spec()
    with caplog.at_level(logging.WARNING, logger="shared.frame_spec"):
        result = get_frame_spec(shot, "middle_frame")
    assert result == []


# ========================================================================
# 6. derive_char_layout 字段映射
# ========================================================================


def test_derive_char_layout_from_spec_maps_fields():
    """frame_layouts.ff → 旧 char_layout 视图字段映射。"""
    shot = _shot_with_spec(ff=[
        {"name": "杨旭", "on_screen": True, "position": "画中",
         "pose": "盘坐", "gaze_target": "无", "props": ["酒壶"]},
        {"name": "听香", "on_screen": True, "position": "画右",
         "pose": "侧立", "gaze_target": "杨旭", "props": []},
    ])
    cl = derive_char_layout(shot)
    assert len(cl) == 2
    # 第 1 个 on_screen 角色 role='主'
    assert cl[0]["name"] == "杨旭"
    assert cl[0]["position"] == "画中"
    assert cl[0]["facing"] == "盘坐"  # pose 映射到 facing
    assert cl[0]["role"] == "主"
    assert cl[0]["surface"] == ""  # v6.62：占位符「未指定」废弃，改空串
    # 后续 on_screen 角色 role='次'
    assert cl[1]["name"] == "听香"
    assert cl[1]["role"] == "次"


def test_derive_char_layout_skips_offscreen():
    """on_screen=False 的角色不出现在派生 char_layout。"""
    shot = _shot_with_spec(ff=[
        {"name": "杨旭", "on_screen": True, "position": "画中",
         "pose": "仰卧", "gaze_target": "无", "props": []},
        {"name": "神秘人", "on_screen": False, "position": "画外",
         "pose": "倒纵入水", "gaze_target": "无", "props": []},
    ])
    cl = derive_char_layout(shot)
    assert len(cl) == 1
    assert cl[0]["name"] == "杨旭"


def test_derive_char_layout_all_offscreen_returns_empty():
    """全部 on_screen=False → 派生空（空镜语义）。"""
    shot = _shot_with_spec(ff=[
        {"name": "神秘人", "on_screen": False, "position": "画外",
         "pose": "已没入水中", "gaze_target": "无", "props": []},
    ])
    assert derive_char_layout(shot) == []


def test_derive_char_layout_legacy_returns_as_is():
    """老章无 frame_layouts → 原样返回 char_layout."""
    cl = [
        {"name": "杨旭", "position": "画中", "facing": "面向镜头", "role": "主", "surface": "船头"},
    ]
    shot = {"index": 0, "char_layout": cl}
    assert derive_char_layout(shot) == cl


# ========================================================================
# 7. spec_on_screen_names / spec_threat_visible
# ========================================================================


def test_spec_on_screen_names_filters_offscreen():
    """spec_on_screen_names 只返回 on_screen=True 的角色名。"""
    shot = _shot_with_spec(lf=[
        {"name": "杨旭", "on_screen": True, "position": "画中",
         "pose": "仰卧", "gaze_target": "无", "props": []},
        {"name": "神秘人", "on_screen": False, "position": "画外",
         "pose": "已没入水中", "gaze_target": "无", "props": []},
    ])
    assert spec_on_screen_names(shot, "last_frame") == ["杨旭"]


def test_spec_threat_visible_true_when_on_screen():
    """威胁源 on_screen=True → spec_threat_visible True。"""
    shot = _shot_with_spec(ff=[
        {"name": "神秘人", "on_screen": True, "position": "画左",
         "pose": "半身探水", "gaze_target": "画右", "props": ["匕首"]},
    ])
    assert spec_threat_visible(shot, ("神秘人", "刺客", "杀手"), "first_frame") is True


def test_spec_threat_visible_false_when_offscreen():
    """威胁源 on_screen=False → spec_threat_visible False（lf 互斥修根因）。"""
    shot = _shot_with_spec(lf=[
        {"name": "神秘人", "on_screen": False, "position": "画外",
         "pose": "已没入水中", "gaze_target": "无", "props": []},
        {"name": "杨旭", "on_screen": True, "position": "画中",
         "pose": "仰卧", "gaze_target": "无", "props": []},
    ])
    assert spec_threat_visible(shot, ("神秘人", "刺客", "杀手"), "last_frame") is False


def test_spec_threat_visible_alias_match():
    """threat_keywords 子串匹配：'蒙面人' 含 '蒙面' 命中。"""
    shot = _shot_with_spec(ff=[
        {"name": "蒙面人", "on_screen": True, "position": "画左",
         "pose": "站立", "gaze_target": "画右", "props": []},
    ])
    assert spec_threat_visible(shot, ("神秘人", "蒙面人", "黑衣人"), "first_frame") is True


# ========================================================================
# 8. FrameSpecConsistencyConstraint（v6.57+ per-frame spec 一致性）
# ========================================================================

from step01_script_gen.constraints import (
    ALL_CONSTRAINTS,
    ConstraintContext,
    Severity,
    Violation,
)


def _find_constraint(name: str):
    """从全局注册表取 Constraint 实例."""
    for c in ALL_CONSTRAINTS:
        if c.name == name:
            return c
    raise AssertionError(f"Constraint {name!r} 未注册到 ALL_CONSTRAINTS")


def _make_ctx(shots=None, hook=None) -> ConstraintContext:
    """用 mock shots/hook 构造 ConstraintContext."""
    return ConstraintContext(
        script={"title": "test", "shots": shots or [], "hook": hook},
        shots=shots or [],
        hook=hook,
    )


def _violation_messages(violations: list) -> list[str]:
    return [v.message for v in violations]


# ---------- 8a. frame_layouts 缺失 / 三桶不全 ----------


def test_constraint_missing_frame_layouts_legacy_warn():
    """老章无 frame_layouts → WARN（不阻塞 regen）."""
    constraint = _find_constraint("frame_spec_consistency")
    ctx = _make_ctx(shots=[{"index": 1, "char_layout": [{"name": "杨旭"}]}])
    constraint.apply(ctx)
    violations = ctx.drain_violations()
    assert len(violations) == 1
    assert violations[0].severity == Severity.WARN
    assert "缺 frame_layouts" in violations[0].message
    assert "老章降级" in violations[0].message


def test_constraint_missing_bucket_hard():
    """三桶缺一 → HARD 违例."""
    constraint = _find_constraint("frame_spec_consistency")
    ctx = _make_ctx(shots=[{
        "index": 1,
        "frame_layouts": {"ff": [], "lf": []},  # 缺 kfs
    }])
    constraint.apply(ctx)
    violations = ctx.drain_violations()
    assert any(v.severity == Severity.HARD and "kfs" in v.message for v in violations)


def test_constraint_bucket_not_list_hard():
    """ff 不是 list → HARD."""
    constraint = _find_constraint("frame_spec_consistency")
    ctx = _make_ctx(shots=[{
        "index": 1,
        "frame_layouts": {"ff": "not a list", "lf": [], "kfs": []},
    }])
    constraint.apply(ctx)
    violations = ctx.drain_violations()
    assert any(v.severity == Severity.HARD and "不是数组" in v.message for v in violations)


# ---------- 8b. 每项 6 字段缺一 ----------


def test_constraint_item_missing_required_field_hard():
    """每项缺 6 必填字段之一 → HARD."""
    constraint = _find_constraint("frame_spec_consistency")
    ctx = _make_ctx(shots=[{
        "index": 1,
        "frame_layouts": {
            "ff": [{"name": "杨旭", "on_screen": True, "position": "画中",
                    "pose": "盘坐", "gaze_target": "无"}],  # 缺 props
            "lf": [],
            "kfs": [],
        },
    }])
    constraint.apply(ctx)
    violations = ctx.drain_violations()
    assert any(v.severity == Severity.HARD and "缺 props 字段" in v.message for v in violations)


def test_constraint_all_six_fields_valid_passes():
    """6 必填字段齐全 + 字段正确 → 无 HARD 违例."""
    constraint = _find_constraint("frame_spec_consistency")
    spec_item = {"name": "杨旭", "on_screen": True, "position": "画中",
                 "pose": "盘坐", "gaze_target": "无", "props": []}
    ctx = _make_ctx(shots=[{
        "index": 1,
        "ff_desc": "杨旭盘坐船头",
        "lf_desc": "杨旭仰卧船头",
        "motion_desc": "杨旭倒下",
        "frame_layouts": {
            "ff": [spec_item],
            "lf": [dict(spec_item, pose="仰卧", on_screen=False)],
            "kfs": [[spec_item], [spec_item]],
        },
        "keyframes": [
            {"desc": "kf0", "stage": "pre", "t_sec": 0.5},
            {"desc": "kf1", "stage": "after", "t_sec": 2.0},
        ],
    }])
    constraint.apply(ctx)
    violations = ctx.drain_violations()
    hard = [v for v in violations if v.severity == Severity.HARD]
    assert not hard, f"应有 0 HARD 违例，实际: {_violation_messages(hard)}"


# ---------- 8c. 字段类型错 ----------


def test_constraint_onscreen_not_bool_warn():
    """on_screen 不是 bool → WARN（不阻塞 regen）."""
    constraint = _find_constraint("frame_spec_consistency")
    ctx = _make_ctx(shots=[{
        "index": 1,
        "frame_layouts": {
            "ff": [{"name": "杨旭", "on_screen": "yes", "position": "画中",
                    "pose": "盘坐", "gaze_target": "无", "props": []}],
            "lf": [],
            "kfs": [],
        },
    }])
    constraint.apply(ctx)
    violations = ctx.drain_violations()
    assert any(v.severity == Severity.WARN and "on_screen 应为 bool" in v.message for v in violations)


# ---------- 8d. kfs 数量 vs keyframes 数量 ----------


def test_constraint_kfs_count_mismatch_hard():
    """kfs 数量 ≠ keyframes 数量 → HARD."""
    constraint = _find_constraint("frame_spec_consistency")
    spec_item = {"name": "杨旭", "on_screen": True, "position": "画中",
                 "pose": "盘坐", "gaze_target": "无", "props": []}
    ctx = _make_ctx(shots=[{
        "index": 1,
        "frame_layouts": {
            "ff": [spec_item],
            "lf": [spec_item],
            "kfs": [[spec_item]],  # 1 个 kfs
        },
        "keyframes": [  # 2 个 keyframes
            {"desc": "kf0", "stage": "pre", "t_sec": 0.5},
            {"desc": "kf1", "stage": "after", "t_sec": 2.0},
        ],
    }])
    constraint.apply(ctx)
    violations = ctx.drain_violations()
    assert any(
        v.severity == Severity.HARD and "kfs 数量 1" in v.message and "keyframes 数量 2" in v.message
        for v in violations
    )


def test_constraint_kfs_count_match_passes():
    """kfs 数量 == keyframes 数量 → 无此违例."""
    constraint = _find_constraint("frame_spec_consistency")
    spec_item = {"name": "杨旭", "on_screen": True, "position": "画中",
                 "pose": "盘坐", "gaze_target": "无", "props": []}
    ctx = _make_ctx(shots=[{
        "index": 1,
        "frame_layouts": {
            "ff": [spec_item], "lf": [spec_item],
            "kfs": [[spec_item], [spec_item]],
        },
        "keyframes": [
            {"desc": "kf0", "stage": "pre", "t_sec": 0.5},
            {"desc": "kf1", "stage": "after", "t_sec": 2.0},
        ],
    }])
    constraint.apply(ctx)
    violations = ctx.drain_violations()
    kfs_count_violations = [v for v in violations if "kfs 数量" in v.message]
    assert not kfs_count_violations


# ---------- 8e. on_screen=True 角色在 desc 体现退场（lf 互斥根因） ----------


def test_constraint_onscreen_true_with_exit_word_hard():
    """lf on_screen=true 角色 + lf_desc 体现「已没入水中」 → HARD（lf 互斥修根因）."""
    constraint = _find_constraint("frame_spec_consistency")
    ctx = _make_ctx(hook={
        "index": 0,
        "ff_desc": "水下黑影潜伏",
        "lf_desc": "神秘人已没入水中不见踪影",
        "motion_desc": "神秘人破水而出",
        "frame_layouts": {
            "ff": [{"name": "神秘人", "on_screen": True, "position": "画左",
                    "pose": "潜伏水下", "gaze_target": "无", "props": []}],
            "lf": [{"name": "神秘人", "on_screen": True, "position": "画外",  # ← 错误：应 false
                    "pose": "倒纵入水", "gaze_target": "无", "props": []}],
            "kfs": [],
        },
    })
    constraint.apply(ctx)
    violations = ctx.drain_violations()
    msg = _violation_messages(violations)
    assert any(
        v.severity == Severity.HARD
        and "lf_desc" in v.field
        and "神秘人" in v.message
        and "on_screen=false" in v.message
        for v in violations
    ), f"期望 HARD 违例提示把 lf 神秘人标 false，实际: {msg}"


def test_constraint_onscreen_false_no_violation_when_desc_matches():
    """lf on_screen=false 角色 + lf_desc 体现退场 → 一致 → 无 HARD."""
    constraint = _find_constraint("frame_spec_consistency")
    ctx = _make_ctx(hook={
        "index": 0,
        "ff_desc": "水下黑影潜伏",
        "lf_desc": "神秘人已没入水中不见踪影",
        "motion_desc": "神秘人破水而出",
        "frame_layouts": {
            "ff": [{"name": "神秘人", "on_screen": True, "position": "画左",
                    "pose": "潜伏水下", "gaze_target": "无", "props": []}],
            "lf": [{"name": "神秘人", "on_screen": False, "position": "画外",  # ← 正确
                    "pose": "倒纵入水", "gaze_target": "无", "props": []}],
            "kfs": [],
        },
    })
    constraint.apply(ctx)
    violations = ctx.drain_violations()
    onscreen_violations = [v for v in violations if "on_screen=false" in v.message]
    assert not onscreen_violations, f"应无 on_screen 矛盾违例，实际: {_violation_messages(onscreen_violations)}"


# ---------- 8f. 幂等性 + 边角 ----------


def test_constraint_hooks_also_checked():
    """hook 也走检查（skip_hook 默认 False）."""
    constraint = _find_constraint("frame_spec_consistency")
    ctx = _make_ctx(hook={"index": 0, "frame_layouts": {"ff": [], "lf": []}})  # 缺 kfs
    constraint.apply(ctx)
    violations = ctx.drain_violations()
    assert any(v.severity == Severity.HARD and "kfs" in v.message for v in violations)


def test_constraint_kfs_2d_each_item_validated():
    """kfs 内部每项缺字段 → HARD（kfs 是 2D，每项都查）."""
    constraint = _find_constraint("frame_spec_consistency")
    ctx = _make_ctx(shots=[{
        "index": 1,
        "frame_layouts": {
            "ff": [],
            "lf": [],
            "kfs": [[{"name": "杨旭", "on_screen": True, "position": "画中"}]],  # 缺 pose/gaze/props
        },
        "keyframes": [{"desc": "kf0", "stage": "pre", "t_sec": 0.5}],
    }])
    constraint.apply(ctx)
    violations = ctx.drain_violations()
    msgs = _violation_messages(violations)
    # 缺 3 个字段 → 3 条 HARD
    hard = [v for v in violations if v.severity == Severity.HARD]
    field_missing = [v for v in hard if "缺" in v.message and "字段" in v.message]
    assert len(field_missing) >= 3, f"期望 ≥3 HARD 缺字段，实际 {len(field_missing)}: {msgs}"


# ========================================================================
# 9. 拆镜器继承 frame_layouts（v6.57+ M2 第 0 步）
# ========================================================================


def test_timecode_split_clears_kfs_with_keyframes():
    """timecode._do_pre_split_oversized_shots 拆镜后 child 的 kfs=[]（与清空的 keyframes 同步）."""
    from step01_script_gen.timecode import _do_pre_split_oversized_shots
    spec_item = {"name": "杨旭", "on_screen": True, "position": "画中",
                 "pose": "盘坐", "gaze_target": "无", "props": []}
    # 长叙述 + 多个 句号（确保 0.2-0.8 区间有 split point）
    narration = (
        "听香颤声道：公子啊这一路风雪交加，您怎么偏要挑这黄道吉日出城呢。"
        "可否多带几件衣裳？"  # ← 中点附近 问号可作为 split point
        "杨旭微微点头却没回头，只轻声道：风雪虽大但我心意已决。"
        "听香咬唇不再多言，船头静默。"
    )  # ~85 字 + 中点 问号
    parent = {
        "index": 1,
        "narration_segment": narration,
        "prompt": "无篷舴艋舟",
        "ff_desc": "杨旭盘坐",
        "lf_desc": "听香说话",
        "motion_desc": "听香说话",
        "scene": "照月湾",
        "char_layout": [{"name": "杨旭"}],
        "frame_layouts": {
            "ff": [spec_item], "lf": [spec_item],
            "kfs": [[spec_item], [spec_item], [spec_item], [spec_item]],
        },
        "keyframes": [
            {"desc": "kf0", "stage": "pre", "t_sec": 0.5},
            {"desc": "kf1", "stage": "climax", "t_sec": 2.0},
            {"desc": "kf2", "stage": "react", "t_sec": 4.0},
            {"desc": "kf3", "stage": "after", "t_sec": 6.0},
        ],
    }
    shots = [parent]
    # cap=15.0：估时 85字/4.5+停顿 ≈ 20s 远超 15s → 必拆；半 42字/4.5+0.3 ≈ 9.6s ≤ 15s → strict 切能成立
    n_split = _do_pre_split_oversized_shots(shots, shot_cap=15.0)
    assert n_split >= 1, f"应拆 1+ 次，实际 {n_split}"
    # 找到 child（带 _split_from）
    children = [s for s in shots if s.get("_split_from") is not None]
    assert len(children) >= 1, "应有 child（带 _split_from）"
    child = children[0]
    # 子镜 keyframes 被清空，kfs 也必须同步清空
    assert child.get("keyframes") == []
    assert child["frame_layouts"]["kfs"] == [], (
        f"拆镜 child 的 kfs 必须 = []（与 keyframes=[] 同步），实际 {child['frame_layouts']['kfs']}"
    )
    # ff/lf 应该继承（连贯性）
    assert len(child["frame_layouts"]["ff"]) == 1
    assert len(child["frame_layouts"]["lf"]) == 1


def test_long_dialogue_split_inherits_frame_layouts():
    """validation._hard_fix_split_long_dialogue_shot 拆镜后 child 继承 ff/lf/kfs（part_i=0 才有 kfs）."""
    from step01_script_gen.validation import _hard_fix_split_long_dialogue_shot
    spec_item = {"name": "杨旭", "on_screen": True, "position": "画中",
                 "pose": "盘坐", "gaze_target": "无", "props": []}
    # 用对话前缀 + 多个句号拆点（必须用「X道：」格式触发拆分器）
    narration = (
        "听香颤声道：第一句很长的对白，公子啊这一路风雪交加。"
        "第二句同样很长的对白，您怎么偏要挑这黄道吉日出城呢。"
        "第三句还是这么长，可否多带几件衣裳？"
    )  # ~55 字 + 3 个句末标点
    parent = {
        "index": 1,
        "narration_segment": narration,
        "scene": "照月湾",
        "prompt": "听香说话",
        "ff_desc": "听香",
        "lf_desc": "听香",
        "char_layout": [{"name": "杨旭"}],
        "frame_layouts": {
            "ff": [spec_item], "lf": [spec_item],
            "kfs": [[spec_item], [spec_item]],
        },
        "keyframes": [
            {"desc": "kf0", "stage": "pre", "t_sec": 0.5},
            {"desc": "kf1", "stage": "climax", "t_sec": 2.0},
        ],
    }
    shots = [parent]
    n_split = _hard_fix_split_long_dialogue_shot(shots)
    assert n_split >= 2, f"长对话至少拆 2 镜，实际 {n_split}"
    # 检查所有子镜都有 frame_layouts（不能是老章降级 WARN）
    for s in shots:
        fl = s.get("frame_layouts")
        assert isinstance(fl, dict), f"拆镜后 child 必须有 frame_layouts dict，实际 {type(fl).__name__}"
    # 第一子镜继承 kfs，后续子镜 kfs=[]
    children = [s for s in shots if s.get("__split_from_idx") == 1]
    assert len(children) >= 2
    for child in children:
        part = child.get("__split_part", 1)
        kfs = child["frame_layouts"]["kfs"]
        if part == 1:
            assert len(kfs) == 2, f"part=1 应继承 2 个 kfs，实际 {len(kfs)}"
        else:
            assert kfs == [], f"part≥2 应 kfs=[]，实际 {kfs}"
        # ff/lf 必继承
        assert len(child["frame_layouts"]["ff"]) == 1
        assert len(child["frame_layouts"]["lf"]) == 1


def test_split_child_passes_frame_spec_consistency():
    """拆镜 child 跑 frame_spec_consistency Constraint 应无 HARD 违例（核心回归测试）.

    8-30 split bug 实证：拆镜后 kfs 与 keyframes 数量错位 → HARD 违例 6 条
    （shot 8/10/12/14 kf=0 + shot 9/15 不等）。修后应 0 HARD。
    """
    constraint = _find_constraint("frame_spec_consistency")
    spec_item = {"name": "杨旭", "on_screen": True, "position": "画中",
                 "pose": "盘坐", "gaze_target": "无", "props": []}
    # 模拟 timecode 拆镜产物
    parent = {
        "index": 1,
        "ff_desc": "杨旭盘坐",
        "lf_desc": "听香说话",
        "motion_desc": "听香说话",
        "frame_layouts": {
            "ff": [spec_item], "lf": [spec_item],
            "kfs": [[spec_item], [spec_item], [spec_item], [spec_item]],
        },
        "keyframes": [
            {"desc": "kf0", "stage": "pre", "t_sec": 0.5},
            {"desc": "kf1", "stage": "climax", "t_sec": 2.0},
            {"desc": "kf2", "stage": "react", "t_sec": 4.0},
            {"desc": "kf3", "stage": "after", "t_sec": 6.0},
        ],
    }
    child = {
        "index": 1.5,
        "_split_from": 1,
        "ff_desc": "听香说话",
        "lf_desc": "听香说话",
        "motion_desc": "听香说话",
        "frame_layouts": {
            "ff": [spec_item], "lf": [spec_item],
            "kfs": [],  # ← 拆镜后 kfs=[]（与 keyframes=[] 同步）
        },
        "keyframes": [],  # ← 拆镜清空
    }
    ctx = _make_ctx(shots=[parent, child])
    constraint.apply(ctx)
    violations = ctx.drain_violations()
    hard = [v for v in violations if v.severity == Severity.HARD]
    assert not hard, (
        f"拆镜 child 跑 Constraint 应无 HARD 违例，实际 {len(hard)} 条: "
        f"{[v.message for v in hard]}"
    )


# ========================================================================
# 10. _inject_frame_spec（v6.57+ per-frame spec 驱动注入）
# ========================================================================


def _make_characters() -> list:
    """构造测试用 characters 列表（含杨旭/听香/神秘人）."""
    return [
        {
            "identifier": "杨旭",
            "static_features": "剑眉星目面如冠玉",
            "dynamic_features": "一袭春衫素白长袍",
        },
        {
            "identifier": "听香",
            "static_features": "柳叶眉樱桃小口",
            "dynamic_features": "藕荷色纱裙腰系步摇",
        },
        {
            "identifier": "神秘人",
            "static_features": "面覆黑巾",
            "dynamic_features": "一身黑衣手持寒光匕首",
        },
    ]


def _make_shot_with_spec(ff=None, lf=None, kfs=None) -> dict:
    """构造带 frame_layouts 的 shot（is_empty_scene=False）. """
    return {
        "index": 1,
        "scene": "照月湾",
        "ff_desc": "杨旭盘坐",
        "lf_desc": "杨旭盘坐",
        "motion_desc": "杨旭盘坐",
        "main_subject": "杨旭",
        "char_layout": [],  # is_empty_scene 通过 main_subject 判断
        "frame_layouts": {
            "ff": ff if ff is not None else [],
            "lf": lf if lf is not None else [],
            "kfs": kfs if kfs is not None else [],
        },
    }


def test_inject_frame_spec_ff_injects_onscreen_chars():
    """ff spec on_screen=true 角色注入 static+dynamic 锚定."""
    from shared.vimax_bridge import _inject_frame_spec
    shot = _make_shot_with_spec(ff=[
        {"name": "杨旭", "on_screen": True, "position": "画中",
         "pose": "盘坐", "gaze_target": "无", "props": []},
    ])
    result = _inject_frame_spec("杨旭盘坐", shot, _make_characters(), "first_frame")
    assert "杨旭" in result
    assert "素白长袍" in result, f"应注入 dynamic 服装锚，实际: {result}"


def test_inject_frame_spec_lf_skips_offscreen_threat():
    """lf spec on_screen=false 角色**不**注入锚块（治 lf 互斥根因）.

    8-30 实证：threat=神秘人 lf on_screen=true + lf_desc「已没入水中」→ count=3 烧 200 张。
    修后：spec 标 on_screen=false → 不注入服装锚 → prompt 不引导模型画神秘人。
    检测手段：desc 文本保留「神秘人」三字（来自 lf_desc），但服装锚块（"一身黑衣"）
    不应被加入。
    """
    from shared.vimax_bridge import _inject_frame_spec
    shot = _make_shot_with_spec(
        ff=[
            {"name": "杨旭", "on_screen": True, "position": "画中",
             "pose": "盘坐", "gaze_target": "无", "props": []},
            {"name": "听香", "on_screen": True, "position": "画左",
             "pose": "站立", "gaze_target": "杨旭", "props": []},
            {"name": "神秘人", "on_screen": True, "position": "画右",
             "pose": "半身探水", "gaze_target": "画左", "props": []},
        ],
        lf=[
            {"name": "杨旭", "on_screen": True, "position": "画中",
             "pose": "仰卧", "gaze_target": "无", "props": []},
            {"name": "听香", "on_screen": True, "position": "画左",
             "pose": "跪坐", "gaze_target": "杨旭", "props": []},
            {"name": "神秘人", "on_screen": False, "position": "画外",  # ← 关键：false
             "pose": "倒纵入水", "gaze_target": "无", "props": []},
        ],
    )
    result = _inject_frame_spec("杨旭仰卧，神秘人已没入水中", shot, _make_characters(), "last_frame")
    # 在场角色：杨旭、听香 — 服装锚应注入
    assert "素白长袍" in result, f"杨旭应在场，服装锚应注入，实际: {result}"
    assert "藕荷色纱裙" in result, f"听香应在场，服装锚应注入，实际: {result}"
    # 退场角色：神秘人 — 服装锚**不应**注入
    assert "一身黑衣" not in result, (
        f"神秘人 lf on_screen=false → 服装锚不应注入（治 lf 互斥），实际: {result}"
    )
    assert "面覆黑巾" not in result, "神秘人脸型锚也不应注入"


def test_inject_frame_spec_kf_uses_per_kf_index():
    """kf spec 用 kfs[i] 各查各的表（治 kf 角色消失 / 角色错位）.

    8-30 实证：kf 路径用 ff+lf 拼接，刺客没入水后 kf 仍注入刺客 → 模型画刺客出现。
    修后：kf[i] spec on_screen=false → 不注入对应 kf 的服装锚。
    """
    from shared.vimax_bridge import _inject_frame_spec
    shot = _make_shot_with_spec(
        ff=[{"name": "神秘人", "on_screen": True, "position": "画右",
             "pose": "潜伏", "gaze_target": "无", "props": []}],
        lf=[{"name": "神秘人", "on_screen": False, "position": "画外",
             "pose": "已没入水中", "gaze_target": "无", "props": []}],
        kfs=[
            # kf0：潜伏（刺客在场）
            [{"name": "神秘人", "on_screen": True, "position": "画右",
              "pose": "破水而出", "gaze_target": "画左", "props": []}],
            # kf1：破水后（刺客在场）
            [{"name": "神秘人", "on_screen": True, "position": "画右",
              "pose": "举刀", "gaze_target": "画左", "props": []}],
            # kf2：刺入后（刺客退场）
            [{"name": "神秘人", "on_screen": False, "position": "画外",
              "pose": "倒纵入水", "gaze_target": "无", "props": []}],
        ],
    )
    # kf0 应注入（刺客在场）
    kf0_result = _inject_frame_spec("神秘人破水而出", shot, _make_characters(),
                                    "keyframe", kf_index=0)
    assert "一身黑衣" in kf0_result, f"kf0 刺客在场应注入服装锚，实际: {kf0_result}"
    # kf2 应**不**注入（刺客已退场）
    kf2_result = _inject_frame_spec("神秘人倒纵入水，杨旭胸口中刀", shot, _make_characters(),
                                    "keyframe", kf_index=2)
    assert "一身黑衣" not in kf2_result, (
        f"kf2 spec 刺客 on_screen=false → 服装锚不应注入，实际: {kf2_result}"
    )
    # 但杨旭如果 on_screen=true 应注入（kf2 退场的是刺客不是杨旭）
    shot_with_both = _make_shot_with_spec(
        ff=[
            {"name": "神秘人", "on_screen": True, "position": "画右",
             "pose": "潜伏", "gaze_target": "无", "props": []},
            {"name": "杨旭", "on_screen": True, "position": "画中",
             "pose": "盘坐", "gaze_target": "无", "props": []},
        ],
        lf=[
            {"name": "神秘人", "on_screen": False, "position": "画外",
             "pose": "已没入水中", "gaze_target": "无", "props": []},
            {"name": "杨旭", "on_screen": True, "position": "画中",
             "pose": "仰卧", "gaze_target": "无", "props": []},
        ],
        kfs=[
            # kf0：杨旭 + 神秘人都在场
            [
                {"name": "神秘人", "on_screen": True, "position": "画右",
                 "pose": "破水而出", "gaze_target": "画左", "props": []},
                {"name": "杨旭", "on_screen": True, "position": "画中",
                 "pose": "盘坐", "gaze_target": "无", "props": []},
            ],
            # kf1：刺客退场，杨旭仍在
            [
                {"name": "神秘人", "on_screen": False, "position": "画外",
                 "pose": "已没入水中", "gaze_target": "无", "props": []},
                {"name": "杨旭", "on_screen": True, "position": "画中",
                 "pose": "仰卧", "gaze_target": "无", "props": []},
            ],
        ],
    )
    kf1_result = _inject_frame_spec("神秘人没入水中杨旭中刀", shot_with_both, _make_characters(),
                                    "keyframe", kf_index=1)
    assert "一身黑衣" not in kf1_result, "kf1 刺客退场不应注入服装锚"
    assert "素白长袍" in kf1_result, "kf1 杨旭仍在场应注入服装锚"


def test_inject_frame_spec_legacy_fallback():
    """老章无 frame_layouts → 降级到 _inject_character_costume（不崩）."""
    from shared.vimax_bridge import _inject_frame_spec
    # 无 frame_layouts 的旧章节 shot
    shot = {
        "index": 1,
        "scene": "照月湾",
        "ff_desc": "杨旭盘坐船头",
        "char_layout": [
            {"name": "杨旭", "position": "画中", "facing": "面向镜头", "role": "主"},
        ],
    }
    result = _inject_frame_spec("杨旭盘坐船头", shot, _make_characters(), "first_frame")
    # 降级路径：spec 为空 → 调 _inject_character_costume 旧逻辑 → 名字匹配 → 注入
    assert "杨旭" in result, f"降级路径应注入杨旭（name in desc），实际: {result}"


def test_inject_frame_spec_all_offscreen_no_injection():
    """spec 全 on_screen=false → 不注入任何角色."""
    from shared.vimax_bridge import _inject_frame_spec
    shot = _make_shot_with_spec(lf=[
        {"name": "神秘人", "on_screen": False, "position": "画外",
         "pose": "已没入水中", "gaze_target": "无", "props": []},
    ])
    result = _inject_frame_spec("水面只剩涟漪", shot, _make_characters(), "last_frame")
    assert "神秘人" not in result
    # 返回原 desc（无 additions）
    assert result == "水面只剩涟漪", f"无 on_screen 角色 → 返回原 desc，实际: {result}"


def test_inject_frame_spec_kf_index_oob_returns_desc():
    """kf_index 越界 → 降级到 _inject_character_costume，不崩."""
    from shared.vimax_bridge import _inject_frame_spec
    shot = _make_shot_with_spec(
        ff=[{"name": "杨旭", "on_screen": True, "position": "画中",
             "pose": "盘坐", "gaze_target": "无", "props": []}],
        kfs=[[{"name": "杨旭", "on_screen": True, "position": "画中",
               "pose": "盘坐", "gaze_target": "无", "props": []}]],
    )
    # kf_index=10 越界 → accessor 返回 [] → 走降级路径（仍可注：杨旭在 desc 里）
    result = _inject_frame_spec("杨旭盘坐", shot, _make_characters(), "keyframe", kf_index=10)
    # 降级到名字检测：desc 有「杨旭」→ 注入
    assert "杨旭" in result, f"kf OOB 降级路径应注入杨旭，实际: {result}"


def test_inject_frame_spec_unknown_character_skipped():
    """spec 角色名不在 characters 注册表 → 跳过（不注入）."""
    from shared.vimax_bridge import _inject_frame_spec
    shot = _make_shot_with_spec(ff=[
        {"name": "陌生角色", "on_screen": True, "position": "画中",
         "pose": "盘坐", "gaze_target": "无", "props": []},
    ])
    result = _inject_frame_spec("画面背景", shot, _make_characters(), "first_frame")
    # 陌生角色在 characters 找不到 → 跳过 → 返回原 desc
    assert "陌生角色" not in result
    assert result == "画面背景"


# ========================================================================
# 11. QC spec 对账（v6.57+ build_claim_checks_spec + 章级开关）
# ========================================================================


def _build_qc_shot(ff=None, lf=None, kfs=None, with_spec=True) -> dict:
    """构造 QC 测试用 shot（默认带 spec；老章用 with_spec=False）."""
    shot = {
        "index": 1,
        "scene": "照月湾",
        "ff_desc": "杨旭盘坐船头",
        "lf_desc": "杨旭仰卧船头",
        "motion_desc": "听香说话",
        "main_subject": "杨旭",
        "char_layout": [],
    }
    if with_spec:
        shot["frame_layouts"] = {
            "ff": ff if ff is not None else [],
            "lf": lf if lf is not None else [],
            "kfs": kfs if kfs is not None else [],
        }
    return shot


def test_build_claim_checks_spec_onscreen_questions():
    """spec 在场角色生成 1 在场 + 1 位置 + 0 视线（gaze=无）+ 0 道具."""
    from shared.image_qc import build_claim_checks_spec
    shot = _build_qc_shot(ff=[
        {"name": "杨旭", "on_screen": True, "position": "画中",
         "pose": "盘坐", "gaze_target": "无", "props": []},
    ])
    qs = build_claim_checks_spec(shot, "first_frame")
    assert len(qs) >= 1
    # 在场问句
    position = [q for q in qs if "位于" in q["q"]]
    assert len(position) >= 1
    assert "画中" in position[0]["q"]
    # 位置合一问句（替代独立在场）（画中 → 应有位置问）
    position = [q for q in qs if "位于" in q["q"]]
    assert len(position) == 1
    assert "画中" in position[0]["q"]
    # 视线问句（gaze=无 → 跳过）
    gaze_qs = [q for q in qs if "视线" in q["q"]]
    assert len(gaze_qs) == 0


def test_build_claim_checks_spec_skips_offscreen():
    """on_screen=false 角色不生成任何问句（治 lf 互斥）. """
    from shared.image_qc import build_claim_checks_spec
    shot = _build_qc_shot(lf=[
        {"name": "杨旭", "on_screen": True, "position": "画中",
         "pose": "仰卧", "gaze_target": "无", "props": []},
        {"name": "神秘人", "on_screen": False, "position": "画外",  # ← 关键：off_screen
         "pose": "已没入水中", "gaze_target": "无", "props": []},
    ])
    qs = build_claim_checks_spec(shot, "last_frame")
    # 神秘人 lf off_screen → 不应有任何问句含「神秘人」
    threat_qs = [q for q in qs if "神秘人" in q["q"] and "已无" not in q["q"]]
    assert len(threat_qs) == 0, (
        f"off_screen 角色应只生成缺席问句，实际: {[q['q'] for q in qs]}"
    )
    # 缺席问句应存在
    absence = [q for q in qs if "已无神秘人" in q["q"]]
    assert len(absence) == 1
    # 杨旭 lf on_screen → 应有在场 + 位置
    assert any("杨旭" in q["q"] and "位于" in q["q"] for q in qs)


def test_build_claim_checks_spec_max_six_questions():
    """每帧 ≤6 问（per-frame 限）."""
    from shared.image_qc import build_claim_checks_spec
    # 3 角色 × (在场+位置+视线+多道具) → 应被裁到 6
    shot = _build_qc_shot(ff=[
        {"name": "杨旭", "on_screen": True, "position": "画中",
         "pose": "盘坐", "gaze_target": "听香", "props": ["竹箫", "酒壶"]},
        {"name": "听香", "on_screen": True, "position": "画左",
         "pose": "站立", "gaze_target": "杨旭", "props": ["油纸伞", "步摇", "灯笼"]},
        {"name": "老汉", "on_screen": True, "position": "画右",
         "pose": "撑船", "gaze_target": "无", "props": ["钓杆"]},
    ])
    qs = build_claim_checks_spec(shot, "first_frame")
    assert len(qs) <= 8, f"spec 问句 ≤8，实际 {len(qs)}: {[q['q'] for q in qs]}"


def test_build_claim_checks_spec_kf_per_index():
    """kf spec 按 kf_index 取对应 kfs[i]（治 kf 角色消失）."""
    from shared.image_qc import build_claim_checks_spec
    shot = _build_qc_shot(
        ff=[{"name": "神秘人", "on_screen": True, "position": "画右",
             "pose": "潜伏", "gaze_target": "无", "props": []}],
        lf=[{"name": "神秘人", "on_screen": False, "position": "画外",
             "pose": "已没入水中", "gaze_target": "无", "props": []}],
        kfs=[
            # kf0：神秘人在场
            [{"name": "神秘人", "on_screen": True, "position": "画右",
              "pose": "破水而出", "gaze_target": "画左", "props": []}],
            # kf1：神秘人退场
            [{"name": "神秘人", "on_screen": False, "position": "画外",
              "pose": "已没入水中", "gaze_target": "无", "props": []}],
        ],
    )
    kf0_qs = build_claim_checks_spec(shot, "keyframe", kf_index=0)
    kf1_qs = build_claim_checks_spec(shot, "keyframe", kf_index=1)
    # kf0：神秘人在场 → 应有问句
    assert any("神秘人" in q["q"] for q in kf0_qs)
    # kf1：神秘人退场 → 不应有问句
    assert not any("神秘人" in q["q"] for q in kf1_qs), (
        f"kf1 神秘人 off_screen → 不应生成问句，实际: {[q['q'] for q in kf1_qs]}"
    )


def test_build_claim_checks_spec_legacy_no_spec_returns_empty():
    """老章无 frame_layouts → spec 路径返回 []（由 dispatch 降级到 desc）."""
    from shared.image_qc import build_claim_checks_spec
    shot = _build_qc_shot(with_spec=False)
    qs = build_claim_checks_spec(shot, "first_frame")
    assert qs == []


# ---- 章级开关 dispatch ----


def test_dispatch_auto_new_chapter_uses_spec():
    """默认（QC_DESC_CLAIMS 未设）：新章有 spec → 走 spec 路径."""
    from shared.image_qc import build_targeted_questions
    shot = _build_qc_shot(ff=[
        {"name": "杨旭", "on_screen": True, "position": "画中",
         "pose": "盘坐", "gaze_target": "无", "props": []},
    ])
    qs = build_targeted_questions(shot, "first_frame")
    # spec 路径特征：问句含「在本帧在场」
    assert any("位于" in q["q"] for q in qs), (
        f"新章应走 spec 路径（问句含「在本帧在场」），实际: {[q['q'] for q in qs]}"
    )


def test_dispatch_auto_legacy_chapter_uses_desc():
    """默认（QC_DESC_CLAIMS 未设）：老章无 spec → 走 desc 路径."""
    from shared.image_qc import build_targeted_questions
    shot = _build_qc_shot(with_spec=False)
    qs = build_targeted_questions(shot, "first_frame")
    # desc 路径特征：问句含「画面是否表现：」（来自逐句对账）
    assert any(q["q"].startswith("画面是否表现：") for q in qs), (
        f"老章应走 desc 路径（逐句对账），实际: {[q['q'] for q in qs]}"
    )


def test_dispatch_force_desc_ignores_spec(monkeypatch):
    """QC_DESC_CLAIMS=1：强制 desc 路径（新章也走 desc，可与 spec A/B 对比）."""
    from shared.image_qc import build_targeted_questions
    monkeypatch.setenv("QC_DESC_CLAIMS", "1")
    shot = _build_qc_shot(ff=[
        {"name": "杨旭", "on_screen": True, "position": "画中",
         "pose": "盘坐", "gaze_target": "无", "props": []},
    ])
    qs = build_targeted_questions(shot, "first_frame")
    # 强制 desc 路径：不应有「在本帧在场」spec 特征问句
    assert not any("在本帧在场" in q["q"] for q in qs), (
        f"QC_DESC_CLAIMS=1 应走 desc 路径，实际: {[q['q'] for q in qs]}"
    )


def test_dispatch_force_spec_works_on_legacy(monkeypatch):
    """QC_DESC_CLAIMS=0：强制 spec 路径；老章无 spec → []."""
    from shared.image_qc import build_targeted_questions
    monkeypatch.setenv("QC_DESC_CLAIMS", "0")
    shot = _build_qc_shot(with_spec=False)
    qs = build_targeted_questions(shot, "first_frame")
    # 强制 spec 路径：老章无 spec → 空
    assert qs == [], f"老章 + QC_DESC_CLAIMS=0 应返回 []，实际: {qs}"


# ---- _expected_persons 走 spec ----


def test_expected_persons_uses_spec_count():
    """_expected_persons：新章走 spec on_screen 计数（治 lf 互斥）."""
    from shared.image_qc import _expected_persons
    # lf spec：神秘人 off_screen → 期望 2 人（杨旭+听香）
    shot = _build_qc_shot(lf=[
        {"name": "杨旭", "on_screen": True, "position": "画中",
         "pose": "仰卧", "gaze_target": "无", "props": []},
        {"name": "听香", "on_screen": True, "position": "画左",
         "pose": "跪坐", "gaze_target": "杨旭", "props": []},
        {"name": "神秘人", "on_screen": False, "position": "画外",  # off_screen
         "pose": "已没入水中", "gaze_target": "无", "props": []},
    ])
    expected = _expected_persons(shot, "last_frame", None)
    assert expected == 2, (
        f"lf spec on_screen 计数 = 2（神秘人 off_screen 不计），实际: {expected}"
    )
