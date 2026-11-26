# -*- coding: utf-8 -*-
"""缩时授权与应用按**可证明的内容下限**（S14 M04，2026-11-22）.

匿名样本，自足构造：发声/动作/关键帧决定每镜下限；`duration_budget` 只授权消除超额
所需的空余秒数；修复应用拒收低于下限的镜长，且在下限之上缩时不改镜内时段。
"""
from __future__ import annotations

import copy

from shared.config import DURATION_MAX_SEC
from step01_script_gen import main as m
from step01_script_gen.quality_issues import _duration_budget_issues
from step01_script_gen.timecode import shot_content_floor


def _shot(i, dur, *, motion_end=4.0, narration="", kf_t=2.0):
    return {"index": i, "duration": dur, "duration_sec": dur,
            "motion_desc": "0-%g秒：人物缓步走过长廊" % motion_end,
            "narration_segment": narration,
            "prompt": "[中景] 长廊", "ff_desc": "人物站在长廊口", "lf_desc": "人物走到长廊尽头",
            "keyframes": [{"t_sec": kf_t, "desc": "人物走到长廊中段", "stage": "approach"}],
            "frame_layouts": {"ff": [], "lf": [], "kfs": [[]]},
            "scene": "长廊", "location": "长廊", "subtitle": ""}


def _script(shots):
    return {"fps": 24, "shots": shots}


def test_floor_uses_spoken_text_motion_and_keyframes():
    long_line = "甲道：" + "这件事今晚必须说清楚" * 3
    s = _shot(1, 12.0, motion_end=4.0, narration=long_line)
    assert shot_content_floor(s) >= 8.0  # 发声撑起下限，不是固定 3s
    assert shot_content_floor(_shot(2, 9.0, motion_end=6.5)) >= 6.5
    assert shot_content_floor(_shot(3, 9.0, motion_end=3.0, kf_t=5.5)) >= 5.5


def test_only_needed_seconds_are_authorized_above_the_floor():
    # 每镜下限 4s；镜 2、镜 3 各有空余，超额只要镜 2 的一部分 ⇒ 只授权镜 2
    n = max(3, int((DURATION_MAX_SEC - 2.0) // 4.0))
    shots = [_shot(i + 1, 4.0) for i in range(n)]
    shots[0] = _shot(1, DURATION_MAX_SEC - 2.0 - 4.0 * (n - 1), motion_end=4.0)
    shots[1] = _shot(2, 4.0 + 3.0)
    shots[2] = _shot(3, 4.0 + 1.0)
    need = sum(s["duration"] for s in shots) - DURATION_MAX_SEC
    assert 0 < need <= 3.0
    issue = _duration_budget_issues(_script(shots))[0]
    assert issue.repairable and issue.repair_targets == [{"shot_id": "2", "field_path": "duration"}]
    assert "镜2 7.00s→%.2fs" % (7.0 - need) in issue.message


def test_no_authorization_when_every_shot_sits_on_its_floor():
    n = int(DURATION_MAX_SEC // 5.0) + 2
    shots = [_shot(i + 1, 5.0, motion_end=5.0) for i in range(n)]
    issue = _duration_budget_issues(_script(shots))[0]
    assert not issue.repairable and not issue.repair_targets
    assert "不授权缩短镜长" in issue.message


def test_repair_below_floor_is_rejected_and_baseline_kept():
    shot = _shot(5, 8.0, motion_end=6.0)
    result, count, rejected = m._apply_shot_replacements(
        _script([copy.deepcopy(shot)]), [{"shot_id": "5", "updates": {"duration": 4.0}}],
        {"shot_5"}, allowed_by_target={"shot_5": {"duration"}}, expected_targets={"5"})
    assert count == 0 and rejected and "内容下限" in rejected[0]
    assert result["shots"][0]["duration"] == 8.0


def test_repair_within_floor_trims_tail_without_rescaling():
    shot = _shot(5, 8.0, motion_end=6.0, kf_t=3.0)
    result, count, rejected = m._apply_shot_replacements(
        _script([copy.deepcopy(shot)]), [{"shot_id": "5", "updates": {"duration": 6.0}}],
        {"shot_5"}, allowed_by_target={"shot_5": {"duration"}}, expected_targets={"5"})
    assert rejected == [] and count == 1
    out = result["shots"][0]
    assert out["duration"] == out["duration_sec"] == 6.0
    assert out["motion_desc"] == shot["motion_desc"]
    assert out["keyframes"][0]["t_sec"] == 3.0
