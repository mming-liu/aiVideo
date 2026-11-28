# -*- coding: utf-8 -*-
"""2026-09-27 用户裁决 3：**缺段只计一次**，时间窗检查保留越窗/顺序.

## 问题（本轮实跑取证）

`validation._check_kf_time_window_alignment` 的检测 1（时段覆盖率）与新接入质量门
的 `quality_issues.kf_segment_missing` 判据同源（都走 `kf_coverage.analyze`），
**报的是同一条缺段**；两者都在 `_QUALITY_BLOCKING_CATEGORIES` 里 → 同一条缺陷
计两次风险。实测 08:11 新稿：11 镜里 8 镜「2 帧 / 3 段」→ 24 条 blocking 里
`kf_time_window_alignment` 8 条 + `kf_segment_missing` 8 条，**8 条是重复**。
更直接的是修复 prompt：`_collect_repair_warnings` 只收 `is_blocking` 的条目 →
同一缺段被写进同一镜的告警列表**两遍**。

## 本文件锁住

1. 一条缺段 → **恰好一条** blocking（`kf_segment_missing`），
   `kf_time_window_alignment` 对它 0 条；
2. 检测 1 的文本不丢：落到**非阻塞**诊断类别 `kf_segment_coverage_diag`；
3. 越窗（orphan KF）**仍然阻塞**（裁决要求「保留越窗、顺序问题」）；
4. 修复 prompt 的 per-shot 告警里该缺段只出现一次；
5. 授权面**不被顺带收窄或放宽**：`kf_time_window_alignment` 剩下的两类只需改叶子
   → 不再授予列表级（可增删 KF）权限；`kf_segment_missing` 的列表级授权保持不变。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

#: 与 08:11 新稿同形：3 段 / 2 帧，第 2 段（2-4s）没有任何帧归属它
_MOTION_3SEG = "前2秒：水面微澜，舟身轻晃；2-4秒：神秘人一按船舷，跃上船头；4-5秒：寒光刺出"
#: 缺口样本：0-2s 与 4-6s 两段，t=3.0 的帧落在两段之间的空档
_MOTION_GAP = "0-2秒：水面微澜，舟身轻晃；4-6秒：寒光刺出，人影倒纵"


def _shot(motion: str, kf_times: list, duration: float = 5.0, layouts=None):
    from tests.step01_script_gen.conftest import make_shot

    shot = make_shot(index=1, motion_desc=motion, duration_sec=duration,
                     ff_desc=" f", lf_desc=" l",
                     keyframes=[{"t_sec": t, "desc": "d"} for t in kf_times])
    if layouts is not None:
        shot["frame_layouts"] = layouts
    return shot


def _collect(shot):
    from step01_script_gen.main import _QUALITY_BLOCKING_CATEGORIES, _VIDEO_ONLY_CATEGORIES
    from step01_script_gen.quality_issues import collect_structured_issues
    from tests.step01_script_gen.conftest import make_script

    return collect_structured_issues(
        make_script([shot]), _QUALITY_BLOCKING_CATEGORIES, _VIDEO_ONLY_CATEGORIES)


def _codes(issues, code):
    return [i for i in issues if i.code == code]


class TestSingleBlockingReport:
    def test_missing_segment_is_blocked_exactly_once(self):
        from step01_script_gen.quality_issues import is_blocking

        issues, _failed, _ver = _collect(_shot(_MOTION_3SEG, [1.0, 4.0]))
        miss = _codes(issues, "kf_segment_missing")
        win = _codes(issues, "kf_time_window_alignment")
        assert len(miss) == 1 and is_blocking(miss[0]), [i.code for i in miss]
        assert win == [], (
            "缺段不得再由 kf_time_window_alignment 重复上报（同一缺陷计两次风险）"
            f"：{[i.message[:60] for i in win]}")

    def test_missing_text_is_kept_in_a_non_blocking_diagnostic_category(self):
        from step01_script_gen.quality_issues import is_blocking

        issues, _failed, _ver = _collect(_shot(_MOTION_3SEG, [1.0, 4.0]))
        diag = _codes(issues, "kf_segment_coverage_diag")
        assert len(diag) == 1, [i.code for i in issues]
        assert not is_blocking(diag[0]), diag[0].severity
        assert "缺段索引" in diag[0].message

    def test_orphan_keyframe_still_blocks(self):
        """裁决要求保留「越窗」：帧落在两段之间的空档 → 仍然 blocking."""
        from step01_script_gen.quality_issues import is_blocking

        issues, _failed, _ver = _collect(_shot(_MOTION_GAP, [1.0, 3.0, 5.0], 6.0))
        win = _codes(issues, "kf_time_window_alignment")
        assert len(win) == 1 and is_blocking(win[0]), [i.message[:70] for i in win]
        assert "不在 motion_desc 时段内" in win[0].message


class TestRepairPromptSeesItOnce:
    def test_shot_warnings_mention_the_missing_segment_once(self):
        from step01_script_gen.main import _collect_repair_warnings
        from tests.step01_script_gen.conftest import make_script

        rep = _collect_repair_warnings(make_script([_shot(_MOTION_3SEG, [1.0, 4.0])]), "")
        msgs = rep["shots"].get("shot_1", [])
        new_style = [m for m in msgs if "没有**按时间" in m]
        old_style = [m for m in msgs if "缺段索引" in m]
        assert len(new_style) == 1, [m[:60] for m in msgs]
        assert old_style == [], (
            "旧通道（kf_time_window_alignment 检测 1）的文案仍进了 per-shot 告警"
            "——同一缺段会被写进 prompt 两遍")


class TestAuthorizationNotWidened:
    """检测 1 移出后，`kf_time_window_alignment` 只剩叶子级修法（越窗/顺序）."""

    def _issue(self, code):
        from step01_script_gen.quality_issues import Issue

        # 确定性 issue：`repair_targets=None` → 走 code 表 / field_path 兼容路径
        return Issue(code=code, severity="visual_risk", message="m",
                     shot_id="1", field_path=code, repairable=True)

    def _allowed(self, code):
        from step01_script_gen.repair_fields import allowed_repair_fields

        shot = _shot(_MOTION_GAP, [1.0, 3.0, 5.0], 6.0)
        return allowed_repair_fields([], [self._issue(code)],
                                     target_shot_id="shot_1",
                                     shots_by_id={"shot_1": shot})

    def test_window_alignment_no_longer_grants_list_level(self):
        allowed = self._allowed("kf_time_window_alignment")
        assert "keyframes.*.t_sec" in allowed, sorted(allowed)
        assert "keyframes" not in allowed, sorted(allowed)
        assert "frame_layouts.kfs" not in allowed, sorted(allowed)

    def test_segment_missing_keeps_its_list_level_grant(self):
        """对照组：缺段的列表级授权（增删帧）**不受本次收窄影响**."""
        from step01_script_gen.repair_fields import (
            _KF_STRUCTURAL_CODES, allowed_repair_fields,
        )
        from step01_script_gen.quality_issues import Issue

        assert "kf_segment_missing" in _KF_STRUCTURAL_CODES
        assert "kf_time_window_alignment" not in _KF_STRUCTURAL_CODES, (
            "检测 1 移出后它不该继续凭旧类别拿结构权限")
        issue = Issue(code="kf_segment_missing", severity="visual_risk", message="m",
                      shot_id="1", field_path="keyframes",
                      repair_targets=[{"shot_id": "shot_1", "field_path": "keyframes"},
                                      {"shot_id": "shot_1",
                                       "field_path": "frame_layouts.kfs"}],
                      repairable=True)
        allowed = allowed_repair_fields(
            [], [issue], target_shot_id="shot_1",
            shots_by_id={"shot_1": _shot(_MOTION_3SEG, [1.0, 4.0],
                                         layouts={"ff": [], "lf": [],
                                                  "kfs": [[], []]})})
        assert "keyframes" in allowed and "frame_layouts.kfs" in allowed, sorted(allowed)
