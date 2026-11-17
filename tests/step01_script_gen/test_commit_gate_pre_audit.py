# -*- coding: utf-8 -*-
"""M4：送审前确定性预审门 + 注入幂等防重（2026-11-18）."""
from __future__ import annotations

import pytest

from step01_script_gen import commit_gate as cg
from step01_script_gen import main as m
from step01_script_gen import repair_loop as rl


def _shot(prompt: str) -> dict:
    return {"index": 1, "duration_sec": 5.0, "prompt": prompt,
            "ff_desc": "甲站立", "lf_desc": "甲转身", "motion_desc": "甲转身",
            "main_subject": "甲", "frame_layouts": {}, "keyframes": [],
            "narration_segment": ""}


def _script(prompt: str) -> dict:
    return {"shots": [_shot(prompt)], "characters": [], "hook": None,
            "chapter_index": 1, "book_index": 1, "book_name": "book",
            "total_duration": 5.0}


def test_clean_script_passes_gate():
    gate = cg.pre_audit_gate(_script("甲站在船头看着远方的江面。"))
    assert gate["ok"] is True
    assert gate["hard"] == []


def test_duplicate_injection_is_hard_and_raises():
    block = "甲站在船头看着远方的江面，风吹动他的衣角慢慢扬起。"
    script = _script(block + block)
    gate = cg.pre_audit_gate(script)
    assert gate["ok"] is False
    assert any(f.get("code") == "duplicate_injection" for f in gate["hard"])
    with pytest.raises(cg.PreAuditError):
        cg.assert_injection_idempotent(script)


def test_pre_audit_enabled_env(monkeypatch):
    monkeypatch.delenv("STEP01_PRE_AUDIT_GATE", raising=False)
    assert cg.pre_audit_enabled() is False
    monkeypatch.setenv("STEP01_PRE_AUDIT_GATE", "1")
    assert cg.pre_audit_enabled() is True


def test_strict_audit_blocks_without_model_call(monkeypatch):
    block = "甲站在船头看着远方的江面，风吹动他的衣角慢慢扬起。"
    script = _script(block + block)
    monkeypatch.setenv("STEP01_PRE_AUDIT_GATE", "1")
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("strict pre-audit must not call the model")

    import step01_script_gen.audit as audit
    monkeypatch.setattr(audit, "run_same_model_audit_with_status", _boom)
    result = m._run_final_semantic_audit(script, "原文", None, run_id="test")
    assert result["status"] == "blocked"
    assert result["error"].startswith("pre_audit_hard")
    assert called["n"] == 0


def test_split_modules_are_wired():
    assert m._REUSE_MISS_TEXT is cg.REUSE_MISS_TEXT
    assert rl.paired_repair_budget() == m._paired_repair_budget()
