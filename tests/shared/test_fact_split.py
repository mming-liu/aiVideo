# -*- coding: utf-8 -*-
"""P0：稳定事实身份、受控拆分、父义务折叠与投影回读（离线，无模型调用）.

设计见 `docs/aivideo_step01_outline-governance-v9-2026-10-24.md` §4。样本是匿名
最小情节，不依赖 `output/` 或 `.tmp`。
"""
from __future__ import annotations

import copy

import pytest

from shared import content_verification as cv
from shared import fact_split as fs
from shared import presentation_contract as pc
from step01_script_gen import source_revision as sr

NOVEL_DIGEST = "a" * 64
BASE_DIGEST = "b" * 64
CLAIMS = {"f01": "甲进屋", "f02": "甲说话并离开", "f03": "乙留下"}
EVIDENCE = {"f01": "甲进屋。", "f02": "甲说会走。甲离开。", "f03": "乙留下。"}
CORE = {k: ([] if k in ("main_events", "causal_chain", "character_motives") else "")
        for k in sr.CORE_FIELDS}


def _view(*, ledger=None, context=None, table=None, facts=None, bindings=None) -> dict:
    uids = list(table or ["f01", "f02", "f03"])
    rows = facts or [{"uid": u, "claim": CLAIMS[u], "evidence": EVIDENCE[u]}
                     for u in ("f01", "f02", "f03")]
    _bind = bindings or [
        {"fact_uid": "f01", "carriers": [{"kind": "visual", "uid": "b01"}]},
        {"fact_uid": "f02", "carriers": [{"kind": "narration", "uid": "n01"}]},
        {"fact_uid": "f03", "carriers": [{"kind": "visual", "uid": "b02"}]},
    ]
    v = {
        "schema": pc.PRESENTATION_SCHEMA,
        "mode": pc.MODE_LEGACY,
        "event_binding": "unknown",
        "frozen_facts": rows,
        "frozen_events": ["事件1"],
        "beats": [
            {"uid": "b01", "text": "甲推门进屋。", "event_uids": ["事件1"],
             "purpose": "", "evidence": "甲进屋。", "source": {},
             "voice_ref": {"kind": "visual", "uid": "b01"}},
            {"uid": "b02", "text": "甲说完转身离开。", "event_uids": ["事件1"],
             "purpose": "", "evidence": "甲离开。", "source": {},
             "voice_ref": {"kind": "narration", "uid": "n01"}},
        ],
        "dialogue_units": [],
        "narration_units": [{"uid": "n01", "text": "甲说会走。",
                             "source": {"text": "甲说会走。"}}],
        "screen_cards": [],
        "fact_bindings": _bind,
        "hook_bindings": {"spoken_uids": [], "carriers": []},
        "evidence_updates": [],
        "plan_basis": {"fact_uids": uids, "climax": "x",
                       "causal_chain": ["起→承"], "content_note": "匿名样本"},
    }
    if context is not None:
        v["plan_basis"][sr.FIELD] = context
    if ledger is not None:
        v["plan_basis"][fs.CONTEXT_FIELD] = ledger
    return v


def _children() -> list:
    return [
        {"uid": "f02.1", "claim": "甲说话", "evidence": "甲说会走。",
         "carrier": {"kind": "visual", "uid": "b01"}},
        {"uid": "f02.2", "claim": "甲离开", "evidence": "甲离开。",
         "carrier": {"kind": "visual", "uid": "b02"}},
    ]


def _ops(children=None) -> dict:
    return {"parent_uid": "f02", "children": copy.deepcopy(children or _children())}


def _base_plan():
    return pc.plan_from_view(_view())


def _base_digest() -> str:
    return cv.plan_digest_of(_base_plan())


