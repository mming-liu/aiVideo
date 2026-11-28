# -*- coding: utf-8 -*-
"""KF 结构修复：授权面 + 验收闸门（v6.104，总计划 §1.1）.

背景（真实重跑实测）：修复 prompt 的决策树一直写着「报『kf 时段缺段』→ 按
motion_desc 时段表补 1 帧定格」，但授权面**只有** `keyframes.*.desc/stage/t_sec`
这些叶子路径，而 `merge_authorized` 的规则是「列表路径本身未被授权 → 长度变化
一律回填原值」——模型的补帧正解被静默丢弃，`kf_time_window_alignment` 结构性
不可解，三轮重跑全部停在 `resolved=0` → BLOCK。

本文件锁住三件事：
  1. 该授权（`allowed_repair_fields` 给出列表本身）；
  2. 授权之后补帧**真的能活下来**（`merge_authorized` 不再回填）；
  3. 授权带来的风险由确定性验收兜住（KF 单调在镜内 + motion 原事件文本仍在）。
"""
from __future__ import annotations

from step01_script_gen.kf_structural_guard import (
    kf_order_violations, kf_structural_violations, motion_event_preservation,
)
from step01_script_gen.main import _repair_acceptance_decision
from step01_script_gen.repair_fields import allowed_repair_fields, merge_authorized


class _Issue:
    frame_id = ""
    entity_id = ""

    def __init__(self, code, field_path="", message=""):
        self.code = code
        self.field_path = field_path
        self.message = message


def _shot(motion="0-2秒：甲破水。2-4秒：乙闷哼。4-6秒：丙染血。", kfs=(1.0, 3.0),
          dur=6.0):
    return {
        "index": 4,
        "duration_sec": dur,
        "motion_desc": motion,
        "keyframes": [{"t_sec": t, "desc": "帧%d" % i, "stage": "pre"}
                      for i, t in enumerate(kfs)],
        "frame_layouts": {"kfs": [[{"name": "甲"}]] * len(kfs)},
    }


# --------------------------------------------------------------- 授权面
def test_segment_missing_issue_authorizes_the_list_itself():
    """缺段的列表级授权（2026-09-27 起唯一来源；旧由 `kf_time_window_alignment` 给）."""
    allowed = allowed_repair_fields(
        [], [_Issue("kf_segment_missing", "keyframes")])
    assert "keyframes" in allowed
    assert "frame_layouts.kfs" in allowed
    # 叶子路径已被列表包含 → 收窄掉，日志/REPAIR FEEDBACK 只列一层
    assert "keyframes.*.desc" not in allowed


def test_window_alignment_no_longer_authorizes_the_list_itself():
    """对照（2026-09-27 用户裁决 3）：`kf_time_window_alignment` 只剩越窗/顺序.

    它此前凭「检测 1：kfs 数 < 非空时段数 → 只能补帧」拿结构权限；检测 1 已并入
    `kf_segment_missing`，剩下的两类只改叶子 → 不该继续凭旧类别拿「可增删整张
    KF 列表」的授权面（总计划 §2.3「禁止顺带修改」）。
    """
    allowed = allowed_repair_fields(
        [], [_Issue("kf_time_window_alignment", "kf_time_window_alignment")])
    assert allowed, "越窗/顺序仍需叶子级授权（t_sec / desc）"
    assert "keyframes" not in allowed
    assert "frame_layouts.kfs" not in allowed


def test_result_action_support_authorizes_list_and_motion():
    allowed = allowed_repair_fields([], [_Issue("result_action_support", "motion_desc")])
    assert {"keyframes", "frame_layouts.kfs", "motion_desc"} <= allowed


def test_unrelated_issue_never_gets_list_authorization():
    for code, fp in (("ff_desc_length", "ff_desc"),
                     ("frame_presence_consistency", "frame_layouts.lf.*.on_screen"),
                     ("kf_event_window_mismatch", "keyframes.desc"),
                     ("kf_time_missing", "keyframes.*.t_sec")):
        allowed = allowed_repair_fields([], [_Issue(code, fp)])
        assert "keyframes" not in allowed, code
        assert "frame_layouts.kfs" not in allowed, code


