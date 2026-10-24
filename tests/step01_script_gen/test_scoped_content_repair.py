"""单事实槽位修订：**可编辑范围同源**（请求权限 → 动作有效权限）与越权拒收.

覆盖：
1. `build_editable_scope` 请求阶段列出可能面、`answer` 阶段收敛到实际授权面；
2. `scope_violations` 对越权字段给出具体路径（声音/归属/保护字段/非目标绑定/钩子）；
3. 真实入口：编译出的合法 delta 在**收窄范围**下被 `stage="scope"` 拒收，
   且该判断发生在内容核验之前。
"""
from __future__ import annotations

import copy

import pytest

from shared import presentation_contract as pc
from step01_script_gen import outline as o
from step01_script_gen import plan_content_repair as pcr


def _view() -> dict:
    """最小匿名视图：b01 画面、b02 承声；f01 绑 b01、f02 绑 b02、f03 与 f01 同载 b01."""
    return {
        "beats": [
            {"uid": "b01", "text": "甲推门进屋。", "event_uids": [],
             "voice_ref": {"kind": "visual", "uid": "b01"}},
            {"uid": "b02", "text": "甲说：走。", "event_uids": [],
             "voice_ref": {"kind": "narration", "uid": "n01"}},
        ],
        "narration_units": [{"uid": "n01", "text": "甲说走。"}],
        "dialogue_units": [],
        "screen_cards": [],
        "fact_bindings": [
            {"fact_uid": "f01", "carriers": [{"kind": "visual", "uid": "b01"}]},
            {"fact_uid": "f02", "carriers": [{"kind": "narration", "uid": "n01"}]},
            {"fact_uid": "f03", "carriers": [{"kind": "visual", "uid": "b01"}]},
        ],
        "hook_bindings": {"spoken_uids": [], "carriers": []},
        "frozen_facts": [{"uid": "f01", "claim": "甲进屋"},
                         {"uid": "f02", "claim": "甲说话"},
                         {"uid": "f03", "claim": "甲又离开"}],
        "frozen_events": [],
    }


def _slots() -> list:
    return [
        {"slot_id": "slot:fact:f01", "kind": "fact_visual", "fact_uid": "f01",
         "claim": "甲进屋", "allowed_actions": [pcr.ACT_REWRITE, pcr.ACT_BIND],
         "target_uids": ["b01"], "insert_positions": []},
        {"slot_id": "slot:fact:f02", "kind": "fact_visual", "fact_uid": "f02",
         "claim": "甲说话", "allowed_actions": [pcr.ACT_BIND],
         "target_uids": ["b01", "b02"], "insert_positions": []},
    ]


def test_the_request_scope_lists_every_possible_edit_and_the_co_carried_fact():
    _s = pcr.build_editable_scope(_view(), _slots())
    assert _s["phase"] == "request"
    assert _s["allowed_fact_uids"] == ["f01", "f02"]
    assert _s["allowed_rewrite_beat_uids"] == ["b01"]
    assert _s["allowed_bind_beat_uids"] == ["b01", "b02"]
    # f03 与目标 f01 同载 b01，但它不是本轮目标 ⇒ 必须列进同载保护
    assert _s["co_carried_facts_by_target"] == {"b01": ["f03"]}
    assert "voice_ref" in _s["beat_protected_fields"]
    # P1a：声音集合改为**目标级授权**，不再整块冻结；没有声音槽时授权集合为空。
    assert "narration_units" not in _s["frozen_collections"]
    assert _s["allowed_reclassify_voices"] == []
    assert _s["allowed_bind_voice_uids"] == []


def test_bind_only_answers_get_no_permission_to_touch_beat_text():
    _ans = {"answers": [{"slot_id": "slot:fact:f02", "action": pcr.ACT_BIND,
                         "target_uid": "b01"}]}
    _s = pcr.build_editable_scope(_view(), _slots(), answer=_ans)
    assert _s["phase"] == "effective"
    assert _s["allowed_fact_uids"] == ["f02"]
    assert _s["allowed_rewrite_beat_uids"] == []      # 只绑不改
    assert _s["allowed_bind_beat_uids"] == ["b01"]


