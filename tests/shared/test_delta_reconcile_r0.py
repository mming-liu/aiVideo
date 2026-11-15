"""R0-only 自动收尾的针对性测试（匿名、确定性、自足，零 `output/` 依赖）。"""
from __future__ import annotations

import copy
import json

import pytest

from shared import presentation_contract as pc
from shared.delta_reconcile import diagnose_splits, reconcile


def _view(*, beats, narr=(), dial=(), facts=(), hooks=None):
    """最小匿名视图：节拍只带 voice_ref，声音单元只带 uid/text。"""
    view = {
        "beats": [{"uid": u, "text": t,
                   "voice_ref": ({"kind": vr[0], "uid": vr[1]} if vr and vr[1] else None)}
                  for u, t, vr in beats],
        "narration_units": [{"uid": u, "text": t} for u, t in narr],
        "dialogue_units": [{"uid": u, "text": t} for u, t in dial],
        "fact_bindings": list(facts),
    }
    if hooks is not None:
        view["hook_bindings"] = hooks
    return view


def _hashes(view):
    return json.dumps(view, sort_keys=True, ensure_ascii=False)


# ---------------- R0：合法修复 ----------------
def test_a_missing_prefix_on_a_declared_temp_unit_is_repaired():
    view = _view(beats=[("b1", "甲", (pc.KIND_NARRATION, "n9"))],
                 narr=[("new:n9", "甲来。")])
    delta = {"upsert": {"narration_units": [{"uid": "new:n9", "text": "甲来。"}]}}
    out, notes = reconcile(view, base_view={}, delta=delta)
    assert out["beats"][0]["voice_ref"]["uid"] == "new:n9"
    assert [n["rule"] for n in notes] == ["R0_temp_prefix"]
    assert out["narration_units"] == view["narration_units"]      # 正文不动


# ---------------- R0：拒收例 ----------------
def test_an_undeclared_temp_unit_is_not_repaired():
    view = _view(beats=[("b1", "甲", (pc.KIND_NARRATION, "n9"))], narr=[("new:n9", "甲来。")])
    out, notes = reconcile(view, base_view={}, delta={"upsert": {}})
    assert out["beats"][0]["voice_ref"]["uid"] == "n9"
    assert any(n["rule"] == "R0_skipped" for n in notes)


def test_an_existing_baseline_uid_is_never_redirected():
    base = _view(beats=[], narr=[("n9", "既有。")])
    view = _view(beats=[("b1", "甲", (pc.KIND_NARRATION, "n9"))],
                 narr=[("n9", "既有。"), ("new:n9", "新。")])
    delta = {"upsert": {"narration_units": [{"uid": "new:n9", "text": "新。"}]}}
    out, _ = reconcile(view, base_view=base, delta=delta)
    assert out["beats"][0]["voice_ref"]["uid"] == "n9"


def test_a_kind_mismatch_is_not_repaired():
    view = _view(beats=[("b1", "甲", (pc.KIND_DIALOGUE, "n9"))],
                 narr=[("new:n9", "甲来。")])
    delta = {"upsert": {"narration_units": [{"uid": "new:n9", "text": "甲来。"}]}}
    out, notes = reconcile(view, base_view={}, delta=delta)
    assert out["beats"][0]["voice_ref"]["uid"] == "n9"
    assert any("类型不一致" in n["why"] for n in notes if n["rule"] == "R0_skipped")


def test_a_malformed_reference_is_left_alone():
    view = _view(beats=[("b1", "甲", (pc.KIND_NARRATION, "n9"))], narr=[("new:n9", "甲来。")])
    view["beats"][0]["voice_ref"]["extra"] = 1        # 非法字段 ⇒ 不修
    delta = {"upsert": {"narration_units": [{"uid": "new:n9", "text": "甲来。"}]}}
    out, _ = reconcile(view, base_view={}, delta=delta)
    assert out["beats"][0]["voice_ref"]["uid"] == "n9"


