# -*- coding: utf-8 -*-
"""补帧后的**帧身份对齐**（S01–S04 补正，2026-11-15）：自足匿名夹具与入口验收.

## 复现的缺陷

1. run h 第 2 轮把新帧插在 `kfs[1]`，原 `kfs[1]` 逐字后移到 `kfs[2]`；守卫按
   同下标比较，把「插入 + 后移」判成「保留条目被改写」。
2. 补正复核发现：**布局恒等推导无法证明帧来源**。同一候选 `[A, B改, B]`，
   布局推导 `[0,2]` 放行，操作来源 `[0,1]` 却正确报出改写 —— 相同内容两种裁决。
   另外「缺映射 → 退回同下标比较」会让末尾新增的空布局帧被静默放过；
   `int()` 转换还会把 `[False, 2.9]` 当成 `[0,2]`。

## 本包契约

- 来源只能由**装配操作**生成，或由整表替换时**保留关键帧完整结构**唯一推导；
  布局内容一律不承担来源证明。
- 计数/下标必须 `type(x) is int`，映射带 `source` 与 `source_revision`。
- 缺映射、歧义、失配、跨 revision 一律**结构化拒收**，不退回旧比较。

## 夹具边界

两份匿名样本（实体数 / 帧数 / 镜号 / 布局分布不同），不依赖历史产物、不读
小说、不发起模型调用。历史现场由 `tools/replay_run_h_frame_identity.py` 专项回放。
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen.main import (                       # noqa: E402
    _apply_shot_replacements, _assemble_keyframe_additions)
from step01_script_gen.repair_effects import (              # noqa: E402
    build_layout_effects, frame_locator_integrity, frame_structure_state)
from step01_script_gen.repair_fields import (               # noqa: E402
    compose_frame_mappings, derive_frame_mapping_from_keyframes,
    frame_layout_structural_violations, identity_frame_mapping,
    validate_frame_mapping, verify_frame_identity_by_authorized_leaves)


# ---------------------------------------------------------------------------
# 样本 A：2 原帧 / 2 实体 / 镜号 5；样本 B：3 原帧 / 3 实体 / 镜号 9
# ---------------------------------------------------------------------------

def _entry(name, pose, gaze="画上", **extra):
    return dict(name=name, pose=pose, gaze_target=gaze, on_screen=True, **extra)


_SAMPLE_A = {
    "index": 5,
    "duration": 5.0, "duration_sec": 5.0,
    "motion_desc": "前2秒：甲静立；2-4秒：乙靠近；4-5秒：甲转身",
    "ff_desc": "起始画面", "lf_desc": "结束画面", "prompt": "中景",
    "subtitle": "", "narration_segment": "",
    "keyframes": [
        {"t_sec": 1.0, "desc": "甲静立", "stage": "pre"},
        {"t_sec": 3.0, "desc": "乙靠近，甲未及反应", "stage": "approach"}],
    "frame_layouts": {
        "ff": [], "lf": [],
        "kfs": [
            [_entry("甲", "静立")],
            [_entry("乙", "靠近", "画左"), _entry("甲", "未及反应", "画右")],
        ]},
}

_SAMPLE_B = {
    "index": 9,
    "duration": 6.0, "duration_sec": 6.0,
    "motion_desc": "前2秒：三人分立；2-4秒：中间人抬手；4-6秒：三人错身",
    "ff_desc": "起始", "lf_desc": "结束", "prompt": "全景",
    "subtitle": "", "narration_segment": "",
    "keyframes": [
        {"t_sec": 1.0, "desc": "三人分立", "stage": "pre"},
        {"t_sec": 3.0, "desc": "中间人抬手", "stage": "approach"},
        {"t_sec": 5.0, "desc": "三人错身", "stage": "exit"}],
    "frame_layouts": {
        "ff": [], "lf": [],
        "kfs": [
            [_entry("甲", "左立"), _entry("乙", "中立"), _entry("丙", "右立")],
            [_entry("乙", "抬手", "画左")],
            [_entry("甲", "错身"), _entry("丙", "错身")],
        ]},
}

# 整张列表 + 逐帧点名同时授权：前者让成对补帧通过合并层，后者触发守卫。
_ALLOWED = {
    "keyframes", "keyframes.*.t_sec", "keyframes.*.desc", "keyframes.*.stage",
    "frame_layouts.kfs", "frame_layouts.kfs[0]", "frame_layouts.kfs[1]",
    "frame_layouts.kfs[2]",
}
_REV = "rev-A"


def _script(sample):
    return {"shots": [copy.deepcopy(sample)], "characters": [], "hook": None}


def _sample_id(sample):
    return "shot_%d" % sample["index"]


def _apply(sample, additions, *, updates=None, allowed=None, expect=1):
    """走**实际**替换入口；返回 (before, merged, mappings, rejected)."""
    before = _script(sample)
    item = {"shot_id": str(sample["index"])}
    if updates:
        item["updates"] = updates
    if additions:
        item["keyframe_additions"] = additions
    mappings: dict = {}
    merged, count, rejected = _apply_shot_replacements(
        before, [item], {_sample_id(sample)},
        allowed_by_target={_sample_id(sample): set(allowed or _ALLOWED)},
        expected_targets={str(sample["index"])},
        frame_mappings_out=mappings)
    assert count == expect, (count, rejected)
    for mp in mappings.values():
        mp["source_revision"] = _REV
    return before, merged, mappings, rejected


def _guard(before, merged, sample, mappings=None, *, allowed=None, revision=_REV):
    tid = _sample_id(sample)
    return frame_layout_structural_violations(
        before, merged, {tid: set(allowed or _ALLOWED)}, {tid},
        frame_mappings=mappings, revision=revision)


def _new_frame(t, desc, entries):
    return {"frame": {"t_sec": t, "desc": desc, "stage": "approach"},
            "layout": copy.deepcopy(entries)}


# 与 run h 同形：中间新帧含两个**同名**人物条目，姿态/视线不同于原帧。
_INSERT_LAYOUT = [_entry("乙", "抬手", "画左"), _entry("甲", "静立", "画上")]


def _insert(t=2.0, desc="补入动作", entries=None):
    return _new_frame(t, desc,
                      entries if entries is not None else copy.deepcopy(_INSERT_LAYOUT))


def _assembly_mapping(old_to_new, added, before, after):
    return {"before_count": before, "after_count": after,
            "old_to_new": old_to_new, "added_indices": added,
            "source": "assembly", "source_revision": _REV}


# ---------------------------------------------------------------------------
# S01：三组确定性反例（当前实现必须全部**拒收**）
# ---------------------------------------------------------------------------

class TestCounterexamples:
    def test_missing_mapping_on_frame_count_change_is_rejected(self):
        """反例1：帧数增加 + 无映射 + 末尾新增空布局 ⇒ 结构化拒收（旧实现返回 []）."""
        before = _script(_SAMPLE_A)
        after = copy.deepcopy(before)
        after["shots"][0]["frame_layouts"]["kfs"].append([])
        v = frame_layout_structural_violations(
            before, after, {_sample_id(_SAMPLE_A): set(_ALLOWED)},
            {_sample_id(_SAMPLE_A)}, frame_mappings={}, revision=_REV)
        assert v and any("缺少可验证的来源映射" in x for x in v), v

    def test_layout_copy_cannot_prove_origin(self):
        """反例2：真改写 + 末尾复制旧布局，内容推导不得承担来源证明."""
        before = _script(_SAMPLE_A)
        base = copy.deepcopy(_SAMPLE_A["frame_layouts"]["kfs"])
        rewritten = [dict(base[1][0], pose="偷偷改写"), base[1][1]]
        after = copy.deepcopy(before)
        after["shots"][0]["frame_layouts"]["kfs"] = [base[0], rewritten, base[1]]
        tid = _sample_id(_SAMPLE_A)
        # 布局恒等推导已从生产路径移除；关键帧完整结构推导在本例也无从建立
        # （候选只有 2 个原关键帧，却出现 3 个布局帧）。
        assert derive_frame_mapping_from_keyframes(
            before["shots"][0]["keyframes"],
            after["shots"][0]["keyframes"]) is not None  # 关键帧未变 → 恒等
        # 伪造一个「按布局推导」的装配映射：守卫按来源复算关键帧结构时会被拒。
        forged = {"before_count": 2, "after_count": 3,
                  "old_to_new": [0, 2], "added_indices": [1],
                  "source": "keyframe_structure", "source_revision": _REV}
        v = frame_layout_structural_violations(
            before, after, {tid: set(_ALLOWED)}, {tid},
            frame_mappings={tid: forged}, revision=_REV)
        assert v and any("无法复现" in x for x in v), v
        # 换成真正可证明的操作来源 [0,1] ⇒ 原帧改写被正确指出。
        oper = {"before_count": 2, "after_count": 3,
                "old_to_new": [0, 1], "added_indices": [2],
                "source": "assembly", "source_revision": _REV}
        v2 = frame_layout_structural_violations(
            before, after, {tid: set(_ALLOWED)}, {tid},
            frame_mappings={tid: oper}, revision=_REV)
        assert any("字段被改写" in x for x in v2), v2

    @pytest.mark.parametrize("bad,keyword", [
        ({"before_count": 2, "after_count": 3, "old_to_new": [False, 2.9],
          "added_indices": [1], "source": "assembly",
          "source_revision": _REV}, "下标不是整数"),
        ({"before_count": 2, "after_count": 3, "old_to_new": ["0", "2"],
          "added_indices": [1], "source": "assembly",
          "source_revision": _REV}, "下标不是整数"),
        ({"before_count": True, "after_count": 3, "old_to_new": [0, 2],
          "added_indices": [1], "source": "assembly",
          "source_revision": _REV}, "计数不是整数"),
        ({"before_count": 2, "after_count": 3, "old_to_new": [0, 2],
          "added_indices": [1], "source": "assembly",
          "source_revision": "rev-WRONG"}, "revision"),
        ({"before_count": 2, "after_count": 3, "old_to_new": [0, 2],
          "added_indices": [1]}, "source"),
    ])
    def test_strict_mapping_contract(self, bad, keyword):
        """反例3：bool / float / str、跨 revision、缺来源标记一律明确拒收."""
        assert keyword in validate_frame_mapping(bad, 2, 3,
                                                 expected_revision=_REV)


# ---------------------------------------------------------------------------
# S02：装配期生成映射 / 来源链
# ---------------------------------------------------------------------------

class TestMappingGeneration:
    @pytest.mark.parametrize("sample,t,expected_o2n,expected_added", [
        (_SAMPLE_A, 2.0, [0, 2], [1]),
        (_SAMPLE_B, 2.0, [0, 2, 3], [1]),
        (_SAMPLE_B, 5.5, [0, 1, 2], [3]),   # 纯追加
    ])
    def test_assembly_exports_origin_mapping(self, sample, t,
                                             expected_o2n, expected_added):
        out: dict = {}
        shot, why = _assemble_keyframe_additions(
            copy.deepcopy(sample),
            [_new_frame(t, "补入动作", [_entry("乙", "抬手")])],
            frame_mapping_out=out)
        assert why == "", why
        assert len(shot["keyframes"]) == len(shot["frame_layouts"]["kfs"])
        assert out["source"] == "assembly"
        assert out["old_to_new"] == expected_o2n
        assert out["added_indices"] == expected_added
        assert validate_frame_mapping(out, out["before_count"],
                                      out["after_count"]) == ""

    def test_multiple_middle_inserts_keep_origin_order(self):
        out: dict = {}
        _shot, why = _assemble_keyframe_additions(
            copy.deepcopy(_SAMPLE_A),
            [_new_frame(2.0, "补入一", [_entry("乙", "抬手")]),
             _new_frame(2.5, "补入二", [_entry("乙", "挥手")])],
            frame_mapping_out=out)
        assert why == "", why
        assert out["old_to_new"] == [0, 3] and out["added_indices"] == [1, 2]

    def test_duplicate_layout_does_not_change_identity(self):
        """新增帧与原帧布局逐字相同：身份仍由**装配操作**决定，不靠内容猜."""
        out: dict = {}
        duplicate = copy.deepcopy(_SAMPLE_A["frame_layouts"]["kfs"][0])
        _shot, why = _assemble_keyframe_additions(
            copy.deepcopy(_SAMPLE_A),
            [_new_frame(2.0, "重复布局补帧", duplicate)],
            frame_mapping_out=out)
        assert why == "", why
        assert out["old_to_new"] == [0, 2] and out["added_indices"] == [1]

    def test_output_containers_are_reset_per_call(self):
        """失败调用不得留下上一次的映射（单次调用初始化）."""
        out: dict = {}
        _shot, why = _assemble_keyframe_additions(
            copy.deepcopy(_SAMPLE_A), [_insert()], frame_mapping_out=out)
        assert why == "" and out["after_count"] == 3
        _bad, why2 = _assemble_keyframe_additions(
            copy.deepcopy(_SAMPLE_A),
            [{"frame": {"t_sec": 2.0}}], frame_mapping_out=out)
        assert why2 and out == {}, (why2, out)
        outer: dict = {"stale": {"source": "assembly"}}
        _merged, count, _rej = _apply_shot_replacements(
            _script(_SAMPLE_A), [{"shot_id": "5", "shot": "不是对象"}],
            {"shot_5"}, frame_mappings_out=outer)
        assert count == 0 and outer == {}

    def test_apply_exports_mapping_only_for_committed_target(self):
        before, merged, mappings, _ = _apply(_SAMPLE_A, [_insert()])
        tid = _sample_id(_SAMPLE_A)
        assert list(mappings) == [tid]
        assert mappings[tid]["old_to_new"] == [0, 2]
        assert mappings[tid]["source"] == "assembly"
        assert len(merged["shots"][0]["keyframes"]) == 3
        assert before["shots"][0]["keyframes"] == _SAMPLE_A["keyframes"]

    def test_rejected_target_exports_no_mapping(self):
        before = {"shots": [copy.deepcopy(_SAMPLE_A), copy.deepcopy(_SAMPLE_B)],
                  "characters": [], "hook": None}
        mappings: dict = {}
        _merged, count, rejected = _apply_shot_replacements(
            before,
            [{"shot_id": "9", "keyframe_additions": [{"frame": {"t_sec": 2.0}}]}],
            {"shot_5", "shot_9"},
            allowed_by_target={"shot_5": set(_ALLOWED), "shot_9": set(_ALLOWED)},
            expected_targets={"5", "9"},
            frame_mappings_out=mappings)
        assert count == 0 and any("缺 layout" in r for r in rejected), rejected
        assert mappings == {}

    def test_updates_plus_insert_keeps_origin_coordinates(self):
        """原帧时间字段的合法更新不改变来源；先落 updates 再装配."""
        before, merged, mappings, _ = _apply(
            _SAMPLE_A, [_insert()],
            updates=[{"field_path": "keyframes.1.t_sec", "value": 3.5}])
        tid = _sample_id(_SAMPLE_A)
        assert mappings[tid]["old_to_new"] == [0, 2]
        assert _guard(before, merged, _SAMPLE_A, mappings) == []

    def test_whole_container_update_is_rejected_without_proven_source(self):
        """`updates` 整表重写 keyframes 且改写身份字段：来源链无法证明 ⇒ 拒收."""
        rewritten = [dict(_SAMPLE_A["keyframes"][0], desc="改写了身份"),
                     _SAMPLE_A["keyframes"][1]]
        _before, _merged, _maps, rejected = _apply(
            _SAMPLE_A, [_insert()],
            updates=[{"field_path": "keyframes", "value": rewritten}],
            expect=0)
        assert any("帧来源无法证明" in r or "来源链" in r for r in rejected), rejected

    def test_whole_container_update_with_verbatim_keyframes_is_accepted(self):
        """整表更新但关键帧完整结构逐字保留 ⇒ 来源明确、可验收（不是一律拒）."""
        _before, _merged, maps, rejected = _apply(
            _SAMPLE_A, [_insert()],
            updates=[{"field_path": "keyframes",
                      "value": copy.deepcopy(_SAMPLE_A["keyframes"])}])
        assert maps[_sample_id(_SAMPLE_A)]["old_to_new"] == [0, 2], rejected

    def test_compose_mappings_chains_update_and_assembly(self):
        pre = identity_frame_mapping(2)
        post = _assembly_mapping([0, 2], [1], 2, 3)
        composed = compose_frame_mappings(pre, post)
        assert composed["old_to_new"] == [0, 2]
        assert composed["added_indices"] == [1]
        assert composed["source"] == "assembly"
        assert validate_frame_mapping(composed, 2, 3) == ""


# ---------------------------------------------------------------------------
# S02：整表替换只允许「保留关键帧完整结构」的唯一、顺序一致关系
# ---------------------------------------------------------------------------

class TestFullShotIdentity:
    def test_unique_order_preserving_mapping_is_derived(self):
        base = copy.deepcopy(_SAMPLE_A["keyframes"])
        inserted = [base[0], {"t_sec": 2.0, "desc": "补入", "stage": "approach"},
                    base[1]]
        mp = derive_frame_mapping_from_keyframes(base, inserted)
        assert mp == {"before_count": 2, "after_count": 3,
                      "old_to_new": [0, 2], "added_indices": [1],
                      "source": "keyframe_structure"}

    def test_ambiguous_or_rewritten_frames_return_none(self):
        base = copy.deepcopy(_SAMPLE_A["keyframes"])
        rewritten = [dict(base[0], desc="改了"), dict(base[1], desc="改了")]
        assert derive_frame_mapping_from_keyframes(base, rewritten) is None
        # 重复关键帧 ⇒ 落点不唯一
        assert derive_frame_mapping_from_keyframes(
            [base[0], base[0]], [base[0], base[0], base[0]]) is None

    def test_full_shot_with_preserved_keyframes_is_accepted(self):
        before = _script(_SAMPLE_A)
        shot = copy.deepcopy(_SAMPLE_A)
        shot["prompt"] = "合法改写"
        merged, count, rejected = _apply_shot_replacements(
            before, [{"shot_id": "5", "shot": shot}], {"shot_5"},
            allowed_by_target={"shot_5": {"prompt"}},
            expected_targets={"5"})
        assert count == 1, rejected

    def test_full_shot_with_rewritten_identity_is_rejected(self):
        """run h 第 2 轮同形：整表替换 + 改关键帧 desc + 插帧 ⇒ 身份无法证明."""
        before = _script(_SAMPLE_A)
        shot = copy.deepcopy(_SAMPLE_A)
        shot["keyframes"][1]["desc"] = "被改写的关键帧描述"
        shot["keyframes"].append({"t_sec": 4.0, "desc": "补入", "stage": "exit"})
        shot["frame_layouts"]["kfs"].append([_entry("丙", "新增")])
        _merged, count, rejected = _apply_shot_replacements(
            before, [{"shot_id": "5", "shot": shot}], {"shot_5"},
            allowed_by_target={"shot_5": set(_ALLOWED)},
            expected_targets={"5"})
        assert count == 0, rejected
        assert any("帧来源无法证明" in r for r in rejected), rejected

    def test_authorized_leaf_only_difference_proves_identity(self):
        assert verify_frame_identity_by_authorized_leaves(
            _SAMPLE_A["keyframes"],
            [_SAMPLE_A["keyframes"][0],
             dict(_SAMPLE_A["keyframes"][1], t_sec=3.5)],
            {"keyframes.*.t_sec"})
        assert not verify_frame_identity_by_authorized_leaves(
            _SAMPLE_A["keyframes"],
            [_SAMPLE_A["keyframes"][0],
             dict(_SAMPLE_A["keyframes"][1], t_sec=3.5)],
            {"keyframes"})   # 整表容器授权会掩盖重排


# ---------------------------------------------------------------------------
# S03：守卫身份对齐 / 真违规仍拒收
# ---------------------------------------------------------------------------

class TestGuardIdentity:
    @pytest.mark.parametrize("sample", [_SAMPLE_A, _SAMPLE_B])
    def test_with_mapping_accepts_inserted_frame(self, sample):
        """合法中间插帧经正常入口保留下来."""
        before, merged, mappings, _ = _apply(sample, [_insert()])
        assert _guard(before, merged, sample, mappings) == []

    def test_true_rewrite_is_still_rejected_with_both_coordinates(self):
        """C 组：位移后真改写 pose/gaze 必须拒收，且诊断指出原/候选坐标."""
        before, merged, mappings, _ = _apply(_SAMPLE_A, [_insert()])
        tid = _sample_id(_SAMPLE_A)
        j = mappings[tid]["old_to_new"][1]
        merged["shots"][0]["frame_layouts"]["kfs"][j][0]["pose"] = "被偷偷改写"
        merged["shots"][0]["frame_layouts"]["kfs"][j][1]["gaze_target"] = "画下"
        violations = _guard(before, merged, _SAMPLE_A, mappings)
        assert len(violations) == 2, violations
        assert all("字段被改写" in v for v in violations)
        assert all("原 kfs[1] → 候选 kfs[%d]" % j in v for v in violations)

    def test_illegal_new_frame_shape_is_rejected(self):
        before, merged, mappings, _ = _apply(_SAMPLE_A, [_insert()])
        merged["shots"][0]["frame_layouts"]["kfs"][1] = [{"pose": "缺名字"}]
        violations = _guard(before, merged, _SAMPLE_A, mappings)
        assert any("缺 name" in v for v in violations), violations

    def test_unauthorized_new_frame_is_rejected(self):
        """新增帧需要整表补帧授权或逐字点名；只有叶子授权不够."""
        before, merged, mappings, _ = _apply(_SAMPLE_A, [_insert()])
        narrow = {"frame_layouts.kfs[0]", "frame_layouts.kfs[1]"}
        violations = _guard(before, merged, _SAMPLE_A, mappings, allowed=narrow)
        assert any("新增帧未获授权" in v for v in violations), violations

    @pytest.mark.parametrize("broken,keyword", [
        (_assembly_mapping([0, 1], [1], 2, 3), "重叠"),
        (_assembly_mapping([0, 9], [1], 2, 3), "越界"),
        (_assembly_mapping([2, 0], [1], 2, 3), "相对顺序"),
        ({"before_count": 2, "after_count": 3, "old_to_new": [0],
          "added_indices": [2], "source": "assembly",
          "source_revision": _REV}, "完整覆盖"),
        ({"before_count": 9, "after_count": 3, "old_to_new": [0, 2],
          "added_indices": [1], "source": "assembly",
          "source_revision": _REV}, "不符"),
    ])
    def test_broken_mapping_fails_closed(self, broken, keyword):
        before, merged, _mappings, _ = _apply(_SAMPLE_A, [_insert()])
        tid = _sample_id(_SAMPLE_A)
        violations = _guard(before, merged, _SAMPLE_A, {tid: broken})
        assert any("帧映射无效" in v and keyword in v for v in violations), violations

    def test_cross_revision_mapping_is_rejected_not_filtered(self):
        """跨 revision 映射不得被静默过滤后走同下标比较."""
        before, merged, mappings, _ = _apply(_SAMPLE_A, [_insert()])
        tid = _sample_id(_SAMPLE_A)
        stale = dict(mappings[tid], source_revision="rev-OLD")
        violations = _guard(before, merged, _SAMPLE_A, {tid: stale})
        assert any("revision" in v for v in violations), violations

    def test_identity_mapping_keeps_existing_contract(self):
        before = _script(_SAMPLE_A)
        after = copy.deepcopy(before)
        tid = _sample_id(_SAMPLE_A)
        mp = {tid: identity_frame_mapping(len(_SAMPLE_A["keyframes"]),
                                          source_revision=_REV)}
        assert _guard(before, after, _SAMPLE_A, mp) == []
        after["shots"][0]["frame_layouts"]["kfs"][1][0]["pose"] = "改写"
        assert len(_guard(before, after, _SAMPLE_A, mp)) == 1

    def test_no_structure_change_without_mapping_keeps_legacy(self):
        """帧数未变且无映射 ⇒ 旧坐标比较（旧入口兼容，不放宽任何判据）."""
        before = _script(_SAMPLE_A)
        after = copy.deepcopy(before)
        after["shots"][0]["frame_layouts"]["kfs"][1][0]["pose"] = "改写"
        assert len(frame_layout_structural_violations(
            before, after, {_sample_id(_SAMPLE_A): set(_ALLOWED)},
            {_sample_id(_SAMPLE_A)})) == 1

    def test_leaf_rewrite_is_still_protected(self):
        """位移后改写保留条目的叶子：帧被逐字点名时仍按纯增删判据拒收."""
        before, merged, mappings, _ = _apply(
            _SAMPLE_A, [_insert()],
            updates=[{"field_path": "frame_layouts.kfs[1][0].pose",
                      "value": "叶子改写"}])
        assert _guard(before, merged, _SAMPLE_A, mappings), \
            "保留条目的字段改写不应被映射放行"


# ---------------------------------------------------------------------------
# S03：效果登记落在合并后坐标 / 定位符完整性
# ---------------------------------------------------------------------------

class TestEffectsUseMappedCoordinates:
    def _mp(self):
        return _assembly_mapping([0, 2], [1], 2, 3)

    def test_effect_binds_to_new_frame_position(self):
        before = _script(_SAMPLE_A)
        merged = copy.deepcopy(before)
        base = copy.deepcopy(_SAMPLE_A["frame_layouts"]["kfs"])
        merged["shots"][0]["frame_layouts"]["kfs"] = [
            base[0], [_entry("乙", "抬手")],
            [base[1][0], _entry("丙", "新增")]]
        merged["shots"][0]["keyframes"].insert(
            1, {"t_sec": 2.0, "desc": "补入", "stage": "approach"})
        tid = _sample_id(_SAMPLE_A)
        effects = build_layout_effects(
            before, merged, {tid: set(_ALLOWED)}, _REV,
            round_id="1", frame_mappings={tid: self._mp()})
        hit = [e for e in effects if e.get("source_frame_locator")
               == "frame_layouts.kfs[1]"]
        assert hit, effects
        assert hit[0]["frame_locator"] == "frame_layouts.kfs[2]"
        assert [e["name"] for e in hit[0]["added_entries"]] == ["丙"]
        assert hit[0]["removed_entries"], "原帧条目删除也应登记到新位置"

    def test_pure_shift_produces_zero_effects(self):
        before, merged, mappings, _ = _apply(_SAMPLE_A, [_insert()])
        assert build_layout_effects(
            before, merged, {_sample_id(_SAMPLE_A): set(_ALLOWED)}, _REV,
            frame_mappings=mappings) == []

    def test_legacy_effect_record_has_no_source_locator(self):
        before = _script(_SAMPLE_A)
        merged = copy.deepcopy(before)
        merged["shots"][0]["frame_layouts"]["kfs"][0].append(_entry("丙", "新增"))
        tid = _sample_id(_SAMPLE_A)
        effects = build_layout_effects(before, merged,
                                       {tid: set(_ALLOWED)}, _REV)
        assert effects and all("source_frame_locator" not in e for e in effects)

    def test_missing_mapping_blocks_all_effects(self):
        """缺映射且帧结构变化 ⇒ 守卫拒收 ⇒ 零有效效果登记."""
        before = _script(_SAMPLE_A)
        merged = copy.deepcopy(before)
        merged["shots"][0]["frame_layouts"]["kfs"].append([])
        assert build_layout_effects(
            before, merged, {_sample_id(_SAMPLE_A): set(_ALLOWED)}, _REV) == []

    def test_frame_structure_state_ignores_t_sec_but_detects_reorder(self):
        shot = copy.deepcopy(_SAMPLE_A)
        other = copy.deepcopy(_SAMPLE_A)
        other["keyframes"][0]["t_sec"] = 1.4
        assert frame_structure_state(shot) == frame_structure_state(other)
        swapped = copy.deepcopy(_SAMPLE_A)
        swapped["keyframes"] = list(reversed(shot["keyframes"]))
        assert frame_structure_state(shot) != frame_structure_state(swapped)

    def test_frame_locator_integrity_detects_injected_structure_change(self):
        """后处理 writer 注入插入/删除 ⇒ 定位符失效，明确拒收."""
        before, merged, mappings, _ = _apply(_SAMPLE_A, [_insert()])
        effects = [{"shot_id": _sample_id(_SAMPLE_A),
                    "frame_locator": "frame_layouts.kfs[1]"}]
        assert frame_locator_integrity(merged, merged, effects,
                                       frame_mappings=mappings) == []
        broken = copy.deepcopy(merged)
        broken["shots"][0]["frame_layouts"]["kfs"].pop()
        broken["shots"][0]["keyframes"].pop()
        issues = frame_locator_integrity(merged, broken, effects,
                                         frame_mappings=mappings)
        assert issues and any("改变了帧结构" in x for x in issues), issues
