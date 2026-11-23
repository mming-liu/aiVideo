# -*- coding: utf-8 -*-
"""S05（2026-11-23）：legacy 拆分裁决的来源侧校准——匿名最小情节，影子检查，不改任何门禁.

情节形状与冻结现场一致：父主张是两个分句，证据只引了第一句，第二分句缺主体；
原文里两句分别连续存在。校准结论：
1. 影子原子性检查能确定性发现父主张"复合且未被证据覆盖"（来源缺陷可见）；
2. 受限拆分合同只接受父主张的**逐字连续片段**——合法拆分会原样继承缺主体的分句；
3. 改写子主张（无论忠于原文还是换了主体）都被合同拒收。
所以拆分修不了来源缺陷：缺主体要在**来源生产者**那里改父主张，不能指望拆分或放宽 M。
"""
from step01_script_gen import fact_atomicity as fa
from step01_script_gen import restricted_split as rs

S1 = "少爷喝多了，差点掉进河里，丫鬟这才叫了起来。"
S2 = "铺子里出了急事，得请少爷立刻回去处理。"
NOVEL = ("夜深了，管家走到门口，对庄丁说：“" + S1 + "”庄丁正要退下，管家又道：“"
         + S2 + "”说罢扶着少爷上了车。")
PARENT_CLAIM = "管家对庄丁谎称少爷醉酒，并称有急事要回去"
PARENT = "%s｜原文证据：“%s”｜承载：B3/台词1" % (PARENT_CLAIM, S1)


def _plan(facts):
    return {"required_facts": facts, "fact_uids": ["f%02d" % (i + 1) for i in range(len(facts))]}


def _child(claim, evidence):
    return "%s｜原文证据：“%s”｜承载：B3/台词1" % (claim, evidence)


def _split(second_claim, second_evidence=S2, first=("管家对庄丁谎称少爷醉酒", S1)):
    kids = [_child(*first), _child(second_claim, second_evidence)]
    return [p["code"] for p in rs.split_contract_problems(
        PARENT, kids, novel_text=NOVEL, parent_uid="f01", child_uids=["f01a", "f01b"])]


def test_composite_parent_with_partial_evidence_is_flagged_by_the_shadow_check():
    probs = fa.atomicity_problems(_plan([PARENT]), NOVEL)
    assert probs and probs[0]["code"] == "fact_atomicity_composite_uncovered"
    assert probs[0]["legal_split"] is False
    assert any("急事" in s for s in probs[0]["uncovered_segments"])


def test_legal_split_inherits_the_missing_subject_verbatim():
    """合同合法的拆法 = 父主张逐字切开：第二个子主张仍是"并称有急事要回去"（谁回去仍不明）."""
    assert _split("并称有急事要回去") == []
    assert "少爷" not in "并称有急事要回去"


def test_rewritten_children_are_rejected_whether_faithful_or_subject_swapped():
    faithful = _split("管家称铺子出了急事，须少爷立刻回去处理")
    swapped = _split("管家称自己有急事要立刻回去")
    for codes in (faithful, swapped):
        assert "split_child_claim_not_covered" in codes
        assert "split_claim_union_mismatch" in codes