# ---------------- R1 已停用：正文可拼接也不删 ----------------
def test_text_that_could_be_a_split_is_never_deleted():
    view = _view(beats=[("b1", "甲", (pc.KIND_NARRATION, "n1")),
                        ("b2", "乙", (pc.KIND_NARRATION, "n2"))],
                 narr=[("n1", "甲走。"), ("n2", "乙留。"), ("n3", "甲走。乙留。")])
    out, notes = reconcile(view, base_view={}, delta={})
    assert [u["uid"] for u in out["narration_units"]] == ["n1", "n2", "n3"]
    assert not any(n["rule"].startswith("R1") for n in notes)


def test_an_empty_delta_never_triggers_deletion():
    view = _view(beats=[("b1", "甲", (pc.KIND_NARRATION, "n1"))],
                 narr=[("n1", "甲走。"), ("n2", "甲走。乙留。")])
    out, _ = reconcile(view, base_view={}, delta={"upsert": {}})
    assert len(out["narration_units"]) == 2


def test_unrelated_edits_never_trigger_deletion():
    view = _view(beats=[("b1", "甲", (pc.KIND_NARRATION, "n1"))],
                 narr=[("n1", "甲走。"), ("n2", "甲走。乙留。")])
    delta = {"upsert": {"beats": [{"uid": "b1", "text": "改了画面", "voice_ref":
                                   {"kind": pc.KIND_NARRATION, "uid": "n1"}}]}}
    out, _ = reconcile(view, base_view={}, delta=delta)
    assert len(out["narration_units"]) == 2


# ---------------- 引用保护：钩子/事实 ----------------
def test_a_unit_spoken_by_a_hook_is_kept():
    view = _view(beats=[("b1", "甲", (pc.KIND_NARRATION, "n1"))],
                 narr=[("n1", "甲走。"), ("n2", "甲走。乙留。")],
                 hooks={"spoken_uids": ["n2"], "carriers": []})
    out, _ = reconcile(view, base_view={}, delta={})
    assert {u["uid"] for u in out["narration_units"]} == {"n1", "n2"}
    assert out["hook_bindings"]["spoken_uids"] == ["n2"]


def test_a_unit_carried_by_a_fact_is_kept():
    view = _view(beats=[("b1", "甲", (pc.KIND_NARRATION, "n1"))],
                 narr=[("n1", "甲走。"), ("n2", "甲走。乙留。")],
                 facts=[{"fact_uid": "f1", "carriers": [{"kind": pc.KIND_NARRATION,
                                                         "uid": "n2"}]}])
    out, _ = reconcile(view, base_view={}, delta={})
    assert {u["uid"] for u in out["narration_units"]} == {"n1", "n2"}
    assert out["fact_bindings"][0]["carriers"][0]["uid"] == "n2"


def test_hook_spoken_uids_reference_dialogue_and_are_repaired():
    """`hook_bindings.spoken_uids` 引用的是**对白**：漏前缀要按对白修。"""
    view = _view(beats=[], narr=[], dial=[("new:d9", "甲说：走。")],
                 hooks={"spoken_uids": ["d9"], "carriers": []})
    delta = {"upsert": {"dialogue_units": [{"uid": "new:d9", "text": "甲说：走。"}]}}
    out, notes = reconcile(view, base_view={}, delta=delta)
    assert out["hook_bindings"]["spoken_uids"] == ["new:d9"]
    assert any(n["where"] == "hook_bindings.spoken_uids" for n in notes)


def test_a_narration_cannot_stand_in_for_the_hook_dialogue():
    """旁白声明不能拿来补钩子对白的引用（类型不符 ⇒ 不动，交钩子判据拒收）。"""
    view = _view(beats=[], narr=[("new:n9", "甲来。")], dial=[],
                 hooks={"spoken_uids": ["n9"], "carriers": []})
    delta = {"upsert": {"narration_units": [{"uid": "new:n9", "text": "甲来。"}]}}
    out, notes = reconcile(view, base_view={}, delta=delta)
    assert out["hook_bindings"]["spoken_uids"] == ["n9"]
    assert any(n["rule"] == "R0_skipped" and "类型不一致" in n["why"] for n in notes)