def test_warning_text_without_structured_code_still_flags_missing_kf():
    """结构化 code 缺失时的文案回退——只认「缺段/缺帧」这类窄信号."""
    allowed = allowed_repair_fields(
        ["镜头4 motion_desc 分 3 段但缺段索引 [2]，只有 2 个 kf。"], [])
    assert "keyframes" in allowed
    assert "frame_layouts.kfs" in allowed
    # 无关告警不得放开增删
    assert "keyframes" not in allowed_repair_fields(["跨镜 LF 重复率 70%"], [])


# --------------------------------------------------------------- 合并层
def test_added_keyframe_survives_authorized_merge():
    """生产回归：授权列表后，模型的补帧正解必须活下来（此前整表被回填）."""
    target = _shot()
    new = _shot(kfs=(1.0, 3.0, 5.0))
    allowed = allowed_repair_fields(
        [], [_Issue("kf_segment_missing", "keyframes")])
    merged, echoed = merge_authorized(target, new, allowed)
    assert len(merged["keyframes"]) == 3
    assert len(merged["frame_layouts"]["kfs"]) == 3
    assert echoed == []


def test_added_keyframe_is_reverted_without_list_authorization():
    """对照（说明旧行为为什么必然 resolved=0）：只授权叶子路径 → 整表回填."""
    target = _shot()
    new = _shot(kfs=(1.0, 3.0, 5.0))
    merged, _ = merge_authorized(
        target, new, {"keyframes.*.desc", "keyframes.*.t_sec", "keyframes.*.stage"})
    assert len(merged["keyframes"]) == 2


# --------------------------------------------------------------- 验收闸门
def test_kf_order_violations_cover_shot_and_monotonicity():
    assert kf_order_violations(_shot()) == []
    assert kf_order_violations(_shot(kfs=(5.0, 1.0)))          # 倒装
    assert any("越出镜长" in m for m in kf_order_violations(_shot(kfs=(1.0, 9.0))))
    missing = _shot()
    missing["keyframes"][1] = {"desc": "无时刻", "stage": "pre"}
    assert any("缺 t_sec" in m for m in kf_order_violations(missing))


def test_motion_event_preservation_allows_adding_but_not_deleting():
    before = _shot()
    added = _shot(motion="0-2秒：甲破水。2-4秒：乙闷哼，丁丢下灯盏。4-6秒：丙染血。")
    assert motion_event_preservation(before, added) == []
    deleted = _shot(motion="0-3秒：甲破水。3-6秒：丙染血。")
    assert any("原事件文本丢失" in m for m in motion_event_preservation(before, deleted))
    reordered = _shot(motion="0-2秒：丙染血。2-4秒：乙闷哼。4-6秒：甲破水。")
    assert any("事件顺序变化" in m for m in motion_event_preservation(before, reordered))


def test_structural_violations_combine_both_gates():
    before = _shot()
    after = _shot(motion="0-3秒：甲破水。3-6秒：丙染血。", kfs=(5.0, 1.0))
    msgs = kf_structural_violations(before, after)
    assert any("原事件文本丢失" in m for m in msgs)
    assert any("非单调" in m for m in msgs)


# ------------------------------------------------- 差分口径（2026-09-28 复核决定）
# 门的意义是「本轮修复有没有把 KF 结构弄坏」，不是「这份稿此刻干不干净」——后者由
# 质量门在最终提交处施加（`kf_time_out_of_shot` 等 hard 项，绝对口径）。
def test_preexisting_out_of_window_frame_no_longer_fails_the_round():
    """run 02 的死锁：稿内既有越窗帧让任何改该镜 KF 的修复轮必被拒 → 零改善 → 停止修复."""
    before = _shot(dur=3.0, kfs=(1.0, 2.5, 3.5))       # 3.5 > 3.0：修复前就存在
    after = _shot(dur=3.0, kfs=(0.5, 1.0, 2.5, 3.5))   # 本轮补了一帧（下标全变），越窗帧未动
    assert kf_structural_violations(before, after) == []
    # 绝对口径不放宽：最终提交仍按这份稿判 blocking
    assert any("越出镜长" in m for m in kf_order_violations(after))


