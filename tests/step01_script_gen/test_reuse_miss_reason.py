# -*- coding: utf-8 -*-
"""M2.3：复用失配键必须精确（2026-11-16）.

要证明的：`_reusable_final_audit` 不再只给一句「content_identity 不匹配
（原文/计划/定向项/契约已变）」，而是对每一项**单独命名**，尤其把
`audit_focus`（定向项）从其余四项里分出来——这正是 `19:00` / `22:35` 两次
复现、却被文档误归因到「确定性层改了内容身份」的那一格。
"""
from __future__ import annotations

import pytest


def _script(n: int = 2) -> dict:
    shots = []
    for i in range(1, n + 1):
        shots.append({
            "index": i, "duration_sec": 5.0, "main_subject": "甲",
            "ff_desc": "甲站立", "lf_desc": "甲转身", "motion_desc": "甲转身",
            "prompt": "[中景] 甲转身", "narration_segment": "",
            "scene": "院内", "location": "院中", "clip_transition": "硬切",
            "context_bridge": "", "frame_layouts": {}, "keyframes": [],
        })
    return {"title": "t", "shots": shots, "characters": [], "hook": None}


def _payload(script: dict, **over) -> dict:
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
        "created_at": "2026-11-16T00:00:00",
        "run_id": "test-run",
        "content_identity": "cid-ok",
        "source_identity": "src-ok",
    }
    # 通道判据只在配置了审计专用通道时生效——按本进程的生效值写进 payload，
    # 否则"全部相符"的样本会被通道判据判成失配（那是另一条契约，不该混进本用例）。
    from step01_script_gen.audit import resolve_audit_channel
    _cp, _cm = resolve_audit_channel()
    if _cp is not None:
        payload["channel_provider"] = _cp
        payload["model"] = _cm
    payload.update(over)
    return payload


def _key(payload: dict, *, script: dict, **over):
    import step01_script_gen.main as m
    from shared.quality_gate import compute_script_revision

    kw = {"revision": compute_script_revision(script),
          "expected_identity": "cid-ok",
          "expected_source_identity": "src-ok",
          "expected_identity_without_focus": None}
    kw.update(over)
    return m._reuse_miss_key(payload, **kw)


def test_all_match_is_reusable():
    script = _script()
    assert _key(_payload(script), script=script) == ""


def test_status_not_ok_is_named():
    script = _script()
    assert _key(_payload(script, status="request_failed"), script=script) == "status_not_ok"


def test_missing_identity_is_named():
    script = _script()
    assert _key(_payload(script, content_identity=""), script=script) == "content_identity_missing"
    assert (_key(_payload(script, source_identity=""), script=script,
                 expected_identity=None) == "source_identity_missing")


def test_focus_only_mismatch_is_separated_from_content():
    """同稿同契约、只有定向项不同 → 必须报 audit_focus，而不是笼统的 content_identity."""
    script = _script()
    payload = _payload(script, content_identity="cid-without-focus")
    assert _key(payload, script=script,
                expected_identity_without_focus="cid-without-focus") == "audit_focus"
    # 没给无 focus 探针时退回粗粒度（不猜）
    assert _key(payload, script=script) == "content_identity"


def test_real_content_mismatch_stays_content_identity():
    script = _script()
    payload = _payload(script, content_identity="cid-from-other-script")
    assert _key(payload, script=script,
                expected_identity_without_focus="cid-without-focus") == "content_identity"


@pytest.mark.parametrize("field,value,expected", [
    ("input_revision", "deadbeef", "input_revision"),
    ("rule_source_fingerprint", "0000", "rule_source_fingerprint"),
    ("audit_scope", "other_scope", "audit_scope"),
    ("audit_prompt_version", "old", "audit_prompt_version"),
    ("audit_focus_version", "old", "audit_focus_version"),
])
def test_each_contract_field_is_named(field, value, expected):
    script = _script()
    assert _key(_payload(script, **{field: value}), script=script) == expected


def test_duration_band_is_named():
    script = _script()
    import step01_script_gen.main as m
    band = dict(m.duration_band())
    band["min"] = float(band["min"]) + 1.0
    assert _key(_payload(script, duration_band=band), script=script) == "duration_band"


def test_channel_is_named(monkeypatch):
    import step01_script_gen.audit as audit
    script = _script()
    monkeypatch.setattr(audit, "resolve_audit_channel", lambda: ("agent_plan", "model-B"))
    payload = _payload(script, channel_provider="agent_plan", model="model-A")
    assert _key(payload, script=script) == "channel_model"


def test_last_reuse_miss_records_key_and_both_sides(chapter):
    """失配读数要能被提交摘要/测试直接读，不必再从日志里猜."""
    import step01_script_gen.main as m

    script = _script()
    _write(chapter, "step01_audit.json", _payload(script, input_revision="deadbeef"))
    assert m._reusable_final_audit(script, expected_identity="cid-ok") is None
    miss = m.last_reuse_miss()
    assert miss["key"] == "input_revision"
    assert miss["detail"]["field"] == "input_revision"
    assert miss["detail"]["audit"] == "deadbeef"
    assert miss["detail"]["expected"] != "deadbeef"


@pytest.fixture
def chapter(tmp_path, monkeypatch):
    import step01_script_gen.main as m

    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    monkeypatch.setattr(m, "OUTPUT_FILE", tmp_path / "script_result.json")
    monkeypatch.setattr(m, "SCRIPT_FILE", tmp_path / "script_result.json")
    monkeypatch.delenv("STEP01_AUDIT_SOURCE", raising=False)
    return tmp_path


def _write(chapter, name: str, payload: dict) -> None:
    import json

    (chapter / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

def test_revision_mismatch_is_not_masked_by_content_identity():
    """19:00 定案：不同稿件先报 input_revision，不能被聚合身份遮住。"""
    script = _script()
    payload = _payload(script, input_revision="deadbeef",
                       content_identity="cid-from-other-script")
    assert _key(payload, script=script) == "input_revision"


def test_prompt_version_is_not_masked_by_content_identity():
    script = _script()
    payload = _payload(script, audit_prompt_version="old",
                       content_identity="cid-from-other-script")
    assert _key(payload, script=script) == "audit_prompt_version"