def test_hook_carriers_use_the_sound_reference_shape():
    view = _view(beats=[], narr=[("new:n9", "甲来。")], dial=[],
                 hooks={"spoken_uids": [],
                        "carriers": [{"kind": pc.KIND_NARRATION, "uid": "n9"}]})
    delta = {"upsert": {"narration_units": [{"uid": "new:n9", "text": "甲来。"}]}}
    out, notes = reconcile(view, base_view={}, delta=delta)
    assert out["hook_bindings"]["carriers"][0]["uid"] == "new:n9"
    assert any(n["where"] == "hook_bindings.carriers" for n in notes)


def test_illegal_members_are_never_coerced():
    """`spoken_uids` 只处理字符串成员；非法成员原样保留（不做 str() 强转）。"""
    view = _view(beats=[], narr=[], dial=[("new:d9", "甲说：走。")],
                 hooks={"spoken_uids": [9, "", None, "d9"], "carriers": []})
    delta = {"upsert": {"dialogue_units": [{"uid": "new:d9", "text": "甲说：走。"}]}}
    out, _ = reconcile(view, base_view={}, delta=delta)
    assert out["hook_bindings"]["spoken_uids"] == [9, "", None, "new:d9"]


def test_mixed_reference_fields_are_left_untouched():
    """载体混入 `beat_uid`/`card_id` 等字段 ⇒ 形状非法，原样保留不补前缀。"""
    view = _view(beats=[], narr=[("new:n9", "甲来。")], dial=[],
                 facts=[{"fact_uid": "f1",
                         "carriers": [{"kind": pc.KIND_NARRATION, "uid": "n9",
                                       "beat_uid": "b1"}]}])
    delta = {"upsert": {"narration_units": [{"uid": "new:n9", "text": "甲来。"}]}}
    out, _ = reconcile(view, base_view={}, delta=delta)
    assert out["fact_bindings"][0]["carriers"][0] == {"kind": pc.KIND_NARRATION,
                                                      "uid": "n9", "beat_uid": "b1"}


# ---------------- 幂等与输入保护 ----------------
def test_repeated_application_is_idempotent_and_does_not_touch_input():
    view = _view(beats=[("b1", "甲", (pc.KIND_NARRATION, "n9"))], narr=[("new:n9", "甲来。")])
    snapshot = _hashes(view)
    delta = {"upsert": {"narration_units": [{"uid": "new:n9", "text": "甲来。"}]}}
    once, _ = reconcile(view, base_view={}, delta=delta)
    twice, notes2 = reconcile(once, base_view={}, delta=delta)
    assert _hashes(once) == _hashes(twice)
    assert notes2 == [] or all(n["rule"] == "R0_skipped" for n in notes2)
    assert _hashes(view) == snapshot                 # 原始输入未被修改


def test_split_diagnostics_never_modify_the_candidate():
    view = _view(beats=[("b1", "甲", (pc.KIND_NARRATION, "n1")),
                        ("b2", "乙", (pc.KIND_NARRATION, "n2"))],
                 narr=[("n1", "甲走。"), ("n2", "乙留。"), ("n3", "甲走。乙留。")])
    snapshot = _hashes(view)
    diag = diagnose_splits(view)
    assert diag and diag[0]["uid"] == "n3"
    assert _hashes(view) == snapshot


# ---------------- R1'：基线权威的重复归并 ----------------
def _merged(new_uid, new_text, kind, *, extra_narr=(), extra_dial=(), beats=(), facts=(),
            hooks=None, source=None):
    """合并后的视图：基线单元（`extra_*`，`(uid, text)`）+ 本轮新增单元."""
    unit = {"uid": new_uid, "text": new_text}
    if source is not None:
        unit["source"] = {"text": source}
    view = {
        "beats": [{"uid": u, "text": t,
                   "voice_ref": ({"kind": vr[0], "uid": vr[1]} if vr and vr[1] else None)}
                  for u, t, vr in beats],
        "narration_units": [{"uid": u, "text": t} for u, t in extra_narr],
        "dialogue_units": [{"uid": u, "text": t} for u, t in extra_dial],
        "fact_bindings": list(facts),
    }
    view["narration_units" if kind == pc.KIND_NARRATION else "dialogue_units"].append(unit)
    if hooks is not None:
        view["hook_bindings"] = hooks
    return view