def _record_for(plan=None, *, fact_uid="f02", verdict="partial", spans=None) -> dict:
    _plan = plan or _base_plan()
    _spans = [EVIDENCE[fact_uid]] if spans is None else list(spans)
    _uids = [str(x) for x in (_plan.get("fact_uids") or ["f01", "f02", "f03"])]
    _fact = lambda _u, _v: {"fact_uid": _u, "verdict": _v, "support_spans": [],
                            "missing": "", "suggested_carriers": [], "obligations": []}
    _src = lambda _u, _s: {"fact_uid": _u, "verdict": "supported",
                           "evidence_spans": list(_s), "reason": ""}
    _facts = [_fact(_u, verdict if _u == fact_uid else "supported") for _u in _uids]
    _sources = []
    for _u in _uids:
        if _u == fact_uid:
            _s = _spans
        else:
            _s = [EVIDENCE.get(_u) or EVIDENCE.get(_u.split(".")[0]) or ""]
        _sources.append(_src(_u, _s))
    return {"schema": cv.SCHEMA_VERIFICATION, "status": "ok",
            "plan_digest": cv.plan_digest_of(_plan), "novel_digest": NOVEL_DIGEST,
            "rule_version": cv.DEFAULT_RULE_VERSION,
            "facts": _facts, "source_support": _sources,
            "voice_units": [
                {"unit_uid": "d01", "verdict": "supported", "source_kind": "dialogue",
                 "speaker": "甲", "echo_text": "甲说会走。", "reason": ""},
                {"unit_uid": "n01", "verdict": "supported", "source_kind": "narration",
                 "speaker": "", "echo_text": "甲说会走。", "reason": ""},
            ]}


def _ledger(base_digest=None, *, parent_claim=CLAIMS["f02"], children=None,
            baseline_claim=None, verdict="supported", spans=None,
            carrier=None) -> dict:
    _digest = _base_digest() if base_digest is None else base_digest
    _kids = copy.deepcopy(children or _children())
    if carrier is not None:
        _kids[0]["carrier"] = copy.deepcopy(carrier)
    _spans = [EVIDENCE["f02"]] if spans is None else list(spans)
    return {
        "version": fs.VERSION,
        "novel_digest": NOVEL_DIGEST,
        "base_digest": _digest,
        "splits": [{
            "parent_uid": "f02",
            "parent_claim": parent_claim,
            "parent_evidence": EVIDENCE["f02"],
            "parent_evidence_spans": _spans,
            "baseline_claim": baseline_claim,
            "authority": {"rule_version": cv.DEFAULT_RULE_VERSION, "plan_digest": _digest,
                          "fact_uid": "f02", "code": "verify_fact_unsupported",
                          "verdict": "partial"},
            "children": _kids,
            "narrowed_voices": [],
            "judgment": {"mode": "shadow", "verdict": verdict, "missing": "",
                         "reason": "", "inputs_digest": fs.inputs_digest(parent_claim, _kids),
                         "call_id": "", "prompt_digest": "", "response_digest": ""},
        }],
    }


def _scope() -> dict:
    return {"allowed_split_fact_uids": ["f02"],
            "allowed_split_carrier_uids": ["b01", "b02", "n01"]}


def _apply(**kw):
    _plan = _base_plan()
    _scope_arg = kw.pop("scope", None)
    _ledger_arg = kw.pop("ledger", None)
    _record_arg = kw.pop("record", None)
    return fs.apply_split_view(
        _view(), _ops(),
        scope=_scope() if _scope_arg is None else _scope_arg,
        ledger=_ledger() if _ledger_arg is None else _ledger_arg,
        record=_record_for(_plan) if _record_arg is None else _record_arg, **kw)


# ---- 身份：写表 / 读表 / 失配 ----

def test_legacy_projection_persists_and_reads_fact_uids():
    _plan = pc.plan_from_view(_view())
    assert _plan["fact_uids"] == ["f01", "f02", "f03"]
    _back = pc.view_from_plan(_plan)
    assert [f["uid"] for f in _back[0]["frozen_facts"]] == ["f01", "f02", "f03"]
    assert _back[0]["fact_bindings"][1]["carriers"] == [
        {"kind": "narration", "uid": "n01"}]


@pytest.mark.parametrize("table", [["f01", "f02"], ["f01", "f02", "f02"],
                                   ["f01", "f02", ""]])
