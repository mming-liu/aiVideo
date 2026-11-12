"""S05：内容身份与恢复判定（2026-11-11）.

要证明的：
  1. `run_id` 是运行诊断，不进请求字节——同一份冻结输入重跑得到同一个内容身份；
  2. 原文 / 计划 / 送审稿 / 契约任一变化 ⇒ 身份变化（失配必须明确）；
  3. 给了期望身份时，旧审计缺身份或身份不符一律拒收，**不退回** revision 弱判据。
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def chapter(tmp_path, monkeypatch):
    import step01_script_gen.main as m

    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    monkeypatch.setattr(m, "OUTPUT_FILE", tmp_path / "script_result.json")
    monkeypatch.setattr(m, "SCRIPT_FILE", tmp_path / "script_result.json")
    return tmp_path


def _shot(i: int) -> dict:
    return {"index": i, "duration_sec": 5.0, "main_subject": "甲",
            "ff_desc": "甲站立", "lf_desc": "甲转身", "motion_desc": "甲转身",
            "prompt": "[中景] 甲转身", "narration_segment": "",
            "scene": "院内", "location": "院中", "clip_transition": "硬切",
            "context_bridge": "", "frame_layouts": {}, "keyframes": []}


def _script(n: int = 2) -> dict:
    return {"title": "t", "shots": [_shot(i) for i in range(1, n + 1)],
            "characters": [], "hook": None}


def _payload(script: dict, *, content_identity="", source_identity="") -> dict:
    import step01_script_gen.main as m
    from shared.quality_gate import compute_script_revision
    from step01_script_gen.audit import audit_prompt_version

    payload = {
        "status": "ok", "model": "fake-model", "error": "", "issues": [],
        "input_revision": compute_script_revision(script),
        "rule_source_fingerprint": m.RULE_SOURCE_FINGERPRINT,
        "audit_scope": m.STEP01_AUDIT_SCOPE,
        "audit_prompt_version": audit_prompt_version(),
        "audit_focus_version": m._audit_focus_version(),
        "created_at": "2026-11-11T00:00:00",
        "run_id": "test-run",
    }
    if content_identity:
        payload["content_identity"] = content_identity
    if source_identity:
        payload["source_identity"] = source_identity
    return payload


def test_run_id_is_diagnostic_and_not_in_request_bytes():
    from step01_script_gen import audit

    script = _script()
    meta_a = {"input_revision": "r", "run_id": "run-A"}
    meta_b = {"input_revision": "r", "run_id": "run-B"}
    prompt_a = audit.build_audit_prompt("原文", {}, script, metadata=meta_a)
    prompt_b = audit.build_audit_prompt("原文", {}, script, metadata=meta_b)
    assert prompt_a == prompt_b
    assert "run-A" not in prompt_a and "run-B" not in prompt_b
    # payload 仍保留 run_id（诊断字段不因身份剔除而丢失）
    assert audit.content_metadata(meta_a) == {"input_revision": "r"}


def test_content_identity_is_stable_across_runs():
    import step01_script_gen.main as m

    script = _script()
    a = m._final_audit_identity(script, "原文", {}, ["f"], "m", "run-A")
    b = m._final_audit_identity(script, "原文", {}, ["f"], "m", "run-B")
    assert a == b


def test_content_identity_changes_with_novel_plan_and_script():
    import step01_script_gen.main as m

    script = _script()
    base = m._final_audit_identity(script, "原文", {"b": 2}, ["f"], "m", "run")
    assert m._final_audit_identity(script, "改过的原文", {"b": 2}, ["f"], "m", "run") != base
    assert m._final_audit_identity(script, "原文", {"b": 3}, ["f"], "m", "run") != base
    assert m._final_audit_identity(script, "原文", {"b": 2}, ["f2"], "m", "run") != base
    changed = _script()
    changed["shots"][0]["ff_desc"] = "改过之后"
    assert m._final_audit_identity(changed, "原文", {"b": 2}, ["f"], "m", "run") != base


def test_source_identity_changes_with_source_and_plan(chapter):
    import step01_script_gen.main as m

    script = _script()
    base = m._final_audit_source_identity(script, "原文", {"b": 2}, "m")
    assert m._final_audit_source_identity(script, "改过的原文", {"b": 2}, "m") != base
    assert m._final_audit_source_identity(script, "原文", {"b": 3}, "m") != base
    assert m._final_audit_source_identity(_script(3), "原文", {"b": 2}, "m") != base


def test_reuse_requires_matching_content_identity(chapter):
    import step01_script_gen.main as m
    from shared.json_io import atomic_dump_json

    script = _script()
    identity = m._final_audit_identity(script, "原文", {}, ["f"], "m", "run")
    atomic_dump_json(chapter / "step01_audit.json",
                     _payload(script, content_identity=identity),
                     ensure_ascii=False, indent=2)

    assert m._reusable_final_audit(script, expected_identity=identity) is not None
    assert m._reusable_final_audit(script, expected_identity="other") is None


def test_legacy_audit_without_identity_is_rejected_not_trusted(chapter):
    import step01_script_gen.main as m
    from shared.json_io import atomic_dump_json

    script = _script()
    atomic_dump_json(chapter / "step01_audit.json", _payload(script),
                     ensure_ascii=False, indent=2)
    # 旧审计没有身份字段 ⇒ 复用证据不足，必须拒收
    assert m._reusable_final_audit(script, expected_identity="whatever") is None
    # 来源身份同理
    assert m._reusable_final_audit(script, expected_source_identity="whatever") is None
    # 未给期望身份的历史调用保留原语义（生产入口必须给）
    assert m._reusable_final_audit(script) is not None


def test_source_identity_reuse_path(chapter):
    import step01_script_gen.main as m
    from shared.json_io import atomic_dump_json

    script = _script()
    src = m._final_audit_source_identity(script, "原文", {}, "m")
    atomic_dump_json(chapter / "step01_audit.json",
                     _payload(script, source_identity=src),
                     ensure_ascii=False, indent=2)
    assert m._reusable_final_audit(script, expected_source_identity=src) is not None
    assert m._reusable_final_audit(script, expected_source_identity="other") is None