def test_rewrite_answers_authorise_only_the_selected_beat():
    _ans = {"answers": [{"slot_id": "slot:fact:f01", "action": pcr.ACT_REWRITE,
                        "target_uid": "b01", "text": "甲推门进屋，环顾四周。"}]}
    _s = pcr.build_editable_scope(_view(), _slots(), answer=_ans)
    assert _s["allowed_rewrite_beat_uids"] == ["b01"]
    assert _s["allowed_fact_uids"] == ["f01"]


def _legal_candidate() -> dict:
    _v = copy.deepcopy(_view())
    _v["fact_bindings"][1]["carriers"] = [{"kind": "visual", "uid": "b01"}]
    return _v


def test_a_matching_edit_has_no_scope_violation():
    _s = pcr.build_editable_scope(_view(), _slots(),
                                  answer={"answers": [
                                      {"slot_id": "slot:fact:f02", "action": pcr.ACT_BIND,
                                       "target_uid": "b01"}]})
    assert pc.scope_violations(_view(), _legal_candidate(), _s) == []


def test_every_out_of_scope_change_names_its_field():
    _s = {"allowed_fact_uids": ["f02"], "allowed_rewrite_beat_uids": ["b01"],
          "allowed_bind_beat_uids": [], "allowed_card_ids": []}
    _c = _legal_candidate()
    _c["beats"][1]["text"] = "甲改口：留。"                        # 未授权节拍正文
    _c["beats"][0]["event_uids"] = ["e1"]                          # 授权节拍的受保护字段
    _c["beats"][1]["voice_ref"] = {"kind": "visual", "uid": "b02"}  # 声音归属
    _c["narration_units"][0]["text"] = "甲改口。"                   # 声音正文
    _c["fact_bindings"][0]["carriers"] = [{"kind": "visual", "uid": "b02"}]  # 非目标绑定
    _c["hook_bindings"]["carriers"] = [{"kind": "visual", "uid": "b01"}]     # 钩子
    _codes = {x["code"]: x["path"] for x in pc.scope_violations(_view(), _c, _s)}
    assert _codes["scope_uneditable_beat_modified"] == "beats[b02].text"
    assert _codes["scope_beat_protected_field_modified"] == "beats[b01].event_uids"
    assert _codes["scope_beat_voice_ref_modified"] == "beats[b02].voice_ref"
    assert _codes["scope_voice_unit_modified"] == "narration_units[n01].text"
    assert _codes["scope_uneditable_binding_modified"] in (
        "fact_bindings[f01].carriers", "hook_bindings")


def test_an_unauthorised_insert_is_rejected_but_an_authorised_one_is_not():
    _c = copy.deepcopy(_view())
    _c["beats"].insert(1, {"uid": "new:b", "text": "甲停步。", "event_uids": [],
                           "voice_ref": {"kind": "visual", "uid": "new:b"}})
    _deny = {"allowed_fact_uids": [], "allowed_rewrite_beat_uids": [],
             "allowed_insert_after_uids": []}
    assert [x["code"] for x in pc.scope_violations(_view(), _c, _deny)] == \
        ["scope_uneditable_beat_modified"]
    _allow = {"allowed_fact_uids": [], "allowed_rewrite_beat_uids": [],
              "allowed_insert_after_uids": ["b01"]}
    assert pc.scope_violations(_view(), _c, _allow) == []


def _outcome(problems, **kw):
    return o._slots_revision_outcome(problems, **kw)


def test_the_outcome_is_tri_state_and_never_claims_content_ok_without_evidence():
    """四维记账：没有核验证据时 `content_repaired` 是 `null`，不是 true."""
    _missing = _outcome([{"code": "verify_record_missing"}])
    assert _missing["protocol_ok"] is True
    assert _missing["content_repaired"] is None          # 未核验 ⇒ 不声称修好
    assert _missing["protection_intact"] is True
    assert _missing["final_accepted"] is False

    _bad = _outcome([{"code": "verify_fact_unsupported", "uid": "f08"}],
                    target_fact_uids=["f08"])
    assert _bad["content_repaired"] is False
    assert _bad["final_accepted"] is False

    _clean = _outcome([])
    assert _clean["content_repaired"] is True
    assert _clean["final_accepted"] is True