def test_a_mismatched_fact_uid_table_is_rejected_not_renumbered(table):
    _plan = pc.plan_from_view(_view())
    _plan["fact_uids"] = table
    _view_out, _problems = pc.view_from_plan(_plan)
    assert _view_out is None
    assert [p["code"] for p in _problems] == ["view_fact_uids_mismatch"]


def test_a_plan_without_the_table_still_reads_positionally():
    _plan = pc.plan_from_view(_view())
    _plan.pop("fact_uids")
    _view_out, _problems = pc.view_from_plan(_plan)
    assert _problems == []
    assert [f["uid"] for f in _view_out["frozen_facts"]] == ["f01", "f02", "f03"]


# ---- 拆分应用与回读 ----

def test_split_replaces_the_parent_in_place_and_keeps_every_other_fact():
    _cand, _problems, _res = _apply()
    assert _res["kind"] == "applied", _res
    assert _problems == []
    assert [f["uid"] for f in _cand["frozen_facts"]] == \
        ["f01", "f02.1", "f02.2", "f03"]
    assert [f["claim"] for f in _cand["frozen_facts"]] == \
        ["甲进屋", "甲说话", "甲离开", "乙留下"]
    assert _cand["fact_bindings"][1]["carriers"] == [
        {"kind": "visual", "uid": "b01"}]
    assert _cand["fact_bindings"][2]["carriers"] == [
        {"kind": "visual", "uid": "b02"}]
    assert _cand["fact_bindings"][0] == _view()["fact_bindings"][0]
    assert _cand["fact_bindings"][3] == _view()["fact_bindings"][2]
    assert fs.load_problems(_cand) == []


def test_the_split_candidate_projects_and_reads_back_with_the_same_identity():
    _cand, _, _ = _apply()
    _plan = pc.plan_from_view(_cand)
    assert _plan["fact_uids"] == ["f01", "f02.1", "f02.2", "f03"]
    _back, _problems = pc.view_from_plan(_plan)
    assert _problems == []
    assert [f["uid"] for f in _back["frozen_facts"]] == _plan["fact_uids"]
    assert [f["claim"] for f in _back["frozen_facts"]] == \
        [f["claim"] for f in _cand["frozen_facts"]]
    assert _back["fact_bindings"] == _cand["fact_bindings"]
    assert _back["plan_basis"][fs.CONTEXT_FIELD] == _cand["plan_basis"][fs.CONTEXT_FIELD]


def test_a_second_split_keeps_the_first_ledger_entry_and_stable_uids():
    _cand, _, _ = _apply()
    _plan = pc.plan_from_view(_cand)
    _digest = cv.plan_digest_of(_plan)
    _kids = [{"uid": "f03.1", "claim": "乙留下", "evidence": "乙留下。",
              "carrier": {"kind": "visual", "uid": "b02"}},
             {"uid": "f03.2", "claim": "乙仍留下", "evidence": "乙留下。",
              "carrier": {"kind": "visual", "uid": "b01"}}]
    _first = _cand["plan_basis"][fs.CONTEXT_FIELD]["splits"][0]
    _second = dict(copy.deepcopy(_first), parent_uid="f03", parent_claim="乙留下",
                   parent_evidence="乙留下。", parent_evidence_spans=["乙留下。"],
                   children=_kids,
                   authority=dict(_first["authority"], fact_uid="f03", plan_digest=_digest),
                   judgment={"mode": "shadow", "verdict": "supported", "missing": "",
                             "reason": "", "inputs_digest": fs.inputs_digest("乙留下", _kids),
                             "call_id": "", "prompt_digest": "", "response_digest": ""})
    _ledger2 = {"version": fs.VERSION, "novel_digest": NOVEL_DIGEST,
                "base_digest": _digest,
                "splits": [copy.deepcopy(_first), _second]}
    _ops2 = {"parent_uid": "f03", "children": _kids}
    _cand2, _problems, _res = fs.apply_split_view(
        _cand, _ops2, scope={"allowed_split_fact_uids": ["f03"],
                             "allowed_split_carrier_uids": ["b01", "b02"]},
        ledger=_ledger2,
        record=_record_for(_plan, fact_uid="f03",
                           spans=[EVIDENCE["f03"]]))
    assert _res["kind"] == "applied", (_res, _problems)
    assert [f["uid"] for f in _cand2["frozen_facts"]] == \
        ["f01", "f02.1", "f02.2", "f03.1", "f03.2"]
    assert fs.load_problems(_cand2) == []


