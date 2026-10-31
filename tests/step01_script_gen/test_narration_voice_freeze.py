# -*- coding: utf-8 -*-
"""R06：缺省旁白的**有效声源**冻结（2026-10-30）.

被修的行为：冻结判据此前按「有没有写 voice_role 字段」筛选，于是**缺省旁白**
（字段缺席 = narrator）根本不进冻结集合——修订把它改成角色发声，守卫直接放行；
反向地，显式 narrator 与省略字段这两种**等价表示**又会被按原值比较误报换声源。

样本只看契约，不绑定任何作品：两组匿名的旧模式视图，长度、事实数与声音分布不同。
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest

from shared import presentation_contract as pc
from shared.narration_voice import narration_role
from step01_script_gen import outline as o


def _view(facts, units):
    """把「事实 → 旁白」的一一对应装配成一份旧模式视图（引用与绑定都闭合）."""
    beats, bindings, spoken = [], [], []
    for _i, (_uid, _text, _fact) in enumerate(units, 1):
        _b = "b%02d" % _i
        beats.append({"uid": _b, "event_uids": [_fact], "text": "动作%d" % _i,
                      "purpose": "推进", "evidence": _text,
                      "voice_ref": {"kind": pc.KIND_NARRATION, "uid": _uid}})
        bindings.append({"fact_uid": _fact,
                         "carriers": [{"kind": pc.KIND_NARRATION, "uid": _uid}]})
        spoken.append({"uid": _uid, "text": _text, "source": {"text": _text}})
    return {
        "schema": pc.PRESENTATION_SCHEMA, "mode": pc.MODE_LEGACY,
        "frozen_facts": [{"uid": f, "claim": c, "evidence": e} for f, c, e in facts],
        "frozen_events": [c for _f, c, _e in facts],
        "beats": beats, "dialogue_units": [],
        "narration_units": spoken,
        "screen_cards": [], "fact_bindings": bindings,
        "hook_bindings": {"spoken_uids": [], "carrier": None},
        "evidence_updates": [],
    }


#: 样本 A：两条旁白，一条**缺省**（narrator）、一条显式角色；短句。
_SAMPLE_A = _view(
    [("f01", "甲递辞呈", "“甲递了辞呈。”"), ("f02", "乙接管", "“乙随后接管。”")],
    [("n01", "甲递了辞呈。", "f01"),
     ("n02", "乙随后接管。", "f02")],
)
_SAMPLE_A["narration_units"][1]["voice_role"] = "阿禾"

#: 样本 B：三条旁白，两条**缺省**、一条显式角色；事实与句子都更长，分布不同。
_SAMPLE_B = _view(
    [("f01", "甲交账", "“甲把账册推到灯下，一言不发。”"),
     ("f02", "丙抄录", "“丙就着那盏灯，一行一行抄到天亮。”"),
     ("f03", "乙封箱", "“乙把抄本封进木箱，落了锁。”")],
    [("n01", "甲把账册推到灯下，一言不发。", "f01"),
     ("n02", "丙就着那盏灯，一行一行抄到天亮。", "f02"),
     ("n03", "乙把抄本封进木箱，落了锁。", "f03")],
)
_SAMPLE_B["narration_units"][2]["voice_role"] = "老周"

_SAMPLES = {"A": _SAMPLE_A, "B": _SAMPLE_B}


def _base(sample) -> dict:
    plan = pc.plan_from_view(copy.deepcopy(sample))
    view, probs = pc.view_from_plan(plan)
    assert view is not None, [p["code"] for p in probs]
    assert [p["code"] for p in probs] == [], probs
    assert pc.view_problems(view) == [], pc.view_problems(view)
    return view


def _response(base: dict, view: dict) -> dict:
    return {"schema": pc.REVISION_SCHEMA,
            "rule_version": o.PLAN_VIEW_RULE_VERSION,
            "base_plan_hash": pc.view_digest(base),
            "fact_basis_hash": pc.fact_basis_digest(base),
            "view": view}


def _apply(base: dict, view: dict):
    return pc.apply_editable_view(base, _response(base, view),
                                  expected_rule_version=o.PLAN_VIEW_RULE_VERSION)


def _codes(probs) -> list:
    return [str(p.get("code") or "") for p in probs]


def _unit(view: dict, uid: str) -> dict:
    return [u for u in view["narration_units"] if u.get("uid") == uid][0]


# ---------------------------------------------------------------------------
# ① 契约对照：缺席与显式 narrator 等价，其余非法值逐项报错
# ---------------------------------------------------------------------------
def test_absence_means_narrator_and_explicit_narrator_is_the_same_voice():
    assert narration_role({"uid": "n01", "text": "x"}) == "narrator"
    assert narration_role({"uid": "n01", "text": "x", "voice_role": "narrator"}) == "narrator"
    assert narration_role({"uid": "n01", "voice_role": "阿禾"}) == "阿禾"


@pytest.mark.parametrize("bad", [None, "", "   ", 7, ["narrator"]])
def test_invalid_explicit_values_are_rejected_with_unit_identity(bad):
    """非法值报错必须带**单元身份与字段路径**，不能只抛一条没有定位的 ValueError."""
    view = _base(_SAMPLE_A)
    view["narration_units"][0]["voice_role"] = bad
    with pytest.raises(ValueError) as exc:
        pc.narration_voice_roles(view)
    text = str(exc.value)
    assert "narration_units[%s].voice_role" % view["narration_units"][0]["uid"] in text, text


@pytest.mark.parametrize("name", ["A", "B"])
def test_missing_field_survives_the_plan_round_trip(name):
    """冻结**有效声源**，不为冻结把缺席字段强行写进原始数据（表示兼容保留）."""
    view = _base(_SAMPLES[name])
    plan = pc.plan_from_view(view)
    by_text = {ln["text"]: ln for ln in plan["narration_lines"]}
    for unit in view["narration_units"]:
        entry = by_text[unit["text"]]
        assert ("voice_role" in entry) == ("voice_role" in unit), (unit, entry)
    assert narration_role(by_text[view["narration_units"][0]["text"]]) == "narrator"


# ---------------------------------------------------------------------------
# ② 请求：冻结集合必须包含**缺省**旁白
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["A", "B"])
def test_revision_request_freezes_default_narrators_too(name):
    view = _base(_SAMPLES[name])
    req = pc.revision_request(view)
    frozen = req["frozen_voice_roles"]
    assert set(frozen) == {u["uid"] for u in view["narration_units"]}, frozen
    assert frozen[view["narration_units"][0]["uid"]] == "narrator", frozen
    # 增量请求与完整视图请求同源：集合不许在两套协议里各自演化
    assert pc.delta_request(view)["frozen_voice_roles"] == frozen


# ---------------------------------------------------------------------------
# ③ 合法路径：声源不变 / 等价表示
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["A", "B"])
def test_unchanged_voice_roles_are_applied_and_reloaded(name):
    base = _base(_SAMPLES[name])
    cand = copy.deepcopy(base)
    # 只改与声音身份无关的证据更正：有效声源不动
    cand["evidence_updates"] = [{"fact_uid": base["frozen_facts"][0]["uid"],
                                 "evidence": base["frozen_facts"][0]["evidence"]}]
    view, probs, result = _apply(base, cand)
    assert result["kind"] == "applied", (result, probs)
    assert _codes(probs) == []
    plan = pc.plan_from_view(view)
    roles = [narration_role(e) for e in plan["narration_lines"]]
    assert roles == [narration_role(u) for u in base["narration_units"]], roles


def test_absent_and_explicit_narrator_are_interchangeable():
    """等价表示不误报换声源：缺席 → 显式 narrator、显式 narrator → 缺席都接受."""
    base = _base(_SAMPLE_A)
    uid = base["narration_units"][0]["uid"]
    assert "voice_role" not in _unit(base, uid)

    cand = copy.deepcopy(base)
    _unit(cand, uid)["voice_role"] = "narrator"
    view, probs, result = _apply(base, cand)
    assert result["kind"] == "applied", (result, _codes(probs))
    assert _unit(view, uid)["voice_role"] == "narrator"

    explicit_base = copy.deepcopy(base)
    _unit(explicit_base, uid)["voice_role"] = "narrator"
    explicit_base_plan = pc.plan_from_view(explicit_base)
    base2, _p2 = pc.view_from_plan(explicit_base_plan)
    assert [p["code"] for p in _p2] == [], _p2
    cand2 = copy.deepcopy(base2)
    del _unit(cand2, uid)["voice_role"]
    view2, probs2, result2 = _apply(base2, cand2)
    assert result2["kind"] == "applied", (result2, _codes(probs2))
    assert narration_role(_unit(view2, uid)) == "narrator"


# ---------------------------------------------------------------------------
# ④ 反例：缺省声源被悄悄改掉
# ---------------------------------------------------------------------------
def test_default_narration_cannot_be_turned_into_a_character():
    base = _base(_SAMPLE_A)
    uid = base["narration_units"][0]["uid"]
    cand = copy.deepcopy(base)
    _unit(cand, uid)["voice_role"] = "阿禾"
    view, probs, result = _apply(base, cand)
    assert view is None and result["kind"] == "illegal", result
    codes = _codes(probs)
    assert "view_voice_role_changed" in codes, codes
    rec = [p for p in probs if p["code"] == "view_voice_role_changed"][0]
    assert rec["ref"] == uid, rec
    assert rec["path"] == "narration_units[%s].voice_role" % uid, rec
    assert "narrator" in rec["reason"] and "阿禾" in rec["reason"], rec


def test_explicit_role_cannot_be_dropped_back_to_narrator():
    base = _base(_SAMPLE_B)
    uid = base["narration_units"][2]["uid"]
    cand = copy.deepcopy(base)
    del _unit(cand, uid)["voice_role"]
    view, probs, result = _apply(base, cand)
    assert view is None and result["kind"] == "illegal", result
    assert "view_voice_role_changed" in _codes(probs), _codes(probs)


def test_role_cannot_switch_to_another_role():
    base = _base(_SAMPLE_A)
    uid = base["narration_units"][1]["uid"]
    cand = copy.deepcopy(base)
    _unit(cand, uid)["voice_role"] = "老周"
    view, probs, result = _apply(base, cand)
    assert view is None and result["kind"] == "illegal", result
    assert "view_voice_role_changed" in _codes(probs), _codes(probs)


def test_delete_and_recreate_with_a_new_uid_is_refused():
    """删除原声音身份、用新 uid 换说话人 = 换声源，不许从新身份绕过去."""
    base = _base(_SAMPLE_A)
    uid = base["narration_units"][0]["uid"]
    cand = copy.deepcopy(base)
    old = _unit(cand, uid)
    cand["narration_units"] = [u for u in cand["narration_units"] if u.get("uid") != uid]
    cand["narration_units"].insert(0, {"uid": "new:v1", "text": old["text"],
                                       "source": old.get("source") or {},
                                       "voice_role": "阿禾"})
    # 结构也要跟着闭合，否则会先在引用层被判悬空——这里要验的正是"结构合法、身份被换掉"。
    for beat in cand["beats"]:
        if (beat.get("voice_ref") or {}).get("uid") == uid:
            beat["voice_ref"] = {"kind": pc.KIND_NARRATION, "uid": "new:v1"}
    for binding in cand["fact_bindings"]:
        binding["carriers"] = [
            {"kind": pc.KIND_NARRATION, "uid": "new:v1"}
            if (c.get("uid") == uid and c.get("kind") == pc.KIND_NARRATION) else c
            for c in binding["carriers"]]
    view, probs, result = _apply(base, cand)
    assert view is None and result["kind"] == "illegal", result
    rec = [p for p in probs if p["code"] == "view_voice_role_changed"]
    assert rec and rec[0]["ref"] == uid, probs
    assert rec[0]["path"] == "narration_units[%s]" % uid, rec[0]


# ---------------------------------------------------------------------------
# ⑤ 增量入口同一条守卫
# ---------------------------------------------------------------------------
def _delta(base: dict, units: list) -> dict:
    return {"schema": pc.DELTA_SCHEMA,
            "rule_version": pc.DELTA_RULE_VERSION,
            "base_plan_hash": pc.view_digest(base),
            "fact_basis_hash": pc.fact_basis_digest(base),
            "upsert": {"narration_units": units}}


def test_delta_cannot_revoice_a_default_narration():
    base = _base(_SAMPLE_A)
    uid = base["narration_units"][0]["uid"]
    unit = copy.deepcopy(_unit(base, uid))
    unit["voice_role"] = "阿禾"
    view, probs, result = pc.apply_delta_view(base, _delta(base, [unit]))
    assert view is None and result["kind"] == "illegal", result
    assert "view_voice_role_changed" in _codes(probs), _codes(probs)


def test_delta_may_state_the_equivalent_explicit_narrator():
    base = _base(_SAMPLE_A)
    uid = base["narration_units"][0]["uid"]
    unit = copy.deepcopy(_unit(base, uid))
    unit["voice_role"] = "narrator"
    view, probs, result = pc.apply_delta_view(base, _delta(base, [unit]))
    assert result["kind"] == "applied", (result, _codes(probs))
    assert narration_role(_unit(view, uid)) == "narrator"



def _reclass_to_dialogue(base: dict, uid: str, *, voice_role=None) -> dict:
    """把一条旁白改挂成**同文本**的对白（既有「旁白→对白」重分类的形状）."""
    cand = copy.deepcopy(base)
    old = _unit(cand, uid)
    cand["narration_units"] = [u for u in cand["narration_units"] if u.get("uid") != uid]
    unit = {"uid": "new:d1", "text": old["text"], "source": {}}
    if voice_role is not None:
        unit["voice_role"] = voice_role
    cand["dialogue_units"] = [unit]
    for beat in cand["beats"]:
        if (beat.get("voice_ref") or {}).get("uid") == uid:
            beat["voice_ref"] = {"kind": pc.KIND_DIALOGUE, "uid": "new:d1"}
    for binding in cand["fact_bindings"]:
        binding["carriers"] = [
            {"kind": pc.KIND_DIALOGUE, "uid": "new:d1"}
            if (c.get("uid") == uid and c.get("kind") == pc.KIND_NARRATION) else c
            for c in binding["carriers"]]
    return cand


def test_narration_to_dialogue_reclass_is_the_one_provable_handoff():
    """既有重分类保留：正文逐字相同、有效声源相同 ⇒ 同一声音换了承载."""
    base = _base(_SAMPLE_A)
    uid = base["narration_units"][0]["uid"]
    view, probs, result = _apply(base, _reclass_to_dialogue(base, uid))
    assert result["kind"] == "applied", (result, _codes(probs))
    assert uid not in [u["uid"] for u in view["narration_units"]]
    assert [u["text"] for u in view["dialogue_units"]] == [base["narration_units"][0]["text"]]


def test_handoff_that_changes_the_speaker_is_refused():
    """同文本但换了说话人的"重分类"仍是换声源，不能借重分类放行."""
    base = _base(_SAMPLE_A)
    uid = base["narration_units"][0]["uid"]
    cand = _reclass_to_dialogue(base, uid, voice_role="阿禾")
    view, probs, result = _apply(base, cand)
    assert view is None and result["kind"] == "illegal", result
    assert "view_voice_role_changed" in _codes(probs), _codes(probs)