def _upsert(uid, text, kind, source=None):
    unit = {"uid": uid, "text": text}
    if source is not None:
        unit["source"] = {"text": source}
    key = "narration_units" if kind == pc.KIND_NARRATION else "dialogue_units"
    return {"upsert": {key: [unit]}}


def test_a_new_unit_that_repeats_a_baseline_line_is_merged_back():
    base = _view(beats=[], narr=[("n5", "甲走。")])
    view = _merged("new:n9", "甲走。", pc.KIND_NARRATION,
                   extra_narr=[("n5", "甲走。")],
                   beats=[("b1", "甲", (pc.KIND_NARRATION, "new:n9"))])
    out, notes = reconcile(view, base_view=base, delta=_upsert("new:n9", "甲走。",
                                                               pc.KIND_NARRATION))
    assert out["beats"][0]["voice_ref"] == {"kind": pc.KIND_NARRATION, "uid": "n5"}
    assert [u["uid"] for u in out["narration_units"]] == ["n5"]
    assert [n["rule"] for n in notes] == ["R1p_exact_duplicate"]


def test_a_repeat_can_restore_the_baseline_kind_across_collections():
    """跨集合逐字重复 ⇒ 以**基线已定的类型**改指（单列 `R1p_type_restored`）。"""
    base = _view(beats=[], dial=[("d2", "甲说：走。")])
    view = _merged("new:n9", "甲说：走。", pc.KIND_NARRATION,
                   extra_dial=[("d2", "甲说：走。")],
                   beats=[("b1", "甲", (pc.KIND_NARRATION, "new:n9"))])
    out, notes = reconcile(view, base_view=base, delta=_upsert("new:n9", "甲说：走。",
                                                               pc.KIND_NARRATION))
    assert out["beats"][0]["voice_ref"] == {"kind": pc.KIND_DIALOGUE, "uid": "d2"}
    assert not out["narration_units"]
    assert [n["rule"] for n in notes] == ["R1p_type_restored"]


def test_a_repeat_of_another_new_unit_is_not_merged():
    """目标必须是**基线**单元：两个本轮新单元正文相同 ⇒ 不动（没有可依据的既成身份）。"""
    base = _view(beats=[], narr=[])
    view = _merged("new:n9", "甲走。", pc.KIND_NARRATION,
                   extra_narr=[("new:n10", "甲走。")],
                   beats=[("b1", "甲", (pc.KIND_NARRATION, "new:n9"))])
    delta = {"upsert": {"narration_units": [{"uid": "new:n9", "text": "甲走。"},
                                           {"uid": "new:n10", "text": "甲走。"}]}}
    out, notes = reconcile(view, base_view=base, delta=delta)
    assert len(out["narration_units"]) == 2
    assert out["beats"][0]["voice_ref"]["uid"] == "new:n9"
    assert not notes


def test_a_non_unique_baseline_text_is_not_merged():
    base = _view(beats=[], narr=[("n5", "甲走。"), ("n6", "甲走。")])
    view = _merged("new:n9", "甲走。", pc.KIND_NARRATION,
                   extra_narr=[("n5", "甲走。"), ("n6", "甲走。")],
                   beats=[("b1", "甲", (pc.KIND_NARRATION, "new:n9"))])
    out, notes = reconcile(view, base_view=base, delta=_upsert("new:n9", "甲走。",
                                                               pc.KIND_NARRATION))
    assert out["beats"][0]["voice_ref"]["uid"] == "new:n9"
    assert len(out["narration_units"]) == 3
    assert [n["rule"] for n in notes] == ["R1p_skipped"]