# ---- 折叠准入 ----

def _context(*, targets=("f01",), baseline_claim=CLAIMS["f02"]) -> dict:
    return {"version": sr.VERSION_V2, "novel_digest": NOVEL_DIGEST,
            "base_digest": BASE_DIGEST, "targets": list(targets),
            "baseline_facts": [{"uid": u, "claim": CLAIMS[u]} for u in ("f01", "f02", "f03")],
            "baseline_core": copy.deepcopy(CORE)}


def _with_context(cand, **kw) -> dict:
    _v = copy.deepcopy(cand)
    _v["plan_basis"][sr.FIELD] = _context(**kw)
    return _v


def test_collapse_lets_the_source_admission_read_the_split_candidate():
    _cand, _, _ = _apply()
    _led = _cand["plan_basis"][fs.CONTEXT_FIELD]["splits"][0]
    _led["baseline_claim"] = CLAIMS["f02"]
    _v = _with_context(_cand)
    assert sr.admission_problems(_v) == []
    _collapsed, _problems = fs.collapse(_v)
    assert _problems == []
    assert _collapsed == {"f01": "甲进屋", "f02": "甲说话并离开", "f03": "乙留下"}


def test_a_non_target_parent_cannot_launder_a_claim_change_through_the_ledger():
    _cand, _, _ = _apply()
    _led = _cand["plan_basis"][fs.CONTEXT_FIELD]["splits"][0]
    _led["baseline_claim"] = CLAIMS["f02"]
    _led["parent_claim"] = "甲说话并走开"
    _led["judgment"]["inputs_digest"] = fs.inputs_digest(
        "甲说话并走开", _led["children"])
    _codes = [p["code"] for p in sr.admission_problems(_with_context(_cand))]
    assert _codes == ["verify_fact_split_context"]


def test_deleting_the_ledger_leaves_the_children_to_the_source_rules():
    """账本整体缺席时，子 uid 的合法性交回 v1/v2：非目标子事实被 v2 拒。"""
    _cand, _, _ = _apply()
    _v = copy.deepcopy(_cand)
    _v["plan_basis"].pop(fs.CONTEXT_FIELD)
    _v["plan_basis"][sr.FIELD] = _context()
    assert fs.load_problems(_v) == []
    assert [p["code"] for p in sr.admission_problems(_v)] == \
        ["verify_source_revision_context"]


def test_a_tampered_child_claim_is_rejected_by_the_ledger():
    _cand, _, _ = _apply()
    _v = copy.deepcopy(_cand)
    _v["frozen_facts"][1]["claim"] = "甲喊话"
    assert "split_child_claim_mismatch" in [p["code"] for p in fs.load_problems(_v)]


def test_a_ledger_with_the_parent_still_present_is_rejected():
    _v = _view(ledger=_ledger())
    assert "split_parent_still_present" in [p["code"] for p in fs.load_problems(_v)]


def test_a_dotted_uid_outside_the_ledger_is_rejected_when_a_ledger_exists():
    _cand, _, _ = _apply()
    _v = copy.deepcopy(_cand)
    _v["frozen_facts"].append({"uid": "f04.1", "claim": "丙到场", "evidence": "丙到场。"})
    assert "split_ledger_missing" in [p["code"] for p in fs.load_problems(_v)]


# ---- 拒收 ----

def test_split_requires_stable_fact_uids():
    _v = _view()
    _v["plan_basis"].pop("fact_uids")
    _cand, _problems, _res = fs.apply_split_view(_v, _ops(), scope=_scope(),
                                                 ledger=_ledger(),
                                                 record=_record_for())
    assert _cand is None
    assert _problems[0]["code"] == "split_requires_stable_fact_uids"


