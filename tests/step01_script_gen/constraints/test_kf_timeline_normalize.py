"""KfTimelineNormalizeConstraint 单元测试（2026-09-12，95 分稿复盘根治项）.

锁不变量：kf 时间线同刻重排 / 越界钳制 / 首尾帧标签错位纠正 / hold 帧内容回归。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _make():
    from step01_script_gen.constraints.kf_timeline_normalize import (
        KfTimelineNormalizeConstraint,
    )
    return KfTimelineNormalizeConstraint()


def _ctx(shots: list[dict]):
    """构造并返回 script dict（apply 用 _apply）."""
    from tests.step01_script_gen.conftest import make_script
    return make_script(shots)


def _apply(script):
    from tests.step01_script_gen.conftest import make_ctx
    return _make().apply(make_ctx(script))


def test_duplicate_tsec_redistributed():
    """同刻堆叠（2.5/2.5/2.5）→ 在邻刻间均匀重排，t_sec 唯一且有序."""
    script = _ctx([{
        "index": 2, "duration_sec": 4.0, "location": "小舟",
        "ff_desc": "杨旭坐船头吹箫",
        "keyframes": [
            {"stage": "pre", "t_sec": 1.0, "desc": "杨旭吹箫"},
            {"stage": "after", "t_sec": 2.5, "desc": "放下玉箫"},
            {"stage": "after", "t_sec": 2.5, "desc": "舟继续缓行"},
            {"stage": "after", "t_sec": 2.5, "desc": "望向画左"},
        ],
    }])
    repairs = _apply(script)
    assert repairs, "同刻堆叠应触发重排"
    ts = sorted(k["t_sec"] for k in script["shots"][0]["keyframes"])
    assert len(set(ts)) == 4, f"重排后 t_sec 应唯一: {ts}"
    assert ts == sorted(ts)


def test_tsec_beyond_duration_clamped():
    """t_sec 超出 duration_sec → 钳到 0.95×duration."""
    script = _ctx([{
        "index": 3, "duration_sec": 4.0,
        "ff_desc": "a", "lf_desc": "b",
        "keyframes": [
            {"stage": "pre", "t_sec": 1.0, "desc": "起"},
            {"stage": "after", "t_sec": 5.2, "desc": "收"},
        ],
    }])
    repairs = _apply(script)
    assert any("t_sec" in r.target for r in repairs)
    assert script["shots"][0]["keyframes"][1]["t_sec"] <= 4.0


def test_first_frame_label_misposition_stripped():
    """「本帧画面即首帧静态状态」出现在非首帧 → 剥标签留内容."""
    script = _ctx([{
        "index": 4, "duration_sec": 4.0,
        "ff_desc": "杨旭仰卧船头",
        "keyframes": [
            {"stage": "pre", "t_sec": 1.0, "desc": "杨旭仰卧"},
            {"stage": "after", "t_sec": 2.0, "desc": "（本帧画面即首帧静态状态：杨旭举壶饮酒）"},
            {"stage": "after", "t_sec": 3.0, "desc": "杨旭双目微闭"},
        ],
    }])
    repairs = _apply(script)
    kf1 = script["shots"][0]["keyframes"][1]["desc"]
    assert "本帧画面即首帧静态状态" not in kf1
    assert "杨旭举壶饮酒" in kf1  # 内容保留


def test_hold_frame_copies_prev_kf_not_ff():
    """hold 帧底文抄 ff（时间回归）→ 改抄时间序上前一 kf 的内容."""
    ff = "张十三站在湾堤上，右手灯盏刚脱手，双腿蹬地，身体前倾"
    script = _ctx([{
        "index": 9, "duration_sec": 4.0,
        "ff_desc": ff,
        "keyframes": [
            {"stage": "pre", "t_sec": 0.6, "desc": "张十三站在湾堤上，灯盏脱手"},
            {"stage": "after", "t_sec": 1.5, "desc": "张十三纵身跃起，身体腾空朝水面飞掠"},
            {"stage": "after", "t_sec": 2.5, "desc": "张十三横越在水面上方，朝小舟落去"},
            {"stage": "after", "t_sec": 3.0,
             "desc": "张十三站在湾堤上，右手灯盏刚脱手掉落（画面延续，保持该状态）"},
        ],
    }])
    repairs = _apply(script)
    assert any("keyframes[3].desc" in r.target for r in repairs)
    kf3 = script["shots"][0]["keyframes"][3]["desc"]
    assert "画面延续" in kf3
    assert "横越在水面上方" in kf3  # 抄的是前一 kf（飞掠态），不再是 ff 的起跳态


def test_legit_kfs_untouched():
    """正常 kf（单调唯一、无错位标签）零修改."""
    kfs = [
        {"stage": "pre", "t_sec": 1.0, "desc": "甲"},
        {"stage": "after", "t_sec": 2.0, "desc": "乙"},
        {"stage": "after", "t_sec": 3.0, "desc": "丙"},
    ]
    script = _ctx([{"index": 5, "duration_sec": 4.0, "keyframes": kfs}])
    repairs = _apply(script)
    assert repairs == []