def test_a_reference_that_cannot_be_rewritten_blocks_the_merge():
    """钩子 `spoken_uids` 只能指对白：目标是旁白 ⇒ 整条放弃，不删单元。"""
    base = _view(beats=[], narr=[("n5", "甲走。")])
    view = _merged("new:n9", "甲走。", pc.KIND_NARRATION,
                   extra_narr=[("n5", "甲走。")],
                   beats=[("b1", "甲", (pc.KIND_NARRATION, "new:n9"))],
                   hooks={"spoken_uids": ["new:n9"], "carriers": []})
    out, notes = reconcile(view, base_view=base, delta=_upsert("new:n9", "甲走。",
                                                               pc.KIND_NARRATION))
    assert len(out["narration_units"]) == 2          # 单元仍在，未删
    assert out["hook_bindings"]["spoken_uids"] == ["new:n9"]
    assert [n["rule"] for n in notes] == ["R1p_skipped"]


def test_a_source_text_mismatch_blocks_the_merge():
    base = _view(beats=[], narr=[("n5", "甲走。")])
    base["narration_units"][0]["source"] = {"text": "原文一"}
    view = _merged("new:n9", "甲走。", pc.KIND_NARRATION,
                   extra_narr=[("n5", "甲走。")],
                   beats=[("b1", "甲", (pc.KIND_NARRATION, "new:n9"))],
                   source="原文二")
    out, notes = reconcile(view, base_view=base,
                           delta=_upsert("new:n9", "甲走。", pc.KIND_NARRATION,
                                         source="原文二"))
    assert len(out["narration_units"]) == 2
    assert [n["rule"] for n in notes] == ["R1p_skipped"]


def test_a_zero_reference_orphan_covered_by_a_played_unit_is_removed():
    base = _view(beats=[], narr=[("n5", "甲先走过去。")])
    view = _merged("new:n9", "他回头。甲先走过去。", pc.KIND_NARRATION,
                   extra_narr=[("n5", "甲先走过去。")],
                   beats=[("b1", "甲", (pc.KIND_NARRATION, "new:n9"))])
    out, notes = reconcile(view, base_view=base,
                           delta=_upsert("new:n9", "他回头。甲先走过去。", pc.KIND_NARRATION))
    assert [u["uid"] for u in out["narration_units"]] == ["new:n9"]
    assert any(n["rule"] == "R1pp_covered_orphan" for n in notes)


def test_a_covered_orphan_of_another_kind_is_never_removed():
    """对白正文出现在旁白单元里 ⇒ 不删：那会换掉说话人。"""
    base = _view(beats=[], dial=[("d2", "甲说：走。")])
    view = _merged("new:n9", "他回头说。甲说：走。", pc.KIND_NARRATION,
                   extra_dial=[("d2", "甲说：走。")],
                   beats=[("b1", "甲", (pc.KIND_NARRATION, "new:n9"))])
    out, notes = reconcile(view, base_view=base,
                           delta=_upsert("new:n9", "他回头说。甲说：走。",
                                         pc.KIND_NARRATION))
    assert [u["uid"] for u in out["dialogue_units"]] == ["d2"]
    assert not any(n["rule"] == "R1pp_covered_orphan" for n in notes)


def test_a_carried_orphan_is_never_removed():
    base = _view(beats=[], narr=[("n5", "甲走。")],
                 facts=[{"fact_uid": "f1", "carriers": [{"kind": pc.KIND_NARRATION,
                                                         "uid": "n5"}]}])
    view = _merged("new:n9", "甲先走。甲走。", pc.KIND_NARRATION,
                   extra_narr=[("n5", "甲走。")],
                   beats=[("b1", "甲", (pc.KIND_NARRATION, "new:n9"))],
                   facts=[{"fact_uid": "f1", "carriers": [{"kind": pc.KIND_NARRATION,
                                                           "uid": "n5"}]}])
    out, notes = reconcile(view, base_view=base, delta=_upsert("new:n9", "甲先走。甲走。",
                                                               pc.KIND_NARRATION))
    assert {u["uid"] for u in out["narration_units"]} == {"n5", "new:n9"}
    assert not any(n["rule"] == "R1pp_covered_orphan" for n in notes)


