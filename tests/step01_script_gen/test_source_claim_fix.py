# -*- coding: utf-8 -*-
"""S12 复合来源修正：逐字证据、拆分谱系、父保留结论与拒收对照."""
from __future__ import annotations

import shared.content_verification as scv
from step01_script_gen import content_verify as cv
from step01_script_gen import lightweight_plan as lw
from step01_script_gen.source_claim_fix import apply_source_correction

NOVEL = ("门外来了一个人。\n"
         "他一身叫化子装扮，手里提着一串鱼，鱼尾偶尔摆动几下。\n"
         "这人虽然寒酸，可是五官相貌却和少爷一般无二。\n")
GOOD_CHILDREN = [
    {"claim": "门外来了一个叫化子装扮的人，手提一串鱼。",
     "evidence": "他一身叫化子装扮，手里提着一串鱼，鱼尾偶尔摆动几下。"},
    {"claim": "这人的五官相貌与少爷一般无二。",
     "evidence": "这人虽然寒酸，可是五官相貌却和少爷一般无二。"},
]


def _answer():
    return {"characters": [], "climax": "", "end_hook": "", "emotional_arc": "",
            "omittable": [],
            "events": [{"summary": "一", "beats": [
                {"text": "画面", "purpose": "", "source": "门外来了一个人。",
                 "voice_kind": "none", "voice_text": "", "speaker": ""},
                {"text": "台词", "purpose": "", "source": "他一身叫化子装扮。",
                 "voice_kind": "dialogue", "voice_text": "他一身叫化子装扮。",
                 "speaker": "某人"}]}],
            "facts": [
                {"claim": "门外来了一个叫化子装扮的人，手提一串鱼，五官与少爷一般无二。",
                 "evidence": "他一身叫化子装扮，手里提着一串鱼，鱼尾偶尔摆动几下。",
                 "event": 1, "beat": 2, "carrier": "voice"},
                {"claim": "另有一条事实。", "evidence": "门外来了一个人。",
                 "event": 1, "beat": 1, "carrier": "visual"},
            ]}


def _resp(children, *, verdict="supported", targets=("f01",), spans=None):
    return {"splits": [{"id": targets[0], "children": children}],
            "source_preservation": {
                "target_uids": list(targets), "verdict": verdict,
                "evidence_spans": spans or ["他一身叫化子装扮"],
                "reason": "父主张核心仍由子项共同表达"}}


def test_split_into_two_verbatim_children_keeps_position_and_lineage():
    out, problems = apply_source_correction(_answer(), _resp(GOOD_CHILDREN), NOVEL)
    assert problems == [] and out is not None
    assert [f["claim"] for f in out["facts"]][:2] == [
        "门外来了一个叫化子装扮的人，手提一串鱼。",
        "这人的五官相貌与少爷一般无二。"]
    assert all(f["event"] == 1 and f["beat"] == 2 for f in out["facts"][:2])
    assert [f["_source_parent"] for f in out["facts"][:2]] == ["f01", "f01"]
    assert out["facts"][2]["claim"] == "另有一条事实。"
    assert out["source_splits"][0]["parent_claim"].startswith("门外来了一个")
    assert len(out["source_splits"][0]["children"]) == 2
    assert out["source_preservation"]["verdict"] == "supported"
    view = lw.build_view(out)
    assert view["plan_basis"]["source_splits"][0]["parent_claim"].startswith("门外来了一个")
    assert view["plan_basis"]["source_preservation"]["verdict"] == "supported"
    prompt = cv.build_verification_prompt(view, novel_text=NOVEL)
    assert "来源修正父主张" in prompt and "parent_claim" in prompt
    assert "source_preservation" in prompt and '["f01"]' in prompt
    assert view["plan_basis"]["source_splits"][0]["child_uids"] == ["f01", "f02"]
    assert cv._request_admission(view) == []
    # 待核候选即使没有保留结论也可以进入核验（结论由独立核验给）
    pending = __import__("copy").deepcopy(view)
    pending["plan_basis"].pop("source_preservation")
    assert cv._request_admission(pending) == []