@pytest.mark.parametrize("children,code", [
    ([_children()[0]], "split_children_out_of_range"),
    ([dict(_children()[0], uid="f02.x")] + _children()[1:], "split_children_invalid"),
])
def test_child_count_and_uid_shape_are_rejected(children, code):
    _cand, _problems, _res = fs.apply_split_view(
        _view(), _ops(children), scope=_scope(),
        ledger=_ledger(children=children), record=_record_for())
    assert _cand is None
    assert code in [p["code"] for p in _problems]


def test_child_evidence_must_come_from_the_parent_admitted_spans():
    _kids = _children()
    _kids[1]["evidence"] = "甲走后雪停了。"
    _cand, _problems, _res = fs.apply_split_view(
        _view(), _ops(_kids), scope=_scope(), ledger=_ledger(children=_kids),
        record=_record_for())
    assert _cand is None
    assert "split_evidence_not_derived" in [p["code"] for p in _problems]


def test_an_unsupported_judgment_is_not_applied():
    _cand, _problems, _res = fs.apply_split_view(
        _view(), _ops(), scope=_scope(), ledger=_ledger(verdict="partial"),
        record=_record_for())
    assert _cand is None
    assert [p["code"] for p in _problems] == ["split_judgment_not_supported"]


def test_reapplying_the_same_split_is_rejected():
    _cand, _, _ = _apply()
    _cand2, _problems, _res = fs.apply_split_view(_cand, _ops(), scope=_scope(),
                                                  ledger=_ledger())
    assert _cand2 is None
    assert [p["code"] for p in _problems] == ["split_already_recorded"]


def test_a_mismatched_judgment_digest_invalidates_the_record():
    _cand, _, _ = _apply()
    _led = _cand["plan_basis"][fs.CONTEXT_FIELD]["splits"][0]
    _led["judgment"]["inputs_digest"] = "0" * 64
    assert "verify_fact_split_context" in [p["code"] for p in fs.load_problems(_cand)]


# ---- 状态机 ----

def _record(digest, *, child_verdict="supported", with_source=True) -> dict:
    _facts = [{"fact_uid": u, "verdict": (child_verdict if u.startswith("f02.") else "supported")}
              for u in ("f01", "f02.1", "f02.2", "f03")]
    _out = {"plan_digest": digest, "status": "ok", "facts": _facts,
            "source_support": copy.deepcopy(_facts)}
    if not with_source:
        _out.pop("source_support")
    return _out


def test_split_state_needs_the_post_split_record_and_child_support():
    _cand, _, _ = _apply()
    _entry = _cand["plan_basis"][fs.CONTEXT_FIELD]["splits"][0]
    _digest = cv.plan_digest_of(pc.plan_from_view(_cand))
    assert fs.split_state(_cand, _entry) == "pending"
    assert fs.split_state(_cand, _entry, record=_record("0" * 64),
                          plan_digest=_digest) == "pending"
    assert fs.split_state(_cand, _entry, record=_record(_digest, child_verdict="partial"),
                          plan_digest=_digest) == "pending"
    assert fs.split_state(_cand, _entry, record=_record(_digest),
                          plan_digest=_digest) == "effective"


def test_the_legacy_fact_uid_table_feeds_problem_identity():
    from step01_script_gen import outline as o
    _plan = pc.plan_from_view(_view())
    assert o._uid_tables_of(_plan)["fact"] == ["f01", "f02", "f03"]
    assert o._uid_for({"kind": "fact", "index": 2}, o._uid_tables_of(_plan)) == "f02"
    _cand, _, _ = _apply()
    _plan2 = pc.plan_from_view(_cand)
    assert o._uid_for({"kind": "fact", "index": 2}, o._uid_tables_of(_plan2)) == "f02.1"
    assert o._uid_tables_of({"presentation": {"fact_uids": ["p1"]}})["fact"] == ["p1"]


def test_an_explicit_null_fact_uid_table_is_rejected():
    _plan = pc.plan_from_view(_view())
    _plan["fact_uids"] = None
    _view_out, _problems = pc.view_from_plan(_plan)
    assert _view_out is None
    assert [p["code"] for p in _problems] == ["view_fact_uids_mismatch"]


# ---- 授权、基线与账本绑定 ----