def test_newly_introduced_out_of_window_frame_still_fails():
    before = _shot(dur=3.0, kfs=(1.0, 2.5))
    after = _shot(dur=3.0, kfs=(1.0, 2.5, 3.5))
    assert any("越出镜长" in m for m in kf_structural_violations(before, after))


def test_old_violation_made_worse_counts_as_new():
    """同类旧问题被改坏（t 更大）→ 算本轮新引入，不能因「同型已存在」放过."""
    before = _shot(dur=3.0, kfs=(1.0, 3.5))
    after = _shot(dur=3.0, kfs=(1.0, 4.5))
    assert any("越出镜长" in m for m in kf_structural_violations(before, after))


def test_fixed_violation_is_not_reported():
    before = _shot(dur=3.0, kfs=(1.0, 3.5))
    after = _shot(dur=3.0, kfs=(1.0, 2.5))
    assert kf_structural_violations(before, after) == []


def test_same_violation_duplicated_counts_as_new():
    """同身份按多重集抵消：数量变多算新引入."""
    before = _shot(dur=4.0, kfs=(1.0, 4.5))
    after = _shot(dur=4.0, kfs=(1.0, 4.5, 4.5))
    assert len([m for m in kf_structural_violations(before, after)
                if "越出镜长" in m]) == 1


def _rec(code, shot_id="4", frame_id="", field_path=""):
    return {"code": code, "severity": "visual_risk", "shot_id": shot_id,
            "frame_id": frame_id, "entity_id": "", "field_path": field_path}


def test_acceptance_rejects_kf_structure_violation():
    before = {"issues": [_rec("kf_time_window_alignment")], "checker_failed": False}
    after = {"issues": [], "checker_failed": False}
    ok = _repair_acceptance_decision(before, after, [], {"shots": []}, False)
    assert ok["accepted"] is True
    bad = _repair_acceptance_decision(
        before, after, [], {"shots": []}, False,
        kf_structural_violations=["shot_4: 原事件文本丢失（第 1 段）：「乙闷哼」"])
    assert bad["accepted"] is False
    assert bad["reason"] == "kf_structure_invalid"
    assert bad["kf_structural_violations"]


def test_kf_structure_gate_is_independent_of_resolution_count():
    """闸门独立：即便本轮解决了别的阻塞，结构验收不过仍然整轮拒绝."""
    before = {"issues": [_rec("kf_time_window_alignment")], "checker_failed": False}
    after = {"issues": [], "checker_failed": False}
    decision = _repair_acceptance_decision(
        before, after, [], {"shots": []}, False,
        kf_structural_violations=["shot_4: KF 物理时刻非单调"])
    assert decision["resolved"] == 1
    assert decision["accepted"] is False


# --------------------------------------------------------------- 端到端
def test_scoped_helper_skips_shots_without_list_authorization():
    """只对「本轮拿到 KF 列表授权」的镜头生效——未授权镜头即使乱序也不判越权."""
    from step01_script_gen.main import _kf_structural_violations

    before = {"shots": [dict(_shot(), index=4)]}
    after = {"shots": [dict(_shot(kfs=(5.0, 1.0)), index=4)]}
    assert _kf_structural_violations(before, after, {"shot_4": {"keyframes"}}, {"shot_4"})
    assert _kf_structural_violations(
        before, after, {"shot_4": {"keyframes.*.desc"}}, {"shot_4"}) == []


