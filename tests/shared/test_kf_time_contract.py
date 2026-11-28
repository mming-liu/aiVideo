# -*- coding: utf-8 -*-
"""`get_kf_t_sec` 的兼容契约（发现 8）.

`shared/kf_time.py` 的模块说明与本文是同一份契约的两半：**读取期不做任何缩放**。
删除惰性重标定之前，`get_kf_t_sec` 返回的是 `t_sec × (duration_sec / duration)`；
现在直接返回落盘值。这个差别只在 `duration != duration_sec` 时可见，所以必须有
一条测试明确锁住「此时返回原始秒数」，否则下一个实现者会按旧契约继续写代码
（审核发现 8：docstring 与实现互相矛盾，且没有任何测试锁定哪一边是对的）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.kf_time import get_kf_t_sec


def test_t_sec_returned_verbatim_when_two_durations_disagree():
    """**核心契约**：`duration != duration_sec` 时返回**原始秒数**，不按比例缩放.

    缩放会把 KF 从它自己所属的事件挪走——`motion_desc=0-2 举刀；2-4 收刀` 的
    4 秒镜被写成 8 秒后，读侧的 2 倍缩放会把 t=3 的「收刀」帧挪到 6 秒的
    空白尾段（审核发现 2 的最小复现）。
    """
    shot = {"duration_sec": 8.0, "duration": 4.0}
    assert get_kf_t_sec({"t_sec": 3.0}, shot) == 3.0

    shot2 = {"duration_sec": 4.0, "duration": 8.0}
    assert get_kf_t_sec({"t_sec": 3.0}, shot2) == 3.0


def test_t_sec_wins_over_time_ratio():
    """两个字段并存时以 `t_sec` 为准（`time_ratio` 只在没有 `t_sec` 时兜底）."""
    assert get_kf_t_sec({"t_sec": 2.5, "time_ratio": 0.1}, {"duration_sec": 10.0}) == 2.5


def test_time_ratio_fallback_uses_duration_sec():
    """08-22 之前落盘的数据只有 `time_ratio` → 换算成秒，优先取 `duration_sec`."""
    assert get_kf_t_sec({"time_ratio": 0.25}, {"duration_sec": 8.0, "duration": 4.0}) == 2.0


def test_time_ratio_fallback_falls_back_to_duration():
    assert get_kf_t_sec({"time_ratio": 0.25}, {"duration": 4.0}) == 1.0


def test_none_when_no_time_field():
    assert get_kf_t_sec({"desc": "x"}, {"duration_sec": 4.0}) is None
    assert get_kf_t_sec({"t_sec": "abc"}, {"duration_sec": 4.0}) is None
    assert get_kf_t_sec("not-a-dict", {"duration_sec": 4.0}) is None
    assert get_kf_t_sec({"time_ratio": 0.5}, {}) is None
