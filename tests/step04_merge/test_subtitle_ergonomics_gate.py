# -*- coding: utf-8 -*-
"""字幕屏显质量门（2026-11-28 O7）.

离线纯函数：超长 / 停留过短 / 越界 / 跨镜 / 重叠五类问题，以及 `block` 策略下的
自动拆行。默认 `warn` 只报告——既有 SRT 产物必须逐字节不变。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.subtitle_contract import SubtitleCue, SubtitlePlan
from step04_merge import subtitle_gate as gate


def _cue(cue_id, text, start, end, shot=0):
    return SubtitleCue(cue_id=cue_id, text=text, start_sec=start, end_sec=end,
                       shot_index=shot)


def _plan(*cues):
    return SubtitlePlan(cues=tuple(cues))


# ---------------------------------------------------------------------------
# check_plan
# ---------------------------------------------------------------------------
def test_clean_plan_has_no_problems():
    plan = _plan(_cue("c1", "他抬起头", 0.0, 2.0),
                 _cue("c2", "风停了", 2.0, 4.0))
    assert gate.check_plan(plan) == []


def test_long_cue_is_reported_by_longest_line():
    plan = _plan(_cue("c1", "这一行字幕确实超过了十六个字的竖屏上限", 0.0, 4.0))
    problems = gate.check_plan(plan, max_chars=16)
    assert [p["code"] for p in problems] == ["cue_too_long"]
    assert problems[0]["chars"] > 16
    # 换行按最长一行算
    long_line = "这一行字幕也非常非常长超过十六字上限"
    plan2 = _plan(_cue("c2", "短行\n" + long_line, 0.0, 4.0))
    problems2 = gate.check_plan(plan2, max_chars=16)
    assert [p["code"] for p in problems2] == ["cue_too_long"]
    assert problems2[0]["chars"] == len(long_line)


def test_short_dwell_is_reported():
    plan = _plan(_cue("c1", "啊", 0.0, 0.2))
    problems = gate.check_plan(plan, min_duration_sec=0.4)
    assert [p["code"] for p in problems] == ["cue_too_short"]
    assert problems[0]["duration_sec"] == pytest.approx(0.2)


def test_overlap_and_invalid_window_are_reported():
    plan = _plan(_cue("c1", "甲", 0.0, 2.0), _cue("c2", "乙", 1.5, 3.0))
    codes = [p["code"] for p in gate.check_plan(plan, min_duration_sec=0.1)]
    assert "cue_overlap" in codes
    bad = _plan(_cue("c3", "丙", 3.0, 2.0))
    assert "cue_time_invalid" in [p["code"] for p in gate.check_plan(bad)]


def test_out_of_range_and_crossing_shot_window_are_reported():
    plan = _plan(_cue("c1", "甲", 0.0, 5.0, shot=0), _cue("c2", "乙", 5.0, 9.0, shot=1))
    problems = gate.check_plan(plan, min_duration_sec=0.1, total_duration=8.0,
                               clip_windows=[(0.0, 5.0), (5.0, 8.0)])
    codes = {p["code"] for p in problems}
    assert "cue_out_of_range" in codes
    assert "cue_crosses_shot" in codes


def test_empty_cue_is_reported():
    plan = _plan(_cue("c1", "   ", 0.0, 2.0))
    assert [p["code"] for p in gate.check_plan(plan)] == ["cue_empty"]


# ---------------------------------------------------------------------------
# wrap_plan
# ---------------------------------------------------------------------------
def test_wrap_plan_splits_long_cue_keeping_order_and_span():
    text = "他抬起头看向远处的山峦，心里忽然明白了一件事，却什么也没有说出口。"
    plan = _plan(_cue("c1", text, 2.0, 8.0))
    wrapped = gate.wrap_plan(plan, max_chars=12)
    assert len(wrapped.cues) > 1
    assert "".join(c.text for c in wrapped.cues) == text, "拆分不得丢字/改字"
    assert all(len(c.text) <= 12 for c in wrapped.cues)
    assert wrapped.cues[0].start_sec == pytest.approx(2.0)
    assert wrapped.cues[-1].end_sec == pytest.approx(8.0)
    # 单调不重叠
    for prev, cur in zip(wrapped.cues, wrapped.cues[1:]):
        assert cur.start_sec >= prev.end_sec - 1e-9
    assert gate.check_plan(wrapped, max_chars=12, min_duration_sec=0.0) == []


def test_wrap_plan_is_noop_for_short_cues():
    plan = _plan(_cue("c1", "短句", 0.0, 2.0))
    assert gate.wrap_plan(plan, max_chars=16) is plan


# ---------------------------------------------------------------------------
# enforce
# ---------------------------------------------------------------------------
def test_warn_policy_reports_without_rewriting():
    plan = _plan(_cue("c1", "这一行字幕确实超过了十六个字的竖屏上限", 0.0, 4.0))
    seen = []
    result = gate.enforce(plan, warn=seen.append, env={})
    assert result["policy"] == "warn"
    assert result["ok"] is False and result["wrapped"] is False
    assert result["plan"] is plan, "warn 策略不得改写 plan"
    assert seen and "字幕门" in seen[0]


def test_off_policy_is_a_noop():
    plan = _plan(_cue("c1", "x" * 40, 0.0, 4.0))
    result = gate.enforce(plan, env={gate.ENV_POLICY: "off"})
    assert result["ok"] is True and result["problems"] == []
    assert result["plan"] is plan


def test_block_policy_wraps_then_passes_when_possible():
    text = "他抬起头看向远处的山峦，心里忽然明白了一件事，却什么也没有说出口。"
    plan = _plan(_cue("c1", text, 0.0, 8.0))
    result = gate.enforce(plan, env={gate.ENV_POLICY: "block",
                                     gate.ENV_MAX_CHARS: "12",
                                     gate.ENV_MIN_DURATION: "0"})
    assert result["wrapped"] is True
    assert len(result["plan"].cues) > 1
    assert "".join(c.text for c in result["plan"].cues) == text


def test_block_policy_raises_when_problem_remains():
    # 2 字却只有 0.1s：拆行也救不了停留时长
    plan = _plan(_cue("c1", "甲乙", 0.0, 0.1))
    with pytest.raises(gate.SubtitleErgonomicsError) as excinfo:
        gate.enforce(plan, env={gate.ENV_POLICY: "block",
                                gate.ENV_MIN_DURATION: "0.4"})
    assert excinfo.value.problems[0]["code"] == "cue_too_short"


def test_thresholds_are_configurable_and_invalid_values_fall_back():
    plan = _plan(_cue("c1", "十个字十个字十个字十", 0.0, 4.0))
    assert gate.check_plan(plan, max_chars=5)
    env = {gate.ENV_MAX_CHARS: "not-a-number", gate.ENV_MIN_DURATION: "bad"}
    result = gate.enforce(plan, env=env)
    assert result["max_chars"] == gate.DEFAULT_MAX_CHARS
    assert result["min_duration_sec"] == gate.DEFAULT_MIN_DURATION_SEC


def test_unknown_policy_is_rejected():
    with pytest.raises(ValueError):
        gate.gate_policy({gate.ENV_POLICY: "hard"})