def test_kf_coverage_warning_states_the_required_action():
    """缺段文本必须是**可执行的必须动作**（增几帧、t_sec 取多少），并显式禁止改 motion.

    第二十四次重跑实测：原标题只写「建议：每段生成 1 帧」，模型的实际动作是
    改写/合并 motion_desc（未授权 → 被回填）→ `resolved=0`。缺段是只能靠新增帧
    修的缺陷，把可确定的部分（缺段中点）算好写进告警。

    2026-09-27（用户裁决 3）：这段文本搬到**非阻塞诊断**通道 `coverage_warnings`
    （阻塞由 `kf_segment_missing` 单独上报，避免同一缺陷计两次风险）。
    """
    from step01_script_gen.validation import _check_kf_time_window_alignment

    shot = {
        "index": 4,
        "duration_sec": 6.0,
        "motion_desc": "0-2秒：甲破水。2-4秒：乙闷哼。4-6秒：丙染血。",
        "keyframes": [{"t_sec": 1.0, "desc": "甲破水"}, {"t_sec": 3.0, "desc": "乙闷哼"}],
    }
    result = _check_kf_time_window_alignment([shot])
    text = " ".join(result["coverage_warnings"])
    assert "缺段索引" in text
    assert "新增 1 帧" in text
    assert "t_sec 依次取 5" in text          # 第三段 (4,6) 的中点
    assert "禁止" in text and "合并 motion_desc" in text
    assert "缺段索引" not in " ".join(result["warnings"]), "缺段不得再走阻塞通道"
    # 文案回退路径不得因为这条告警放开整个 frame_layouts（ASCII 整词回避）
    assert "frame_layouts" not in text


# --------------------------------------------------- 补帧后的身份（v6.104.1）
def _kf_shot(index, n_kfs):
    return {"index": index, "duration_sec": 6.0,
            "keyframes": [{"t_sec": 1.0 * (i + 1), "desc": "d%d" % i}
                          for i in range(n_kfs)]}


def test_kf_reindex_does_not_create_phantom_blocking():
    """补帧后帧序号整体后移，同一缺陷不得被记成「一减一增」（第二十五次重跑）.

    实测：shot 9 帧数 2→4，`kf_event_window_mismatch` 的定位符从 kf1 变成 kf3，
    `resolved=4` 的一轮被这条**假新增**整轮回退。
    """
    before = {"issues": [_rec("kf_event_window_mismatch", "9", "kf1", "keyframes.desc")],
              "checker_failed": False}
    after = {"issues": [_rec("kf_event_window_mismatch", "9", "kf3", "keyframes.desc")],
             "checker_failed": False}
    decision = _repair_acceptance_decision(
        before, after, [], {"shots": [_kf_shot(9, 4)]}, False,
        script_before={"shots": [_kf_shot(9, 2)]})
    assert decision["reindexed_kf_shots"] == ["9"]
    assert decision["new_blocking"] == 0
    assert decision["resolved"] == 0


def test_kf_identity_still_positional_when_frame_count_unchanged():
    """对照：帧数没变时，位置序号仍是有效定位符——真的新增必须照旧拦住."""
    before = {"issues": [_rec("kf_event_window_mismatch", "9", "kf1", "keyframes.desc")],
              "checker_failed": False}
    after = {"issues": [_rec("kf_event_window_mismatch", "9", "kf3", "keyframes.desc")],
             "checker_failed": False}
    decision = _repair_acceptance_decision(
        before, after, [], {"shots": [_kf_shot(9, 3)]}, False,
        script_before={"shots": [_kf_shot(9, 3)]})
    assert decision["reindexed_kf_shots"] == []
    assert decision["new_blocking"] == 1
    assert decision["resolved"] == 1


def test_reindex_normalization_leaves_non_kf_identity_alone():
    """只有 kf 帧受重编号影响；同镜的 ff/lf 类 issue 仍按位置严格比较."""
    before = {"issues": [_rec("frame_presence_consistency", "1", "lf",
                              "frame_layouts.lf.*.on_screen")],
              "checker_failed": False}
    after = {"issues": [], "checker_failed": False}
    decision = _repair_acceptance_decision(
        before, after, [], {"shots": [_kf_shot(1, 5)]}, False,
        script_before={"shots": [_kf_shot(1, 2)]})
    assert decision["resolved"] == 1