@pytest.mark.parametrize("scope", [
    None, {},
    {"allowed_split_fact_uids": [], "allowed_split_carrier_uids": ["b01"]},
    {"allowed_split_fact_uids": ["f02"], "allowed_split_carrier_uids": []},
    {"allowed_split_fact_uids": [" "], "allowed_split_carrier_uids": ["b01"]},
    {"allowed_split_fact_uids": ["f02"], "allowed_split_carrier_uids": "b01"},
])
def test_missing_blank_or_illegal_authorization_is_rejected(scope):
    _cand, _p, _res = fs.apply_split_view(
        _view(), _ops(), scope=scope, ledger=_ledger(), record=_record_for())
    assert _cand is None
    assert [x["code"] for x in _p][0] == "split_authorization_missing"


def test_the_parent_and_every_carrier_must_be_explicitly_authorized():
    _s = {"allowed_split_fact_uids": ["f01"], "allowed_split_carrier_uids": ["b01", "b02"]}
    _cand, _p, _res = fs.apply_split_view(_view(), _ops(), scope=_s,
                                          ledger=_ledger(), record=_record_for())
    assert _cand is None and "split_not_authorized" in [x["code"] for x in _p]
    _s = {"allowed_split_fact_uids": ["f02"], "allowed_split_carrier_uids": ["b01"]}
    _cand, _p, _res = fs.apply_split_view(_view(), _ops(), scope=_s,
                                          ledger=_ledger(), record=_record_for())
    assert _cand is None and "split_carrier_not_allowed" in [x["code"] for x in _p]


def test_the_ledger_must_match_the_actual_baseline_digest():
    _led = _ledger(base_digest="c" * 64)
    _cand, _p, _res = fs.apply_split_view(_view(), _ops(), scope=_scope(),
                                          ledger=_led, record=_record_for())
    assert _cand is None
    assert [x["code"] for x in _p] == ["split_base_digest_mismatch"]


def test_the_authority_record_must_match_the_baseline():
    _rec = _record_for()
    _rec["plan_digest"] = "d" * 64
    _cand, _p, _res = fs.apply_split_view(_view(), _ops(), scope=_scope(),
                                          ledger=_ledger(), record=_rec)
    assert _cand is None and "split_authority_record_mismatch" in [x["code"] for x in _p]


def test_a_missing_authority_record_is_rejected():
    _cand, _p, _res = fs.apply_split_view(_view(), _ops(), scope=_scope(),
                                          ledger=_ledger(), record=None)
    assert _cand is None
    assert [x["code"] for x in _p] == ["split_authority_record_missing"]


def test_the_ledger_cannot_invent_parent_evidence_spans():
    _led = _ledger(spans=[EVIDENCE["f02"] + "编造。"])
    _cand, _p, _res = fs.apply_split_view(_view(), _ops(), scope=_scope(),
                                          ledger=_led, record=_record_for())
    assert _cand is None
    assert any(x["code"] == "split_authority_record_mismatch"
               and x["path"] == "parent_evidence_spans" for x in _p)


def test_the_ledger_carrier_must_be_real_and_match_the_binding_on_load():
    _led = _ledger(carrier={"kind": "visual", "uid": "not_here"})
    _cand, _p, _res = fs.apply_split_view(_view(), _ops(), scope=_scope(),
                                          ledger=_led, record=_record_for())
    assert _cand is None
    assert any(x["code"] == "verify_fact_split_context" for x in _p)
    _cand, _, _ = _apply()
    _cand["plan_basis"][fs.CONTEXT_FIELD]["splits"][0]["children"][0]["carrier"] = \
        {"kind": "visual", "uid": "not_here"}
    _codes = [x["code"] for x in fs.load_problems(_cand)]
    assert "split_child_carrier_mismatch" in _codes
    assert "split_children_invalid" in _codes


def test_a_ledger_carrier_that_differs_from_the_binding_is_rejected_on_load():
    _cand, _, _ = _apply()
    _cand["plan_basis"][fs.CONTEXT_FIELD]["splits"][0]["children"][0]["carrier"] = \
        {"kind": "visual", "uid": "b02"}
    assert "split_child_carrier_mismatch" in [x["code"] for x in fs.load_problems(_cand)]