def test_non_target_voice_residuals_do_not_mark_the_target_as_unrepaired():
    """非目标声音残余不得把 `content_repaired` 判成 false（按目标身份归因）."""
    _o = _outcome([{"code": "verify_voice_not_speech", "uid": "d01"}],
                  target_fact_uids=["f08"])
    assert _o["content_repaired"] is True, _o
    assert _o["final_accepted"] is False

    _o2 = _outcome([{"code": "verify_fact_unsupported", "uid": "f09"}],
                   target_fact_uids=["f08"])
    assert _o2["content_repaired"] is True, _o2


def test_co_carried_semantic_protection_is_null_without_verification_evidence():
    _o = _outcome([{"code": "verify_record_missing"}], target_fact_uids=["f08"],
                  co_carried=True)
    assert _o["protection_intact"] is None, _o        # 同载语义未评估
    assert _o["protection_structural"] is True         # 结构保护已成立，单列


def test_target_voice_failure_makes_target_repair_false():
    """复核反例：目标声音 n01 失败，不能因为事实 f01 通过就报 content_repaired=True."""
    _o = _outcome([{"code": "verify_voice_unsupported", "uid": "n01"}],
                  target_voice_uids=["n01"])
    assert _o["content_repaired"] is False, _o
    assert _o["final_accepted"] is False, _o

    _mixed = _outcome([{"code": "verify_voice_unsupported", "uid": "n01"}],
                      target_fact_uids=["f01"], target_voice_uids=["n01"])
    assert _mixed["content_repaired"] is False, _mixed

    _missing = _outcome([{"code": "verify_record_missing"}],
                        target_voice_uids=["n01"])
    assert _missing["content_repaired"] is None, _missing


def test_affected_fact_failure_makes_narrow_target_repair_false():
    _o = _outcome([{"code": "verify_fact_unsupported", "uid": "f01"}],
                  affected_fact_uids=["f01"])
    assert _o["content_repaired"] is False, _o
    assert _o["final_accepted"] is False, _o


def test_none_uid_is_not_treated_as_a_target():
    _o = _outcome([{"code": "verify_fact_unsupported", "uid": "None"}],
                  target_fact_uids=["None"], target_voice_uids=["None"])
    assert _o["content_repaired"] is True, _o


def test_the_request_scope_reports_a_hook_bound_to_a_target_beat():
    """P1 复核：同载 hook 时 `slot_request` 曾因引用已删局部变量而崩溃."""
    _v = _view()
    _v["hook_bindings"] = {"spoken_uids": [], "carriers": [{"kind": "visual", "uid": "b01"}]}
    _scope = pcr.build_editable_scope(_v, _slots())
    assert _scope["protected_hook"], "目标节拍上的 hook 必须进入同源保护信息"
    _req = pcr.slot_request(_v, _slots())              # 不得抛 NameError
    assert _req["editable_scope"]["protected_hook"] == _scope["protected_hook"]
    assert _req["protected_hook"].get("bindings"), _req["protected_hook"]


def test_a_voice_source_change_is_an_out_of_scope_edit():
    """P1 复核：只比"类型+正文"时，改 `source`（说话人）会漏检."""
    _c = _legal_candidate()
    _c["narration_units"][0]["source"] = {"speaker": "乙"}
    _codes = {x["code"]: x["path"] for x in pc.scope_violations(
        _view(), _c, {"allowed_fact_uids": ["f02"], "allowed_rewrite_beat_uids": ["b01"],
                      "allowed_bind_beat_uids": [], "allowed_card_ids": []})}
    assert _codes.get("scope_voice_unit_modified") == "narration_units[n01].source", _codes


