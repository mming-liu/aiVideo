# -*- coding: utf-8 -*-
"""M4：送审前确定性预审门 + 注入幂等防重（2026-11-18）.

T2 扩展（同日）：strict 模式下「先修后审」三态——
  * healed：确定性 hard 被已有 `run_hard_fix` 消除，返回修复稿建议；
  * needs_llm_repair：仍有需文本重生成的 hard，保持 blocked；
  * unresolved：修复器/复检异常或剩余 hard 无法归类，保持 blocked 并给原因。
"""
from __future__ import annotations

import copy

import pytest

from step01_script_gen import commit_gate as cg
from step01_script_gen import main as m
from step01_script_gen import repair_loop as rl


@pytest.fixture(autouse=True)
def _zero_call_guard():
    """零调用证据：本文件任何用例都不得产生逻辑调用/HTTP 尝试/响应."""
    from shared import outbound_guard

    guard = outbound_guard.OutboundGuard(
        transport=lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("unexpected provider POST")))
    prev = outbound_guard.install(guard)
    try:
        yield guard
    finally:
        outbound_guard.install(prev)
    snap = guard.snapshot()
    assert snap["logical_total"] == 0
    assert snap["http_attempts_total"] == 0
    assert snap["responses_total"] == 0


def _shot(prompt: str) -> dict:
    return {"index": 1, "duration_sec": 5.0, "prompt": prompt,
            "ff_desc": "甲站立", "lf_desc": "甲转身", "motion_desc": "甲转身",
            "main_subject": "甲", "frame_layouts": {}, "keyframes": [],
            "narration_segment": ""}


def _script(prompt: str) -> dict:
    return {"shots": [_shot(prompt)], "characters": [], "hook": None,
            "chapter_index": 1, "book_index": 1, "book_name": "book",
            "total_duration": 5.0}


def _group_script() -> dict:
    """匿名群体在场断档：kf0/kf2 有「群演」条目、kf1 没有但描述提到。

    `layout_presence_gap` 是 hard；已有 `group_actor_continuity` 会按描述把
    群体条目补回 kf1——这是 `run_hard_fix` 真实可消除的确定性 hard。
    """
    group = {"name": "群演", "on_screen": True, "position": "画右",
             "pose": "张望", "gaze_target": "无", "props": []}
    person = {"name": "甲", "on_screen": True, "position": "画左",
              "pose": "站立", "gaze_target": "无", "props": []}
    return {
        "title": "t", "characters": [], "hook": None,
        "shots": [{
            "index": 1, "duration_sec": 6.0, "main_subject": "甲",
            "ff_desc": "甲站在院中，群演在画右张望。", "lf_desc": "甲抬头望天。",
            "motion_desc": "甲站在院中抬头望天，群演在画右张望。",
            "prompt": "[中景]甲站在院中，群演在画右张望。",
            "narration_segment": "", "scene": "院内", "location": "院中",
            "clip_transition": "硬切", "context_bridge": "",
            "char_layout": [{"name": "甲", "position": "画左"}],
            "frame_layouts": {
                "ff": [copy.deepcopy(person), copy.deepcopy(group)],
                "lf": [copy.deepcopy(person)],
                "kfs": [
                    [copy.deepcopy(person), copy.deepcopy(group)],
                    [copy.deepcopy(person)],
                    [copy.deepcopy(person), copy.deepcopy(group)],
                ],
            },
            "keyframes": [
                {"desc": "甲站在院中，群演在画右张望。", "t_sec": 1.5, "stage": "pre"},
                {"desc": "甲仍在院中，群演在画右张望。", "t_sec": 3.0, "stage": "mid"},
                {"desc": "甲抬头望天，群演在画右张望。", "t_sec": 4.5, "stage": "after"},
            ],
        }],
    }


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
    assert result["pre_audit_status"] == "needs_llm_repair"
    assert result["issues"]
    assert called["n"] == 0


def test_split_modules_are_wired():
    assert m._REUSE_MISS_TEXT is cg.REUSE_MISS_TEXT
    assert rl.paired_repair_budget() == m._paired_repair_budget()


