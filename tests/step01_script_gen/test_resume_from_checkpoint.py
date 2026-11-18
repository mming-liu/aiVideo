# -*- coding: utf-8 -*-
"""T1 / M2.3：超时落 PAUSED + resume 从 checkpoint 给出续跑起点（2026-11-18）.

覆盖：
  1. `_record_budget_pause()` 写入字段完整（stage/run_id/revision/时间戳）；
  2. 默认关闭（未设 `STEP01_PAUSED_CHECKPOINT`）时零副作用；
  3. `_budget_exceeded_with_pause()` 只在真超时时落盘，返回结论不变；
  4. resume 检测 PAUSED 后打印 `RESUME_FROM_CPx`，但仍走 pending/审计身份校验；
  5. 无 PAUSED 时默认输出不变；损坏/身份不符的 PAUSED fail-closed。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


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


def _script(marker: str = "A", n: int = 2) -> dict:
    return {"title": "t", "characters": [], "hook": None,
            "shots": [{"index": i, "duration_sec": 5.0, "main_subject": marker,
                       "ff_desc": f"{marker}{i}", "lf_desc": f"{marker}{i}b",
                       "motion_desc": f"{marker}{i}c", "prompt": f"[中景]{marker}{i}",
                       "narration_segment": "", "scene": "院内", "location": "院中",
                       "clip_transition": "硬切", "context_bridge": "",
                       "frame_layouts": {}, "keyframes": []}
                      for i in range(1, n + 1)]}


@pytest.fixture()
def chapter(tmp_path, monkeypatch):
    """最小章节（匿名角色 + 通用场景），形状与正式目录一致."""
    from shared.config import duration_band
    from shared.quality_gate import compute_script_revision
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    monkeypatch.setenv("NOVEL_NAME", "anon_novel")
    monkeypatch.setenv("BOOK_NAME", "anon_book")
    monkeypatch.setenv("BOOK_INDEX", "1")
    monkeypatch.setenv("NOVEL_CHAPTER", "1")

    ch = tmp_path / "output" / "anon_novel" / "part_01_anon_book" / "chapter_001"
    (ch / "snapshots").mkdir(parents=True)
    (ch / "_resume").mkdir(parents=True)

    plan = {"outline": {"scenes": []}}
    (ch / "chapter_outline.cache.json").write_text(
        json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    novel = "第一章 匿名测试原文。"
    novel_path = ch / "_resume" / "novel_abc.txt"
    novel_path.write_bytes(novel.encode("utf-8"))

    audited = _script("A")
    audited_path = ch / "snapshots" / "script_result_1.json"
    audited_path.write_text(json.dumps(audited, ensure_ascii=False), encoding="utf-8")
    rev_a = compute_script_revision(audited)

    audit = {"status": "ok", "input_revision": rev_a,
             "rule_source_fingerprint": RULE_SOURCE_FINGERPRINT,
             "audit_scope": "final_after_deterministic_fixes",
             "audit_prompt_version": "v-test", "audit_focus_version": "f-test",
             "run_id": "run-test", "channel_provider": "agent_plan",
             "model": "model-test",
             "issues": [], "duration_band": duration_band()}
    audit_path = ch / "step01_audit.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")

    pending = _script("B")
    pending_path = ch / "snapshots" / "repair_round_01.json"
    pending_path.write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")

    return {"dir": ch, "novel": novel_path, "audited": audited_path,
            "audit": audit_path, "pending": pending_path,
            "audited_script": audited, "pending_script": pending,
            "revision": rev_a,
            "pending_revision": compute_script_revision(pending)}


def _write_pending_manifest(chapter) -> Path:
    from step01_script_gen import resume

    man = resume.build_pending_manifest(
        chapter["dir"], audited_snapshot_path=chapter["audited"],
        audit_path=chapter["audit"], pending_path=chapter["pending"],
        round_no=1, novel_text_path=chapter["novel"])
    manifest_path = chapter["dir"] / "step01_resume.json"
    manifest_path.write_text(json.dumps(man, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    return manifest_path


def test_timeout_records_paused_with_complete_fields(chapter, monkeypatch):
    import step01_script_gen.main as m
    from shared import step01_checkpoint as ck

    monkeypatch.setattr(m, "CHAPTER_DIR", chapter["dir"])
    monkeypatch.setenv("STEP01_PAUSED_CHECKPOINT", "1")
    ck.write(chapter["dir"], ck.CP3_INITIAL_AUDIT_DONE,
             script=chapter["audited_script"], extra={"run_id": "run-test"})

    rec = m._record_budget_pause("test_timeout", run_id="run-timeout")
    assert rec["written"] is True and rec["error"] == ""
    assert rec["stage"] == ck.PAUSED_AT_CP3_INITIAL_AUDIT_DONE
    assert rec["resume_from"] == "CP3"
    assert rec["run_id"] == "run-timeout"
    assert rec["revision"] == chapter["revision"]
    assert rec["written_at"]

    paused = ck.read_paused(chapter["dir"])
    assert paused["reason"] == "test_timeout"
    assert paused["extra"]["checkpoint_key"] == ck.CP3_INITIAL_AUDIT_DONE
    # 暂停态不污染四态索引
    assert ck.latest(chapter["dir"])["key"] == ck.CP3_INITIAL_AUDIT_DONE


def test_timeout_pause_default_off_has_no_side_effect(tmp_path, monkeypatch):
    import step01_script_gen.main as m
    from shared import step01_checkpoint as ck

    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    monkeypatch.delenv("STEP01_PAUSED_CHECKPOINT", raising=False)
    ck.write(tmp_path, ck.CP2_CANDIDATE_GENERATED, script=_script("A"))

    rec = m._record_budget_pause("test_timeout")
    assert rec["written"] is False
    assert rec["skipped"] is True
    assert ck.read_paused(tmp_path) is None


def test_budget_exceeded_with_pause_only_records_on_timeout(monkeypatch):
    import step01_script_gen.main as m

    calls = []
    monkeypatch.setattr(m, "_record_budget_pause",
                        lambda reason, **kw: calls.append(reason) or {"written": True})
    monkeypatch.setattr(m, "_budget_exceeded", lambda: False)
    assert m._budget_exceeded_with_pause("x") is False
    assert calls == []

    monkeypatch.setattr(m, "_budget_exceeded", lambda: True)
    assert m._budget_exceeded_with_pause("x") is True
    assert calls == ["x"]


def test_resume_reports_resume_from_cp4(chapter, monkeypatch, capsys):
    import step01_script_gen.main as m
    from shared import step01_checkpoint as ck
    from step01_script_gen import resume

    monkeypatch.setattr(m, "CHAPTER_DIR", chapter["dir"])
    manifest_path = _write_pending_manifest(chapter)
    ck.write_paused(chapter["dir"], ck.CP4_REPAIR_ROUND_K_DONE,
                    run_id="run-test", revision=chapter["pending_revision"],
                    reason="budget_exceeded")

    rc = resume.run(manifest_path, dry_run=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "RESUME_FROM_CP4" in out
    assert "PENDING_CANDIDATE_REAUDIT" in out

    start = out.rindex('{\n "ok"')
    data = json.loads(out[start:])
    assert data["resume_from"] == "CP4"
    assert data["paused_stage"] == ck.PAUSED_AT_CP4_REPAIR_ROUND_K_DONE
    assert data["pending_candidate"] == str(chapter["pending"].resolve())


def test_resume_without_paused_has_no_resume_marker(chapter, monkeypatch, capsys):
    import step01_script_gen.main as m
    from step01_script_gen import resume

    monkeypatch.setattr(m, "CHAPTER_DIR", chapter["dir"])
    manifest_path = _write_pending_manifest(chapter)

    rc = resume.run(manifest_path, dry_run=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "RESUME_FROM_" not in out
    data = json.loads(out[out.rindex('{\n "ok"'):])
    assert "resume_from" not in data


def test_corrupt_paused_fail_closed(chapter, monkeypatch):
    import step01_script_gen.main as m
    from step01_script_gen import resume

    monkeypatch.setattr(m, "CHAPTER_DIR", chapter["dir"])
    manifest_path = _write_pending_manifest(chapter)
    (chapter["dir"] / "snapshots" / "_paused.json").write_text(
        "{broken", encoding="utf-8")

    with pytest.raises(resume.ResumeError):
        resume.run(manifest_path, dry_run=True)


def test_paused_revision_mismatch_fail_closed(chapter, monkeypatch):
    import step01_script_gen.main as m
    from shared import step01_checkpoint as ck
    from step01_script_gen import resume

    monkeypatch.setattr(m, "CHAPTER_DIR", chapter["dir"])
    manifest_path = _write_pending_manifest(chapter)
    ck.write_paused(chapter["dir"], ck.CP3_INITIAL_AUDIT_DONE,
                    run_id="run-test", revision="not-this-revision",
                    reason="budget_exceeded")

    with pytest.raises(resume.ResumeError):
        resume.run(manifest_path, dry_run=True)
