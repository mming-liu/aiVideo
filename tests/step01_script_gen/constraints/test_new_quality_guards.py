# -*- coding: utf-8 -*-
from __future__ import annotations

from step01_script_gen.constraints.context import ConstraintContext
from step01_script_gen.constraints.motion_time_bounds import MotionTimeBoundsConstraint
from step01_script_gen.constraints.offscreen_action_consistency import (
    OffscreenActionConsistencyConstraint,
)


def _ctx(script):
    return ConstraintContext.from_script(script)


def test_motion_time_bounds_reports_without_extending(monkeypatch):
    """动作提到第 6 秒 > 时长 4 秒 → **只报告**，不改写时长（v6.79，审核发现 1）.

    原实现直接按文本提及的最大秒数延长 duration_sec；那是链后写入，会让
    链前 KF 检查看到的时长不等于最终时长，且绕开单镜 cap。
    """
    import shared.model_caps as caps
    monkeypatch.setattr(caps, "active_video_duration_cap", lambda: 12)
    script = {"shots": [{
        "index": 3,
        "duration_sec": 4.0,
        "motion_desc": "0-2秒：仰卧；2-4秒：环颈；4-6秒：望星空",
    }]}
    repairs = MotionTimeBoundsConstraint().apply(_ctx(script))
    assert repairs
    assert repairs[0].action == "detect", "v6.79 起只报告"
    assert script["shots"][0]["duration_sec"] == 4.0, "原时长必须保留"


def test_offscreen_action_removes_stale_attack_sentence_when_exit_is_explicit():
    script = {"shots": [{
        "index": 5,
        "lf_desc": "神秘人已没入水中，水面震荡。神秘人位于画右、持刀前刺。",
        "frame_layouts": {"ff": [], "lf": [
            {"name": "神秘人", "on_screen": False, "props": []}
        ], "kfs": []},
    }]}
    OffscreenActionConsistencyConstraint().apply(_ctx(script))
    assert "持刀前刺" not in script["shots"][0]["lf_desc"]
    assert "已没入水中" in script["shots"][0]["lf_desc"]


def test_offscreen_action_flips_spec_when_description_has_visible_action():
    script = {"shots": [{
        "index": 5,
        "lf_desc": "神秘人位于画右、持刀前刺。",
        "frame_layouts": {"ff": [], "lf": [
            {"name": "神秘人", "on_screen": False, "props": []}
        ], "kfs": []},
    }]}
    OffscreenActionConsistencyConstraint().apply(_ctx(script))
    assert script["shots"][0]["frame_layouts"]["lf"][0]["on_screen"] is True