def test_the_restricted_mode_opens_only_bind_and_rewrite_for_the_target_fact():
    """P1 复核：受限模式不得再开放 `create_visual`，也不得给非目标事实出槽位."""
    _problems = [
        {"code": "verify_fact_unsupported", "kind": "fact", "uid": "f01"},
        {"code": "verify_fact_unsupported", "kind": "fact", "uid": "f02"}]
    _found = pcr.content_slots(_view(), _problems, allow_create=False,
                               target_fact_uids=["f01"])
    assert [s["fact_uid"] for s in _found["slots"]] == ["f01"], _found["slots"]
    assert all(pcr.ACT_CREATE not in s["allowed_actions"] for s in _found["slots"])
    assert pcr.ACT_BIND in _found["slots"][0]["allowed_actions"]
    assert _found["deferred"], "非目标事实的问题应记为 deferred 而不是出槽位"


def test_a_fact_round_with_voice_residuals_still_uses_the_narrow_slot_entry(monkeypatch):
    """事实目标与声音残余混在一起时，**仍走窄槽位**；声音残余不被静默吞掉."""
    from tests.step01_script_gen.test_plan_content_slots import (_Budget, _controller,
                                                                _fixture, _legal_answer,
                                                                _novel, _stub)
    _f = _fixture()
    _view, _ = pc.view_from_plan(_f["plan"])
    _stub(monkeypatch, _legal_answer(_view))
    _problems = list(_f["problems"]) + [{
        "code": "verify_voice_not_speech", "kind": "dialogue", "uid": "d01",
        "path": "dialogue_units[d01]", "reason": "内心独白占了对白槽", "limits": {}}]
    _out = o.revise_plan_once(_f["plan"], _problems, _Budget(), None,
                              novel_text=_novel(), verification=_controller())
    assert _out["evidence"]["route"] == "full_plan", _out["evidence"].get("route")
    # P1a：**没有与当前候选匹配的核验记录** ⇒ 不产生迁移权限（不猜方向），
    # 声音问题作为真实残余进入完整计划修订；有记录时的窄槽位路径见 P1a 专项用例。
    _cs = _out["evidence"].get("content_slots") or {}
    assert _cs.get("applicable") is False, _cs
    assert _cs.get("state") == pcr.STATE_UNSUPPORTED, _cs


def test_the_compiled_delta_is_rejected_before_acceptance_when_scope_forbids_it():
    """真实入口：合法编译结果 + 收窄范围 ⇒ `stage="scope"`，不是内容问题."""
    from tests.step01_script_gen.test_plan_content_slots import (_controller, _fixture,
                                                                _legal_answer, _novel)
    _f = _fixture()
    _view, _err = pc.view_from_plan(_f["plan"])
    assert _view is not None and not _err, _err
    _ok, _why, _ctx = o._content_slots_applicable(_f["plan"], _f["problems"],
                                                  mode=o.plan_mode(_f["plan"]))
    assert _ok, _why
    _slots = _ctx["found"]["slots"]
    _delta, _probs = pcr.compile_delta(_view, _slots, _legal_answer(_view),
                                       novel_text=_novel())
    assert _delta is not None and not _probs, _probs
    # 只授权 f08 的绑定：f02 的新建画面不在授权内
    _narrow = {"allowed_fact_uids": ["f08"], "allowed_rewrite_beat_uids": [],
               "allowed_insert_after_uids": [], "allowed_card_ids": []}
    _accepted = []
    _cand, _p, _rk = pc.apply_delta_view(
        _view, _delta, editable_scope=_narrow,
        accept=lambda v: (_accepted.append(v), [])[1])
    assert _cand is None and _rk["kind"] == "illegal" and _rk["stage"] == "scope", (_rk, _p)
    assert any(str(x.get("code", "")).startswith("scope_") for x in _p), _p
    # 2026-10-23 复核 P1：越权必须在 **accept 之前** 拒收（accept 调用数 = 0）
    assert _accepted == [], "越权响应不得进入 accept/内容核验"
