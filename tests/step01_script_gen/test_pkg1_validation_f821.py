# -*- coding: utf-8 -*-
"""第 1 包 M05：validation.py 里 ruff F821 指出的两处未定义名字（离线）.

* `_check_focal_switch`：告警文案 f-string 里的 `{人物}` 被当成变量 → NameError；
* `_check_kf_time_window_alignment`：缺段内容提示引用未定义的 `_seg_texts`，
  被内层 try 吞掉 → 提示一直为空。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen import validation as v  # noqa: E402


def test_focal_switch_missing_direction_warning_keeps_placeholder_text():
    res = v._check_focal_switch([{"index": 1, "prompt": "焦点推近", "motion_desc": ""}])
    assert any("切换拍摄对象缺方向" in w and "{人物}" in w for w in res["warnings"]), res


def test_focal_switch_with_direction_is_silent():
    res = v._check_focal_switch([{"index": 1, "prompt": "焦点从左侧推近", "motion_desc": ""}])
    assert not any("缺方向" in w for w in res["warnings"]), res


def test_kf_missing_segment_hint_includes_segment_text():
    shot = {"index": 2, "duration_sec": 6.0,
            "motion_desc": "0-2秒：甲起身；2-4秒：甲拔刀；4-6秒：甲劈下",
            "keyframes": [{"t_sec": 1.0, "time_ratio": 1 / 6, "desc": "甲起身"}]}
    res = v._check_kf_time_window_alignment([shot])
    cov = res.get("coverage_warnings") or []
    assert cov, res
    assert any("缺段内容：[" in w and "拔刀" in w for w in cov), cov
