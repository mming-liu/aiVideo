# -*- coding: utf-8 -*-
"""R3-2：首版原子性预检（R2-6 冻结回归 + 匿名正例）."""
from step01_script_gen import fact_atomicity as fa
from step01_script_gen import restricted_split as rs

F02 = ("杨旭十六岁接手家业，三年孝期刚过便考中秀才｜原文证据：“三年孝期刚过，"
       "杨文轩杨公子又参加府学，一举考中了诸生（秀才），有了功名在身”｜承载：B1/旁白2")
F06 = ("张十三对庄仆谎称公子醉酒，并驾车载杨旭与听香连夜离开｜原文证据：“张十三"
       "沉住了气，漫声说道：‘公子爷吃醉了酒，险些跌落水中，所以惊得听香姑娘尖叫"
       "起来。’”｜承载：B5/台词1")


def _plan(facts):
    return {"required_facts": facts,
            "fact_uids": ["f%02d" % (i + 1) for i in range(len(facts))]}


def _codes(probs):
    return [p["code"] for p in probs]


def test_r26_f02_composite_and_handover_needs_evidence():
    probs = fa.atomicity_problems(_plan([F02]))
    assert probs and probs[0]["uid"] == "f01"
    assert probs[0]["code"] == "fact_atomicity_composite_uncovered"
    assert any("接手" in x for x in probs[0]["uncovered_segments"])
    assert probs[0]["legal_split"] is False


def test_r26_f06_composite_and_carriage_needs_evidence():
    probs = fa.atomicity_problems(_plan([F06]))
    assert probs and probs[0]["code"] == "fact_atomicity_composite_uncovered"
    assert any("载杨旭" in x for x in probs[0]["uncovered_segments"])


def test_complete_atomic_fact_is_not_flagged():
    fact = ("杨旭今年刚及弱冠｜原文证据：“杨家的主人姓杨名旭，字文轩，今年刚及弱冠。”"
            "｜承载：B1/旁白1")
    assert fa.atomicity_problems(_plan([fact])) == []


def test_naming_clause_is_not_flagged_as_composite():
    fact = ("杨旭字文轩，今年刚及弱冠｜原文证据：“杨家的主人姓杨名旭，字文轩，"
            "今年刚及弱冠。”｜承载：B1/旁白1")
    assert fa.atomicity_problems(_plan([fact])) == []


def test_fully_covered_multi_clause_is_flagged_only_if_it_is_a_real_split():
    fact = ("杨旭推门进屋，在窗边坐下来｜原文证据：“杨旭推门进屋，环顾四周，"
            "在窗边坐下来”｜承载：B1/旁白1")
    probs = fa.atomicity_problems(_plan([fact]), novel_text="杨旭推门进屋，环顾四周，在窗边坐下来。")
    assert probs and probs[0]["legal_split"] is True
    assert len(probs[0]["proposed_children"]) == 2


def test_drop_f02_remains_semantic_rejection_not_uid_count():
    base = _plan([F02])
    cand = _plan([])
    probs = rs.plan_split_problems(base, cand, "f01", [], novel_text="")
    assert "split_children_empty" in _codes(probs)


def _two(claim, evidence):
    return _plan(["%s｜原文证据：“%s”｜承载：B1/旁白1" % (claim, evidence)])


def test_bigram_similarity_never_becomes_covered():
    seg = "杨旭十六岁接手家业"
    ev = "杨旭十六岁另谋出路"
    assert fa._covered(seg, [ev]) == ""
    cov = fa._coverage(seg, [ev])
    assert cov["direct"] == ""
    assert cov["similarity"] > 0


def test_handover_vs_alternative_is_uncovered():
    probs = fa.atomicity_problems(_two(
        "杨旭十六岁接手家业，数年间使杨家跻身青州十大豪门",
        "杨旭十六岁另谋出路，数年间使杨家跻身青州十大豪门"))
    p = probs[0]
    assert "杨旭十六岁接手家业" in p["uncovered_segments"]
    assert "杨旭十六岁接手家业" not in p["covered_segments"]
    child = next(c for c in p["proposed_children"]
                 if c["claim"] == "杨旭十六岁接手家业")
    assert child["needs_evidence"] is True and child["evidence"] == ""


def test_negation_or_exclusion_is_uncovered():
    for claim, ev in [
        ("杨旭没有接手家业，数年间使杨家跻身青州十大豪门",
         "杨旭接手家业，数年间使杨家跻身青州十大豪门"),
        ("只有杨旭接手家业，数年间使杨家跻身青州十大豪门",
         "杨旭接手家业，数年间使杨家跻身青州十大豪门"),
    ]:
        p = fa.atomicity_problems(_two(claim, ev))[0]
        assert p["covered_segments"] == ["数年间使杨家跻身青州十大豪门"]
        assert p["uncovered_segments"][0] in (claim.split("，")[0],)


def test_number_age_or_time_mismatch_is_uncovered():
    p = fa.atomicity_problems(_two(
        "杨旭二十岁接手家业，数年间使杨家跻身青州十大豪门",
        "杨旭十六岁接手家业，数年间使杨家跻身青州十大豪门"))[0]
    assert "杨旭二十岁接手家业" in p["uncovered_segments"]


def test_subject_object_swap_is_uncovered():
    p = fa.atomicity_problems(_two(
        "张十三杀死杨旭，数年间使杨家跻身青州十大豪门",
        "杨旭杀死张十三，数年间使杨家跻身青州十大豪门"))[0]
    assert "张十三杀死杨旭" in p["uncovered_segments"]
    assert fa._coverage("张十三杀死杨旭",
                        ["杨旭杀死张十三"])["similarity"] > 0


def test_causal_condition_difference_is_uncovered():
    p = fa.atomicity_problems(_two(
        "因为杨旭接手家业，所以杨家迅速败落",
        "因为杨旭接手家业，所以杨家迅速崛起"))[0]
    assert "所以杨家迅速败落" in p["uncovered_segments"]
