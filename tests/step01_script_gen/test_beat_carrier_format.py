# -*- coding: utf-8 -*-
"""节拍载体段的分隔符格式：解析器只认 `｜载体：`（全角竖线 + 全角冒号）.

2026-11-10 P05 实测：模型把 6/6 条节拍写成 `；载体：`，解析器读不到载体段，
后续每条事实都误报「该节拍没有标载体」。本文件用**自足合成样例**钉住：
  - `｜载体：` 成功；
  - `；载体：` / `;载体：` / `|载体：` / `｜载体:` / 前面没有 `｜` 的 `载体：` 失败；
  - 新守卫 `beat_carrier_format_problem` 只报格式，不误伤正文里提到的「载体」；
  - `plan_contract_records` 把格式问题放在承载语义问题之前。
"""
from __future__ import annotations

import pytest

from step01_script_gen.plan_feasibility import (
    BeatRecord, beat_carrier_format_problem, plan_contract_records,
)

NOVEL = "甲把家业交给了十六岁的儿子。"


def _beat(carrier_seg: str = "｜载体：画面") -> str:
    return ("节拍：甲接手家业｜本镜信息目的：交代背景｜原文依据："
            "“甲把家业交给了十六岁的儿子。”" + carrier_seg)


def _plan(beats):
    return {
        "required_facts": [
            "甲十六岁接手家业｜原文证据：“甲把家业交给了十六岁的儿子。”"
            "｜承载：B1/旁白1",
        ],
        "shot_beats": beats,
        "dialogue_lines": [],
        "narration_lines": [
            {"text": "甲把家业交给了十六岁的儿子。",
             "source": "甲把家业交给了十六岁的儿子。", "fact_refs": [1]},
        ],
        "fact_uids": ["f01"],
        "main_events": ["甲接手家业"],
        "key_scenes": ["甲家"],
        "causal_chain": ["起因→结果"],
        "character_motives": [],
        "omittable": [],
        "time_budget": "约 70 秒",
    }


def test_canonical_pipe_is_parsed():
    assert BeatRecord.parse(_beat("｜载体：画面")).carrier() == ("visual", None)
    assert BeatRecord.parse(_beat("｜载体：旁白1")).carrier() == ("narration", 1)
    assert BeatRecord.parse(_beat("｜载体：台词1")).carrier() == ("dialogue", 1)


def test_space_after_pipe_is_still_parsed():
    # 解析器用 lstrip 识别段首；`｜ 载体：` 是既有合法形态，守卫不能误杀。
    assert BeatRecord.parse(_beat("｜ 载体：画面")).carrier() == ("visual", None)
    assert beat_carrier_format_problem(_beat("｜ 载体：画面"), 1) is None


@pytest.mark.parametrize("sep", [
    "；载体：画面",      # 中文分号
    ";载体：画面",       # 半角分号
    "|载体：画面",       # 半角竖线
    "｜载体:画面",       # 半角冒号
    "载体：画面",        # 前面没有全角竖线
])
def test_malformed_separators_are_not_parsed_and_are_flagged(sep):
    beat = _beat(sep)
    assert BeatRecord.parse(beat).carrier() == (None, None)
    rec = beat_carrier_format_problem(beat, 1)
    assert rec is not None
    assert rec["code"] == "carrier_format_invalid"
    assert rec["path"] == "shot_beats[0].载体"
    assert "｜载体：" in rec["reason"]
    assert "全角" in rec["reason"]


def test_guard_does_not_flag_prose_mention_without_a_carrier_declaration():
    beat = _beat("｜载体：画面").replace("交代背景", "提到载体但不是声明")
    assert beat_carrier_format_problem(beat, 1) is None


def test_plan_contract_reports_format_before_carrier_semantics():
    plan = _plan([_beat("；载体：旁白1")])
    records = plan_contract_records(plan, novel_text=NOVEL, require_verification=False)
    assert records, records
    assert records[0]["code"] == "carrier_format_invalid"
    assert records[0]["index"] == 1
    assert "；载体：" in records[0]["reason"]
    assert "没有标载体" in records[0]["reason"]


def test_valid_plan_has_no_format_problem():
    plan = _plan([_beat("｜载体：旁白1")])
    records = plan_contract_records(plan, novel_text=NOVEL, require_verification=False)
    assert "carrier_format_invalid" not in [r.get("code") for r in records]


def test_new_code_is_registered_as_ops_fix():
    from step01_script_gen import outline as o
    assert o.PLAN_FIX_CAPABILITY["carrier_format_invalid"] == ("ops", "shot_beats")