def test_a_duplicate_is_never_merged_into_a_unit_this_delta_deletes():
    """目标若在本轮 `delete` 里 ⇒ 不能改指（改指 = 悬空；实跑抓到过的真 bug）."""
    base = _view(beats=[], dial=[("d1", "甲说：走。")])
    view = _merged("new:n9", "甲说：走。", pc.KIND_NARRATION,
                   extra_dial=[("d1", "甲说：走。")],
                   beats=[("b1", "甲", (pc.KIND_NARRATION, "new:n9"))])
    delta = {"upsert": {"narration_units": [{"uid": "new:n9", "text": "甲说：走。"}]},
             "delete": {"dialogue_units": ["d1"]}}
    out, notes = reconcile(view, base_view=base, delta=delta)
    assert out["beats"][0]["voice_ref"]["uid"] == "new:n9", "改指到本轮要删的单元 = 悬空"
    assert {u["uid"] for u in out["dialogue_units"]} == {"d1"}
    assert not [n for n in notes if n["rule"] in ("R1p_exact_duplicate", "R1p_type_restored")]


def test_repeated_reconciliation_is_idempotent():
    base = _view(beats=[], narr=[("n5", "甲走。")])
    view = _merged("new:n9", "甲走。", pc.KIND_NARRATION,
                   extra_narr=[("n5", "甲走。")],
                   beats=[("b1", "甲", (pc.KIND_NARRATION, "new:n9"))])
    delta = _upsert("new:n9", "甲走。", pc.KIND_NARRATION)
    once, _ = reconcile(view, base_view=base, delta=delta)
    twice, notes2 = reconcile(once, base_view=base, delta=delta)
    assert _hashes(once) == _hashes(twice)
    assert not [n for n in notes2
                if n["rule"] in ("R1p_exact_duplicate", "R1p_type_restored",
                                 "R1pp_covered_orphan")]


# ---------------- R0 全 kind 通用化（2026-11-15，真实调用第 3 轮复现）----------------
def test_visual_voice_ref_missing_prefix_is_repaired():
    """真实第 3 轮：beats[new:b08].voice_ref {kind:visual, uid:'b08'} 指自己刚
    upsert 的 new:b08——visual 引用此前不在 R0 覆盖内，被悬空判据拒收。"""
    base = _view(beats=[("b01", "一", None)], narr=[])
    view = copy.deepcopy(base)
    view["beats"].append({"uid": "new:b08", "text": "二",
                          "voice_ref": {"kind": pc.KIND_VISUAL, "uid": "b08"}})
    delta = {"upsert": {"beats": [{"uid": "new:b08", "text": "二",
                                   "voice_ref": {"kind": pc.KIND_VISUAL, "uid": "b08"}}]}}
    out, notes = reconcile(view, base_view=base, delta=delta)
    assert out["beats"][1]["voice_ref"] == {"kind": pc.KIND_VISUAL, "uid": "new:b08"}
    assert [n["rule"] for n in notes] == ["R0_temp_prefix"]
    assert notes[0]["where"] == "beats[new:b08].voice_ref"


def test_visual_reference_to_baseline_beat_is_untouched():
    """基线已有的节拍 id 是正常引用，不改写。"""
    view = _view(beats=[("b1", "甲", (pc.KIND_VISUAL, "b0"))],
                 narr=[])
    view["beats"].append({"uid": "b0", "text": "底", "voice_ref": None})
    out, notes = reconcile(view, base_view={}, delta={"upsert": {}})
    assert out["beats"][0]["voice_ref"] == {"kind": pc.KIND_VISUAL, "uid": "b0"}
    assert not [n for n in notes if n["rule"] == "R0_temp_prefix"]


def test_visual_kind_mismatch_is_not_repaired():
    """引用 kind=visual 但只声明了旁白临时单元 ⇒ 不动（不能跨集合猜类型）。"""
    view = _view(beats=[("b1", "甲", (pc.KIND_VISUAL, "n9"))],
                 narr=[("new:n9", "甲来。")])
    delta = {"upsert": {"narration_units": [{"uid": "new:n9", "text": "甲来。"}]}}
    out, notes = reconcile(view, base_view={}, delta=delta)
    assert out["beats"][0]["voice_ref"]["uid"] == "n9"
    assert any(n["rule"] == "R0_skipped" and "类型不一致" in n["why"] for n in notes)