def test_independent_preservation_is_required_and_negative_is_reported():
    out, problems = apply_source_correction(_answer(), _resp(GOOD_CHILDREN), NOVEL)
    assert problems == []
    view = lw.build_view(out)
    splits = view["plan_basis"]["source_splits"]
    assert splits[0]["child_uids"] == ["f01", "f02"]
    good = {"status": "ok",
            "source_preservation": {"target_uids": ["f01"], "verdict": "supported",
                                    "evidence_spans": ["他一身叫化子装扮"],
                                    "reason": "独立核验：两层都在"}}
    assert scv.source_split_preservation_problems(good, view, novel_text=NOVEL) == []
    missing = {"status": "ok"}
    assert [p["code"] for p in scv.source_split_preservation_problems(
        missing, view, novel_text=NOVEL)] == ["verify_source_preservation_record"]
    partial = {"status": "ok", "source_preservation": {
        "target_uids": ["f01"], "verdict": "partial",
        "evidence_spans": ["他一身叫化子装扮"], "reason": "缺五官"}}
    assert [p["code"] for p in scv.source_split_preservation_problems(
        partial, view, novel_text=NOVEL)] == ["source_preservation_failed"]


def test_missing_preservation_is_rejected():
    response = {"splits": [{"id": "f01", "children": GOOD_CHILDREN}]}
    out, problems = apply_source_correction(_answer(), response, NOVEL)
    assert out is None and [p["code"] for p in problems] == ["source_preservation_missing"]


def test_negative_preservation_is_rejected():
    out, problems = apply_source_correction(
        _answer(), _resp(GOOD_CHILDREN, verdict="partial"), NOVEL)
    assert out is None and [p["code"] for p in problems] == ["source_preservation_negative"]


def test_preservation_identity_and_evidence_are_enforced():
    bad_id = _resp(GOOD_CHILDREN)
    bad_id["source_preservation"]["target_uids"] = ["f02"]
    _out, problems = apply_source_correction(_answer(), bad_id, NOVEL)
    assert [p["code"] for p in problems] == ["source_preservation_identity"]
    bad_ev = _resp(GOOD_CHILDREN, spans=["原文里没有的一句"])
    _out2, problems2 = apply_source_correction(_answer(), bad_ev, NOVEL)
    assert [p["code"] for p in problems2] == ["source_preservation_evidence"]


def test_evidence_must_be_verbatim():
    children = [GOOD_CHILDREN[0], {"claim": "乙", "evidence": "这人五官和少爷一模一样。"}]
    out, problems = apply_source_correction(_answer(), _resp(children), NOVEL)
    assert out is None and [p["code"] for p in problems] == ["source_evidence_not_verbatim"]


def test_unknown_fact_is_rejected():
    response = {"splits": [{"id": "f09", "children": GOOD_CHILDREN}],
                "source_preservation": {"target_uids": ["f09"], "verdict": "supported",
                                        "evidence_spans": ["他一身叫化子装扮"],
                                        "reason": "核心保留"}}
    out, problems = apply_source_correction(_answer(), response, NOVEL)
    assert out is None and [p["code"] for p in problems] == ["source_fact_unknown"]


def test_character_coverage_is_diagnostic_not_a_hard_gate():
    # 子主张删掉“装扮/提鱼/五官”，但长证据仍在：字符覆盖高，不得作为硬门；
    # 保留结论若声称 supported，由内容核验（父上下文）判，而不是字符阈值。
    children = [{"claim": "有人来。", "evidence": "门外来了一个人。"},
                {"claim": "鱼尾摆动。",
                 "evidence": "他一身叫化子装扮，手里提着一串鱼，鱼尾偶尔摆动几下。"}]
    out, problems = apply_source_correction(_answer(), _resp(children), NOVEL)
    assert problems == [] and out is not None
    assert all(f.get("_diagnostic_parent_claim_coverage", 1) < 0.8
               for f in out["facts"][:2])


def test_only_authorized_parents_can_be_changed():
    out, problems = apply_source_correction(
        _answer(), _resp(GOOD_CHILDREN), NOVEL, allowed_parents={"f02"})
    assert out is None and [p["code"] for p in problems] == ["source_fact_not_authorized"]


def test_verification_problems_reports_negative_source_split_preservation(monkeypatch):
    out, problems = apply_source_correction(_answer(), _resp(GOOD_CHILDREN), NOVEL)
    assert problems == []
    view = lw.build_view(out)
    monkeypatch.setattr(scv, "record_shape_problems", lambda *a, **k: [])
    monkeypatch.setattr(scv, "fact_identity_problems", lambda *a, **k: [])
    monkeypatch.setattr(scv, "source_results", lambda r: [])
    record = {"status": "ok", "plan_digest": "", "novel_digest": "",
              "rule_version": scv.DEFAULT_RULE_VERSION,
              "source_preservation": {
                  "target_uids": ["f01"], "verdict": "partial",
                  "evidence_spans": ["他一身叫化子装扮"], "reason": "缺五官"}}
    codes = [p["code"] for p in scv.verification_problems(record, view, plan_digest="")]
    assert "source_preservation_failed" in codes, codes
