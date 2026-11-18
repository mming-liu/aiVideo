# -*- coding: utf-8 -*-
"""T3 / M2.4：resume 自动消费修复轮指针的 requires_fresh_audit 态（2026-11-18）.

覆盖：
  1. 合法指针 → 自动构造等效 pending 清单，输出与显式清单一致的
     `PENDING_CANDIDATE_REAUDIT` / `next_action`；
  2. 无指针 → 走原显式清单路径（本文件只证明自动入口 fail-closed）；
  3. 损坏/缺失/未 written/requires_fresh_audit=false/同 revision → fail-closed；
  4. `--dry-run` 下零正式产物、零 provider 调用。
"""
from __future__ import annotations

import json
import os
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


_ENV_KEYS = ("DURATION_MIN_SEC", "DURATION_MAX_SEC", "DURATION_TARGET_MIN_SEC",
             "DURATION_TARGET_MAX_SEC", "NOVEL_NAME", "BOOK_NAME", "BOOK_INDEX",
             "NOVEL_CHAPTER", "OUTPUT_BASE", "STEP01_AUDIT_SOURCE",
             "STEP01_SEND_BUDGET_FILE", "STEP01_PAUSED_CHECKPOINT")


@pytest.fixture(autouse=True)
def _restore_env():
    before = {k: os.environ.get(k) for k in _ENV_KEYS}
    yield
    for key, value in before.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture()
def chapter(tmp_path, monkeypatch):
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

    (ch / "chapter_outline.cache.json").write_text(
        json.dumps({"outline": {"scenes": []}}, ensure_ascii=False),
        encoding="utf-8")
    novel_path = ch / "_resume" / "novel_abc.txt"
    novel_path.write_bytes("第一章 匿名测试原文。".encode("utf-8"))

    audited = _script("A")
    audited_path = ch / "snapshots" / "script_result_1.json"
    audited_path.write_text(json.dumps(audited, ensure_ascii=False), encoding="utf-8")
    rev_a = compute_script_revision(audited)

    audit = {"status": "ok", "input_revision": rev_a,
             "rule_source_fingerprint": RULE_SOURCE_FINGERPRINT,
             "audit_scope": "final_after_deterministic_fixes",
             "audit_prompt_version": "v-test", "audit_focus_version": "f-test",
             "run_id": "run-test", "channel_provider": "agent_plan",
             "model": "model-test", "issues": [],
             "duration_band": duration_band()}
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


def _write_pointer(chapter, *, pending=None, revision=None, requires_fresh=True,
                   written=True, error="", round_no=1, hint_path=None,
                   audit_revision=None):
    rec = {
        "round": int(round_no),
        "path": str(pending or chapter["pending"]),
        "revision": str(revision if revision is not None
                        else chapter["pending_revision"]),
        "written": bool(written),
        "requires_fresh_audit": bool(requires_fresh),
        "error": str(error),
        "audit_path": str(chapter["audit"]),
        "audit_revision": str(audit_revision if audit_revision is not None
                              else chapter["revision"]),
    }
    if hint_path:
        rec["resume_manifest"] = {"path": str(hint_path), "written": True,
                                  "next_action": "reaudit_pending_candidate"}
    path = chapter["dir"] / "snapshots" / "_repair_round_pointer.json"
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _write_hint_manifest(chapter) -> Path:
    from step01_script_gen import resume

    man = resume.build_pending_manifest(
        chapter["dir"], audited_snapshot_path=chapter["audited"],
        audit_path=chapter["audit"], pending_path=chapter["pending"],
        round_no=1, novel_text_path=chapter["novel"])
    hint = chapter["dir"] / "snapshots" / "resume_hint.json"
    hint.write_text(json.dumps(man, ensure_ascii=False, indent=1), encoding="utf-8")
    return hint


def test_auto_pointer_builds_pending_manifest_with_hint(chapter):
    from step01_script_gen import resume

    hint = _write_hint_manifest(chapter)
    _write_pointer(chapter, hint_path=hint)
    man = resume.build_auto_pointer_manifest(chapter["dir"])

    assert man["stage"] == resume.PENDING_STATUS
    assert man["next_action"] == "reaudit_pending_candidate"
    assert man["pending_candidate"]["path"] == str(chapter["pending"].resolve())
    assert man["pending_candidate"]["requires_fresh_audit"] is True
    assert man["pending_candidate"]["script_revision"] == chapter["pending_revision"]
    assert man["script_revision"] == chapter["revision"]
    assert man["audited"]["snapshot_path"] == str(chapter["audited"].resolve())
    assert man["auto_pointer"]["source"] == "repair_round_pointer"
    assert man["auto_pointer"]["pointer_round"] == 1