def test_protocol_problems_classify_the_split_context_code():
    _cand, _, _ = _apply()
    _v = _with_context(_cand)
    _led = _v["plan_basis"][fs.CONTEXT_FIELD]["splits"][0]
    _led["baseline_claim"] = CLAIMS["f02"]
    _led["parent_claim"] = "甲说话并走开"
    _led["judgment"]["inputs_digest"] = fs.inputs_digest("甲说话并走开", _led["children"])
    assert [p["code"] for p in sr.protocol_problems({}, _v)] == \
        ["verify_fact_split_context"]


# ---- 双轴状态与来源准入 ----

def test_split_state_stays_pending_without_the_source_axis():
    _cand, _, _ = _apply()
    _entry = _cand["plan_basis"][fs.CONTEXT_FIELD]["splits"][0]
    _digest = cv.plan_digest_of(pc.plan_from_view(_cand))
    assert fs.split_state(_cand, _entry, record=_record(_digest, with_source=False),
                          plan_digest=_digest) == "pending"
    _rec = _record(_digest)
    _rec["source_support"] = [r for r in _rec["source_support"]
                              if r["fact_uid"] != "f02.2"]
    assert fs.split_state(_cand, _entry, record=_rec,
                          plan_digest=_digest) == "pending"


def test_split_state_is_invalid_when_source_admission_fails():
    _cand, _, _ = _apply()
    _digest = cv.plan_digest_of(pc.plan_from_view(_cand))
    _v = _with_context(_cand)
    _led = _v["plan_basis"][fs.CONTEXT_FIELD]["splits"][0]
    _led["baseline_claim"] = CLAIMS["f02"]
    _led["parent_claim"] = "甲说话并走开"
    _led["judgment"]["inputs_digest"] = fs.inputs_digest("甲说话并走开", _led["children"])
    assert fs.split_state(_v, _led, record=_record(_digest),
                          plan_digest=_digest) == "invalid"


def test_a_failed_authority_record_cannot_authorize_a_split():
    _rec = _record_for()
    _rec["status"] = "failed"
    _cand, _p, _res = fs.apply_split_view(_view(), _ops(), scope=_scope(),
                                          ledger=_ledger(), record=_rec)
    assert _cand is None
    assert "split_authority_record_not_ok" in [x["code"] for x in _p]


def test_a_malformed_authority_record_cannot_authorize_a_split():
    _rec = _record_for()
    _rec.pop("voice_units")
    _cand, _p, _res = fs.apply_split_view(_view(), _ops(), scope=_scope(),
                                          ledger=_ledger(), record=_rec)
    assert _cand is None
    assert "split_authority_record_malformed" in [x["code"] for x in _p]


@pytest.mark.parametrize("group", ["facts", "source_support"])
def test_a_non_list_record_group_is_rejected_without_crashing_or_mutating(group):
    _rec = _record_for()
    _rec[group] = 1
    _v, _ops_in, _led = _view(), _ops(), _ledger()
    _snap = copy.deepcopy((_v, _ops_in, _led, _rec))
    _calls = []
    _cand, _p, _res = fs.apply_split_view(
        _v, _ops_in, scope=_scope(), ledger=_led, record=_rec,
        accept=lambda *a, **k: _calls.append((a, k)))
    assert _cand is None
    assert _res["kind"] == "illegal"
    assert any(x["code"] == "split_authority_record_malformed"
               and x["path"] == group for x in _p), _p
    assert _calls == []
    assert (_v, _ops_in, _led, _rec) == _snap


def test_split_state_recomputes_the_digest_from_the_current_candidate():
    _cand, _, _ = _apply()
    _entry = _cand["plan_basis"][fs.CONTEXT_FIELD]["splits"][0]
    _old = cv.plan_digest_of(pc.plan_from_view(_cand))
    _changed = copy.deepcopy(_cand)
    _changed["frozen_facts"][0]["evidence"] = "甲进屋（改）。"
    assert fs.split_state(_changed, _entry, record=_record(_old),
                          plan_digest=_old) == "invalid"
    assert fs.split_state(_changed, _entry, record=_record(_old)) == "pending"


