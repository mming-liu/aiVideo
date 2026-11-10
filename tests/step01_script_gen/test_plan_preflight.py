# -*- coding: utf-8 -*-
"""R3-3：核验前预检（原子性 + 声音/节拍闭环；影子）."""
from step01_script_gen.plan_preflight import preflight_problems

F02 = ("杨旭十六岁接手家业，三年孝期刚过便考中秀才｜原文证据：“三年孝期刚过，"
       "杨文轩杨公子又参加府学，一举考中了诸生（秀才），有了功名在身”｜承载：B1/旁白2")
F06 = ("张十三对庄仆谎称公子醉酒，并驾车载杨旭与听香连夜离开｜原文证据：“张十三"
       "沉住了气，漫声说道：‘公子爷吃醉了酒，险些跌落水中，所以惊得听香姑娘尖叫"
       "起来。’”｜承载：B5/台词1")


def test_r26_first_plan_blocked_before_verification():
    plan = {
        "required_facts": [F02, F06],
        "fact_uids": ["f02", "f06"],
        "shot_beats": [
            "节拍：黄昏水湾｜本镜信息目的：交代环境｜原文依据：“夕阳西下”｜载体：旁白1",
        ],
        "narration_lines": [
            {"text": "三年孝期刚过，考中秀才。", "source": "三年孝期刚过，考中了诸生（秀才）。",
             "fact_refs": [1]},
            {"text": "另一条未被承载的旁白。", "source": "另一条未被承载的旁白。",
             "fact_refs": []},
        ],
        "dialogue_lines": [],
    }
    out = preflight_problems(plan, "夕阳西下。三年孝期刚过，考中了诸生（秀才）。")
    assert out["passed"] is False
    assert out["first_rejection"]["preflight_stage"] == "fact_atomicity"
    codes = [p["code"] for p in out["problems"]]
    assert "fact_atomicity_composite_uncovered" in codes
    assert "voice_unplaced" in codes
    assert "voice_fact_ref_mismatch" in codes


def test_legal_candidate_has_zero_preflight_problems():
    plan = {
        "required_facts": [
            "甲进屋｜原文证据：“甲进屋。”｜承载：B1/画面"],
        "fact_uids": ["f01"],
        "shot_beats": [
            "节拍：甲推门进屋｜本镜信息目的：建立人物｜原文依据：“甲进屋。”｜载体：画面"],
        "narration_lines": [],
        "dialogue_lines": [],
    }
    out = preflight_problems(plan, "甲进屋。")
    assert out["passed"] is True
    assert out["problems"] == []
