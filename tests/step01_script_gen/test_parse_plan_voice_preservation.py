# -*- coding: utf-8 -*-
"""S14-07（S11 ch2_r04 回放）：候选解析时按**本请求计划**保护批准声音.

旧行为：候选刚解析时还没挂 `chapter_outline`，批准声音表为空，长句断句往逐字旁白里
插逗号 ⇒ 三个候选都被判 extra_dialogue / plan_refs_narration_missing。匿名样本。
"""
from __future__ import annotations

import json

from step01_script_gen.plan_feasibility import is_approved_voice_fragment
from step01_script_gen.validation import parse_response

LINE = "守门的老人是城里最早来到这里的一个木匠。"
PLAN = {"narration_lines": [{"text": LINE, "source": LINE, "fact_refs": [1]}],
        "dialogue_lines": []}


def _raw():
    return json.dumps({"shots": [{
        "index": 1, "prompt": "[中景] 城门口，老人坐在门边", "ff_desc": "老人坐在门边",
        "lf_desc": "老人抬头", "motion_desc": "0-4秒：老人抬头",
        "narration_segment": "旁白：" + LINE, "subtitle": "旁白：" + LINE,
        "duration_sec": 4.0}], "narration": ""}, ensure_ascii=False)


def test_label_is_stripped_when_matching_approved_voice():
    assert is_approved_voice_fragment("旁白：" + LINE, (LINE,))


def test_plan_aware_parse_keeps_approved_narration_verbatim():
    s = parse_response(_raw(), plan=PLAN)
    assert s["shots"][0]["narration_segment"] == "旁白：" + LINE


def test_without_plan_the_long_clause_is_still_broken():
    # 对照：没有计划时仍按旧规则断句（证明上面的保护来自计划，而不是规则被关掉）
    s = parse_response(_raw())
    assert s["shots"][0]["narration_segment"] != "旁白：" + LINE
