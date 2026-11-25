# -*- coding: utf-8 -*-
"""S12 复合来源修正：逐字证据、拆分谱系、拒收对照."""
from __future__ import annotations

from step01_script_gen import content_verify as cv
from step01_script_gen import lightweight_plan as lw
from step01_script_gen.source_claim_fix import apply_source_correction

NOVEL = ("门外来了一个人。\n"
         "他一身叫化子装扮，手里提着一串鱼，鱼尾偶尔摆动几下。\n"
         "这人虽然寒酸，可是五官相貌却和少爷一般无二。\n")


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


def test_split_into_two_verbatim_children_keeps_position_and_lineage():
    response = {"splits": [{"id": "f01", "children": [
        {"claim": "门外来了一个叫化子装扮的人，手提一串鱼。",
         "evidence": "他一身叫化子装扮，手里提着一串鱼，鱼尾偶尔摆动几下。"},
        {"claim": "这人的五官相貌与少爷一般无二。",
         "evidence": "这人虽然寒酸，可是五官相貌却和少爷一般无二。"},
    ]}]}
    out, problems = apply_source_correction(_answer(), response, NOVEL)
    assert problems == [] and out is not None
    assert [f["claim"] for f in out["facts"]][:2] == [
        "门外来了一个叫化子装扮的人，手提一串鱼。",
        "这人的五官相貌与少爷一般无二。"]
    assert all(f["event"] == 1 and f["beat"] == 2 for f in out["facts"][:2])
    assert [f["_source_parent"] for f in out["facts"][:2]] == ["f01", "f01"]
    assert out["facts"][2]["claim"] == "另有一条事实。"
    assert out["source_splits"][0]["parent_claim"].startswith("门外来了一个")
    assert len(out["source_splits"][0]["children"]) == 2
    view = lw.build_view(out)
    assert view["plan_basis"]["source_splits"][0]["parent_claim"].startswith("门外来了一个")
    prompt = cv.build_verification_prompt(view, novel_text=NOVEL)
    assert "来源修正父主张" in prompt and "parent_claim" in prompt


def test_evidence_must_be_verbatim():
    response = {"splits": [{"id": "f01", "children": [
        {"claim": "甲", "evidence": "他一身叫化子装扮，手里提着一串鱼，鱼尾偶尔摆动几下。"},
        {"claim": "乙", "evidence": "这人五官和少爷一模一样。"},
    ]}]}
    out, problems = apply_source_correction(_answer(), response, NOVEL)
    assert out is None and [p["code"] for p in problems] == ["source_evidence_not_verbatim"]


def test_unknown_fact_and_bad_shape_are_rejected():
    out, problems = apply_source_correction(_answer(), {"splits": [
        {"id": "f09", "children": [
            {"claim": "甲", "evidence": "门外来了一个人。"},
            {"claim": "乙", "evidence": "门外来了一个人。"}]}]}, NOVEL)
    assert out is None and [p["code"] for p in problems] == ["source_fact_unknown"]
    out2, problems2 = apply_source_correction(_answer(), {"splits": []}, NOVEL)
    assert out2 is not None and problems2 == []


def test_children_must_cover_the_parent_core():
    response = {"splits": [{"id": "f01", "children": [
        {"claim": "有人来。", "evidence": "门外来了一个人。"},
        {"claim": "鱼尾摆动。", "evidence": "他一身叫化子装扮，手里提着一串鱼，鱼尾偶尔摆动几下。"},
    ]}]}
    out, problems = apply_source_correction(_answer(), response, NOVEL)
    assert out is None
    assert [p["code"] for p in problems] == ["source_parent_coverage_gap"]


def test_only_authorized_parents_can_be_changed():
    response = {"splits": [{"id": "f01", "children": [
        {"claim": "门外来了一个叫化子装扮的人，手提一串鱼。",
         "evidence": "他一身叫化子装扮，手里提着一串鱼，鱼尾偶尔摆动几下。"},
        {"claim": "这人的五官相貌与少爷一般无二。",
         "evidence": "这人虽然寒酸，可是五官相貌却和少爷一般无二。"},
    ]}]}
    out, problems = apply_source_correction(_answer(), response, NOVEL,
                                            allowed_parents={"f02"})
    assert out is None and [p["code"] for p in problems] == ["source_fact_not_authorized"]
