# -*- coding: utf-8 -*-
"""M06（2026-11-22）：收窄声音只从代码已判合法的片段里选（S11 repair_narrow_invalid ×3）.

S11 第 1 章 3 轮在 voice_text:n02 上连续 6 次被拒：旁白正文与原文差一个字（原文笔误被
「纠正」），内部又没有分句标点，任何截取都不可能通过执行器的逐字/边界/来源判据，但槽位
仍下发给模型。现在槽位用执行器同一判据预先枚举合法候选；没有候选就不下发。
样本全部匿名。
"""
from step01_script_gen import plan_content_repair as pcr

NOVEL = "三年前，老掌柜病故，把铺子交给了他十五岁的长子。众人都以为铺子要败落了。"


def _slot():
    return {"slot_id": "voice_text:n01", "affects_hook": False,
            "required_content_manifest": [], "unit_uid": "n01"}


def _legal(text, novel=NOVEL, kind="narration"):
    return pcr._legal_narrow_texts(_slot(), text, kind=kind, novel_text=novel,
                                   source_texts=[text])


def test_every_candidate_passes_the_executor_check():
    text = "三年前，老掌柜病故，把铺子交给了他十五岁的长子。"
    legal = _legal(text)
    assert legal, "有分句标点的句子应当有合法收窄"
    assert "把铺子交给了他十五岁的长子。" in legal
    for cand in legal:
        assert pcr._narrow_problems(_slot(), cand, original=text, kind="narration",
                                    novel_text=NOVEL, source_texts=[text]) == []
    assert len(legal) == len(set(legal))
    assert legal == sorted(legal, key=len, reverse=True)


def test_sentence_without_internal_boundary_has_no_candidate():
    text = "把铺子交给了他十五岁的长子。"
    assert _legal(text) == []


def test_altered_clause_is_never_offered():
    """正文把原文的字改了（来源仍是原文逐字）：含改动的片段不作为候选；其余逐字分句仍可选."""
    text = "三年前，老掌柜病逝，把铺子交给了他十五岁的长子。"
    legal = pcr._legal_narrow_texts(_slot(), text, kind="narration", novel_text=NOVEL,
                                    source_texts=["三年前，老掌柜病故，把铺子交给了他十五岁的长子。"])
    assert legal and all(c in NOVEL for c in legal)
    assert not any("病逝" in c for c in legal)


def test_single_altered_clause_has_no_candidate():
    """S11 形态：没有分句标点 + 与原文差一个字 → 没有任何合法收窄."""
    novel = "把铺子交给了他的的长子。"
    assert _legal("把铺子交给了他的长子。", novel=novel) == []


def test_candidates_are_capped():
    text = "，".join("甲%d" % i for i in range(30)) + "。"
    novel = text
    assert len(_legal(text, novel=novel)) <= pcr._NARROW_CANDIDATE_LIMIT


def test_hook_bound_voice_has_no_candidate():
    slot = dict(_slot(), affects_hook=True)
    text = "三年前，老掌柜病故，把铺子交给了他十五岁的长子。"
    assert pcr._legal_narrow_texts(slot, text, kind="narration", novel_text=NOVEL,
                                   source_texts=[text]) == []