def test_visual_ref_in_beat_uid_form_is_repaired_on_its_own_key():
    """visual 引用按规范键 `beat_uid` 写（外部形状）⇒ 修复写在原键上，不归一写法。"""
    view = {"beats": [{"uid": "new:b08", "text": "二", "voice_ref":
                       {"kind": pc.KIND_VISUAL, "beat_uid": "b08"}}],
            "narration_units": [], "dialogue_units": [], "fact_bindings": []}
    delta = {"upsert": {"beats": [{"uid": "new:b08", "text": "二"}]}}
    out, notes = reconcile(view, base_view={}, delta=delta)
    assert out["beats"][0]["voice_ref"] == {"kind": pc.KIND_VISUAL, "beat_uid": "new:b08"}
    assert [n["rule"] for n in notes] == ["R0_temp_prefix"]


def test_card_beat_uid_missing_prefix_is_repaired():
    """卡片挂在刚 upsert 的节拍上漏 `new:` 前缀 ⇒ R0 新增位置：screen_cards[].beat_uid。"""
    view = {"beats": [{"uid": "new:b08", "text": "二", "voice_ref": None}],
            "narration_units": [], "dialogue_units": [],
            "screen_cards": [{"card_id": "c1", "beat_uid": "b08"}],
            "fact_bindings": []}
    delta = {"upsert": {"beats": [{"uid": "new:b08", "text": "二"}],
                        "screen_cards": [{"card_id": "c1", "beat_uid": "b08"}]}}
    out, notes = reconcile(view, base_view={}, delta=delta)
    assert out["screen_cards"][0]["beat_uid"] == "new:b08"
    assert any(n["where"] == "screen_cards[c1].beat_uid" and n["rule"] == "R0_temp_prefix"
               for n in notes)


def test_card_beat_uid_with_baseline_beat_is_untouched():
    """卡片挂在基线节拍上是正常引用。"""
    base = {"beats": [{"uid": "b1", "text": "甲", "voice_ref": None}],
            "narration_units": [], "dialogue_units": [],
            "screen_cards": [{"card_id": "c1", "beat_uid": "b1"}], "fact_bindings": []}
    out, notes = reconcile(copy.deepcopy(base), base_view=base, delta={"upsert": {}})
    assert out["screen_cards"][0]["beat_uid"] == "b1"
    assert not [n for n in notes if n["rule"] == "R0_temp_prefix"]


def test_visual_carrier_in_fact_bindings_is_repaired():
    """事实载体 kind=visual 漏前缀同样修（载体四种 kind 全覆盖）。"""
    view = {"beats": [{"uid": "new:b08", "text": "二", "voice_ref": None}],
            "narration_units": [], "dialogue_units": [],
            "fact_bindings": [{"fact_uid": "f1",
                               "carriers": [{"kind": pc.KIND_VISUAL, "uid": "b08"}]}]}
    delta = {"upsert": {"beats": [{"uid": "new:b08", "text": "二"}]}}
    out, notes = reconcile(view, base_view={}, delta=delta)
    assert out["fact_bindings"][0]["carriers"][0] == {"kind": pc.KIND_VISUAL,
                                                      "uid": "new:b08"}
    assert any(n["where"] == "fact_bindings[f1]" and n["rule"] == "R0_temp_prefix"
               for n in notes)


def test_repair_of_visual_ref_is_idempotent():
    """修过一遍后再跑：`new:b08` 已是合并视图身份 ⇒ 不再记事（幂等）。"""
    base = _view(beats=[("b01", "一", None)], narr=[])
    view = copy.deepcopy(base)
    view["beats"].append({"uid": "new:b08", "text": "二",
                          "voice_ref": {"kind": pc.KIND_VISUAL, "uid": "b08"}})
    delta = {"upsert": {"beats": [{"uid": "new:b08", "text": "二"}]}}
    once, _ = reconcile(view, base_view=base, delta=delta)
    twice, notes2 = reconcile(once, base_view=base, delta=delta)
    assert _hashes(once) == _hashes(twice)
    assert not [n for n in notes2 if n["rule"] == "R0_temp_prefix"]