def test_heal_three_states_healed_without_model(monkeypatch):
    """healed：确定性 hard 归零；原稿不被就地改写；provider 零触碰."""
    script = _group_script()
    snapshot = copy.deepcopy(script)
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("heal must not call the model")

    import step01_script_gen.audit as audit
    monkeypatch.setattr(audit, "run_same_model_audit_with_status", _boom)

    heal = cg.heal_pre_audit_hard(script)
    assert heal["status"] == "healed"
    assert heal["ok"] is True
    assert heal["hard_before"] and not heal["hard_after"]
    assert any(r["constraint"] == "group_actor_continuity"
               for r in heal["repairs"])
    assert isinstance(heal["script"], dict)
    assert cg.pre_audit_gate(heal["script"])["ok"] is True
    # 函数只返回建议，不就地改调用方对象
    assert script == snapshot
    assert called["n"] == 0


def test_heal_three_states_needs_llm_repair():
    block = "甲站在船头看着远方的江面，风吹动他的衣角慢慢扬起。"
    heal = cg.heal_pre_audit_hard(_script(block + block))
    assert heal["status"] == "needs_llm_repair"
    assert heal["ok"] is False
    assert heal["script"] is None
    assert any(f.get("code") == "duplicate_injection"
               for f in heal["hard_after"])
    assert "duplicate_injection" in heal["reason"]


def test_heal_three_states_unresolved_on_runner_failure(monkeypatch):
    import step01_script_gen.constraints as constraints

    def _boom(*a, **k):
        raise RuntimeError("constraint engine unavailable")

    monkeypatch.setattr(constraints, "run_hard_fix", _boom)
    heal = cg.heal_pre_audit_hard(_group_script())
    assert heal["status"] == "unresolved"
    assert heal["ok"] is False
    assert "constraint engine unavailable" in heal["reason"]


def test_strict_audit_returns_healed_sentinel_without_model(monkeypatch):
    script = _group_script()
    monkeypatch.setenv("STEP01_PRE_AUDIT_GATE", "1")
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("healed pre-audit must not call the model")

    import step01_script_gen.audit as audit
    monkeypatch.setattr(audit, "run_same_model_audit_with_status", _boom)
    result = m._run_final_semantic_audit(script, "原文", None, run_id="test")
    assert result["status"] == "pre_audit_healed"
    assert isinstance(result["healed_script"], dict)
    assert cg.pre_audit_gate(result["healed_script"])["ok"] is True
    assert called["n"] == 0


def test_strict_audit_unresolved_keeps_blocked(monkeypatch):
    import step01_script_gen.constraints as constraints

    def _boom(*a, **k):
        raise RuntimeError("constraint engine unavailable")

    monkeypatch.setattr(constraints, "run_hard_fix", _boom)
    monkeypatch.setenv("STEP01_PRE_AUDIT_GATE", "1")
    called = {"n": 0}

    def _audit_boom(*a, **k):
        called["n"] += 1
        raise AssertionError("unresolved pre-audit must not call the model")

    import step01_script_gen.audit as audit
    monkeypatch.setattr(audit, "run_same_model_audit_with_status", _audit_boom)
    result = m._run_final_semantic_audit(_group_script(), "原文", None,
                                        run_id="test")
    assert result["status"] == "blocked"
    assert result["pre_audit_status"] == "unresolved"
    assert result["error"].startswith("pre_audit_hard")
    assert "constraint engine unavailable" in result["error"]
    assert called["n"] == 0


def test_default_off_does_not_enter_heal(monkeypatch):
    monkeypatch.delenv("STEP01_PRE_AUDIT_GATE", raising=False)
    import step01_script_gen.audit as audit

    def _heal_boom(*a, **k):
        raise AssertionError("heal must not run when strict gate is off")

    monkeypatch.setattr(cg, "heal_pre_audit_hard", _heal_boom)

    class _Result:
        status = "ok"
        error = ""
        issues = []
        channel_model = "fake"
        channel_provider = "fake"
        usage = None
        schema_deviations = []
        attempts = 1
        character_aliases = []
        raw_text = ""
        failed_attempts = []
        completion = None
        timing = {}

    called = {"n": 0}

    def _fake_audit(*a, **k):
        called["n"] += 1
        return _Result()

    monkeypatch.setattr(audit, "run_same_model_audit_with_status", _fake_audit)
    result = m._run_final_semantic_audit(_group_script(), "原文", None,
                                        run_id="test")
    assert result["status"] == "ok"
    assert called["n"] == 1