def test_auto_pointer_builds_without_hint(chapter):
    """不依赖指针自带的清单：按 revision/目录形状精确发现已审稿与冻结原文."""
    from step01_script_gen import resume

    _write_pointer(chapter)
    man = resume.build_auto_pointer_manifest(chapter["dir"])
    assert man["pending_candidate"]["script_revision"] == chapter["pending_revision"]
    assert man["script_revision"] == chapter["revision"]


def test_auto_pointer_dry_run_zero_formal_artifacts_and_calls(chapter, monkeypatch, capsys):
    import step01_script_gen.main as m
    from step01_script_gen import resume

    monkeypatch.setattr(m, "CHAPTER_DIR", chapter["dir"])
    _write_pointer(chapter)

    def _boom(*a, **k):
        raise AssertionError("auto pointer dry-run must not call the model")

    import step01_script_gen.audit as audit
    monkeypatch.setattr(audit, "run_same_model_audit_with_status", _boom)

    before = {p.name for p in chapter["dir"].iterdir()}
    rc = resume.main(["--chapter-dir", str(chapter["dir"]), "--dry-run"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "AUTO_POINTER_FOUND" in out
    assert "PENDING_CANDIDATE_REAUDIT" in out
    assert "reaudit_pending_candidate" in out

    data = json.loads(out[out.rindex('{\n "ok"'):])
    assert data["ok"] is True
    assert data["discovery"] == "auto_pointer"
    assert data["next_action"] == "reaudit_pending_candidate"
    assert data["pending_candidate"] == str(chapter["pending"].resolve())
    assert data["script_revision"] == chapter["pending_revision"]

    after = {p.name for p in chapter["dir"].iterdir()}
    assert "step01_resume.json" not in after
    assert "step01_commit.json" not in after
    assert after - before <= {"step01_send_budget.json"}


def test_no_pointer_fail_closed(chapter, capsys):
    from step01_script_gen import resume

    with pytest.raises(resume.ResumeError) as exc:
        resume.build_auto_pointer_manifest(chapter["dir"])
    assert "未发现修复轮指针" in str(exc.value)

    rc = resume.main(["--chapter-dir", str(chapter["dir"]), "--dry-run"])
    assert rc == 3
    assert "拒绝恢复" in capsys.readouterr().err


def test_corrupt_pointer_fail_closed(chapter):
    from step01_script_gen import resume

    (chapter["dir"] / "snapshots" / "_repair_round_pointer.json").write_text(
        "{broken", encoding="utf-8")
    with pytest.raises(resume.ResumeError) as exc:
        resume.build_auto_pointer_manifest(chapter["dir"])
    assert "不可解析" in str(exc.value)


def test_pointer_written_false_fail_closed(chapter):
    from step01_script_gen import resume

    _write_pointer(chapter, written=False)
    with pytest.raises(resume.ResumeError) as exc:
        resume.build_auto_pointer_manifest(chapter["dir"])
    assert "written=true" in str(exc.value)


def test_pointer_error_field_fail_closed(chapter):
    from step01_script_gen import resume

    _write_pointer(chapter, error="disk full")
    with pytest.raises(resume.ResumeError) as exc:
        resume.build_auto_pointer_manifest(chapter["dir"])
    assert "写入错误" in str(exc.value)


def test_requires_fresh_audit_false_fail_closed(chapter):
    from step01_script_gen import resume

    _write_pointer(chapter, requires_fresh=False)
    with pytest.raises(resume.ResumeError) as exc:
        resume.build_auto_pointer_manifest(chapter["dir"])
    assert "requires_fresh_audit" in str(exc.value)


def test_pending_snapshot_missing_fail_closed(chapter):
    from step01_script_gen import resume

    missing = chapter["dir"] / "snapshots" / "repair_round_99.json"
    _write_pointer(chapter, pending=missing)
    with pytest.raises(resume.ResumeError) as exc:
        resume.build_auto_pointer_manifest(chapter["dir"])
    assert "不存在" in str(exc.value)


def test_same_revision_is_not_pending(chapter):
    from step01_script_gen import resume

    # 指针指向的「待复审稿」与已审稿同一份 → 不需要重审，必须拒。
    _write_pointer(chapter, pending=chapter["audited"],
                   revision=chapter["revision"],
                   audit_revision=chapter["revision"])
    with pytest.raises(resume.ResumeError) as exc:
        resume.build_auto_pointer_manifest(chapter["dir"])
    assert "同 revision" in str(exc.value)


def test_pointer_outside_snapshots_fail_closed(chapter, tmp_path):
    from step01_script_gen import resume

    outside = tmp_path / "elsewhere.json"
    outside.write_text(json.dumps(_script("B"), ensure_ascii=False), encoding="utf-8")
    _write_pointer(chapter, pending=outside)
    with pytest.raises(resume.ResumeError) as exc:
        resume.build_auto_pointer_manifest(chapter["dir"])
    assert "snapshots/" in str(exc.value)
