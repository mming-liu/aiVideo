# -*- coding: utf-8 -*-
"""R3-4：来源修订的 core_updates 默认冻结 + 依赖原因放行."""
from step01_script_gen import source_revision as sr


def _plan():
    return {
        "required_facts": [
            "甲进屋｜原文证据：“甲进屋。”｜承载：B1/画面"],
        "fact_uids": ["f01"],
        "shot_beats": [
            "节拍：甲推门进屋｜本镜信息目的：建立人物｜原文依据：“甲进屋。”｜载体：画面"],
        "main_events": ["甲进屋。"],
        "climax": "甲进屋。",
        "end_hook": "门关上。",
        "plan_verification": {"status": "ok", "novel_digest": "d"},
    }


def _response(core):
    return {"fact_updates": [
        {"fact_uid": "f01", "claim": "甲进屋", "evidence": "甲进屋。"}],
        "core_updates": core}


def _patch(monkeypatch):
    monkeypatch.setattr(sr, "eligible_targets", lambda plan: ["f01"])
    monkeypatch.setattr("shared.content_verification.record_protocol_problems",
                        lambda *a, **k: [])
    monkeypatch.setattr("step01_script_gen.content_verify.novel_identity",
                        lambda text: {"digest": "d"})
    # 本测试只核 core 守卫；来源上下文的完整形状另有既有用例。
    monkeypatch.setattr(sr, "admission_problems", lambda view: [])


def test_r26_core_update_is_rejected(monkeypatch):
    _patch(monkeypatch)
    cand, bad = sr.apply_response(
        _plan(), _response({"main_events": ["改写后的主线"],
                            "end_hook": "改写后的钩子"}),
        novel_text="甲进屋。", novel_digest="d")
    assert cand is None
    assert {p["code"] for p in bad} == {"core_field_frozen"}


def test_target_fact_update_passes_when_core_is_empty(monkeypatch):
    _patch(monkeypatch)
    cand, bad = sr.apply_response(
        _plan(), _response({}), novel_text="甲进屋。", novel_digest="d")
    assert bad == []
    assert cand is not None
    assert cand["required_facts"][0].startswith("甲进屋｜")


def test_core_update_passes_with_dependency_reason_and_is_traced(monkeypatch):
    _patch(monkeypatch)
    cand, bad = sr.apply_response(
        _plan(), _response({"end_hook": "门关上，甲回头。"}),
        novel_text="甲进屋。", novel_digest="d",
        allowed_core_updates={"end_hook": "f01 的进屋动作需要与钩子同步"})
    assert bad == []
    assert cand is not None
    assert cand["end_hook"] == "门关上，甲回头。"
    assert cand["source_revision_context"]["core_update_trace"] == {
        "end_hook": "f01 的进屋动作需要与钩子同步"}
