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


# ---- 分派：目标存在但不可收窄 ≠ 目标不存在（M06 复核，S11 ch1_r05/r08/r18） ----

def _voice_view_without_boundary():
    from tests.step01_script_gen.test_narrow import _voice_view
    return _voice_view(text="甲留在门外直到天黑后才走")


def test_unnarrowable_existing_target_is_not_reported_as_unknown():
    v = _voice_view_without_boundary()
    found = pcr.content_slots(v, [{"code": "verify_voice_text_mismatch", "uid": "n01",
                                   "kind": "verification", "path": "x"}],
                              novel_text="甲留在门外直到天黑后才走")
    assert not found["slots"], "无合法片段时不下发收窄槽位"
    assert found["blocked"] == [], "目标存在，不得记为身份失效"
    assert [r["code"] for r in found["narrow_unavailable"]] == ["repair_narrow_no_legal_span"]
    assert any(u.startswith("verify_voice_text_mismatch") for u in found["unexpressible"])


def test_dispatch_hands_unnarrowable_target_to_the_non_slot_path():
    """上层分派：不是 STATE_UNKNOWN_TARGET（零调用拒收），而是交既有非槽位备用路.

    真实入口 `revise_plan_once` 的回放见交付记录（17 份 S11 冻结输入）。"""
    from step01_script_gen import outline as o
    v = _voice_view_without_boundary()
    found = pcr.content_slots(v, [{"code": "verify_voice_text_mismatch", "uid": "n01",
                                   "kind": "verification", "path": "x"}],
                              novel_text="甲留在门外直到天黑后才走")
    # 与 `_content_slots_applicable` 同一判定顺序：blocked 为空 ⇒ 进入 unexpressible 分支
    deferred, blocking = o._slot_deferral(found["unexpressible"],
                                          [{"code": "verify_voice_text_mismatch"}])
    assert blocking and not deferred, "有槽位适配器的 code 表达不了 ⇒ 走既有非槽位备用路"
    assert pcr.STATE_UNKNOWN_TARGET != pcr.STATE_UNSUPPORTED


def test_missing_target_is_still_zero_call_unknown():
    from tests.step01_script_gen.test_narrow import _voice_view
    v = _voice_view()
    found = pcr.content_slots(v, [{"code": "verify_voice_text_mismatch", "uid": "n99",
                                   "kind": "verification", "path": "x"}])
    assert [b["code"] for b in found["blocked"]] == ["repair_slot_target_unknown"]