def _second_setup():
    _cand, _, _ = _apply()
    _plan = pc.plan_from_view(_cand)
    _digest = cv.plan_digest_of(_plan)
    _kids = [{"uid": "f03.1", "claim": "乙留下", "evidence": "乙留下。",
              "carrier": {"kind": "visual", "uid": "b02"}},
             {"uid": "f03.2", "claim": "乙仍留下", "evidence": "乙留下。",
              "carrier": {"kind": "visual", "uid": "b01"}}]
    _first = copy.deepcopy(_cand["plan_basis"][fs.CONTEXT_FIELD]["splits"][0])
    _second = dict(copy.deepcopy(_first), parent_uid="f03", parent_claim="乙留下",
                   parent_evidence="乙留下。", parent_evidence_spans=["乙留下。"],
                   children=_kids,
                   authority=dict(_first["authority"], fact_uid="f03",
                                  plan_digest=_digest),
                   judgment={"mode": "shadow", "verdict": "supported", "missing": "",
                             "reason": "", "inputs_digest": fs.inputs_digest("乙留下", _kids),
                             "call_id": "", "prompt_digest": "", "response_digest": ""})
    return _cand, _plan, _digest, _first, _second, _kids


def test_a_second_split_cannot_rewrite_a_historical_entry():
    _cand, _plan, _digest, _first, _second, _kids = _second_setup()
    _bad_first = copy.deepcopy(_first)
    _bad_first["parent_claim"] = "甲说话并走开"
    _bad_first["judgment"]["inputs_digest"] = fs.inputs_digest(
        "甲说话并走开", _bad_first["children"])
    _ledger2 = {"version": fs.VERSION, "novel_digest": NOVEL_DIGEST,
                "base_digest": _digest, "splits": [_bad_first, _second]}
    _cand2, _p, _res = fs.apply_split_view(
        _cand, {"parent_uid": "f03", "children": _kids},
        scope={"allowed_split_fact_uids": ["f03"],
               "allowed_split_carrier_uids": ["b01", "b02"]},
        ledger=_ledger2,
        record=_record_for(_plan, fact_uid="f03", spans=[EVIDENCE["f03"]]))
    assert _cand2 is None
    assert "split_history_modified" in [x["code"] for x in _p]


def test_a_ledger_entry_outside_history_and_ops_is_rejected():
    _cand, _plan, _digest, _first, _second, _kids = _second_setup()
    _extra = copy.deepcopy(_second)
    _extra["parent_uid"] = "f01"
    _ledger2 = {"version": fs.VERSION, "novel_digest": NOVEL_DIGEST,
                "base_digest": _digest, "splits": [_first, _second, _extra]}
    _cand2, _p, _res = fs.apply_split_view(
        _cand, {"parent_uid": "f03", "children": _kids},
        scope={"allowed_split_fact_uids": ["f03"],
               "allowed_split_carrier_uids": ["b01", "b02"]},
        ledger=_ledger2,
        record=_record_for(_plan, fact_uid="f03", spans=[EVIDENCE["f03"]]))
    assert _cand2 is None
    assert "split_ledger_unexpected_entry" in [x["code"] for x in _p]


def test_two_consecutive_legal_splits_still_succeed():
    _cand, _plan, _digest, _first, _second, _kids = _second_setup()
    _ledger2 = {"version": fs.VERSION, "novel_digest": NOVEL_DIGEST,
                "base_digest": _digest, "splits": [_first, _second]}
    _cand2, _p, _res = fs.apply_split_view(
        _cand, {"parent_uid": "f03", "children": _kids},
        scope={"allowed_split_fact_uids": ["f03"],
               "allowed_split_carrier_uids": ["b01", "b02"]},
        ledger=_ledger2,
        record=_record_for(_plan, fact_uid="f03", spans=[EVIDENCE["f03"]]))
    assert _res["kind"] == "applied", (_res, _p)
    assert [f["uid"] for f in _cand2["frozen_facts"]] == \
        ["f01", "f02.1", "f02.2", "f03.1", "f03.2"]
    assert fs.load_problems(_cand2) == []
