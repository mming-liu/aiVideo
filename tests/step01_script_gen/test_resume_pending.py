# -*- coding: utf-8 -*-
"""M-A2：修后稿恢复指针（`step01_resume.json` 双块清单）离线验收（2026-11-17）.

要证明的：
  1. 清单同时记录 **已审稿（+审计身份）** 与 **待复审稿（requires_fresh_audit）**，
     并给出 `next_action`；
  2. `load_pending_script()` 从**实际入口**读回待复审稿（不是靠人记路径）；
  3. 保留硬约束——审计只证明同 revision 稿件：待复审稿与已审稿同 revision 直接拒；
  4. 摘要/修订点/文件缺失三类失败都有明确语义。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


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
    """一个最小章节目录：计划缓存 + 原文 + 已审快照 + 审计 + 待复审稿."""
    from shared.quality_gate import compute_script_revision
    from step01_script_gen import resume
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    ch = tmp_path / "chapter_001"
    (ch / "snapshots").mkdir(parents=True)
    (ch / "_resume").mkdir(parents=True)

    plan = {"outline": {"scenes": []}}
    (ch / "chapter_outline.cache.json").write_text(
        json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    novel = "第一章 测试原文。"
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
             "issues": [], "duration_band": {"min": 60.0, "max": 90.0,
                                             "target_min": 70.0, "target_max": 80.0}}
    audit_path = ch / "step01_audit.json"
    audit_path.write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")

    pending = _script("B")
    pending_path = ch / "snapshots" / "repair_round_01.json"
    pending_path.write_text(json.dumps(pending, ensure_ascii=False), encoding="utf-8")

    return {"dir": ch, "novel": novel_path, "audited": audited_path,
            "audit": audit_path, "pending": pending_path,
            "audited_script": audited, "pending_script": pending,
            "revision": rev_a}


def _build(chapter, *, round_no=1):
    from step01_script_gen import resume

    return resume.build_pending_manifest(
        chapter["dir"], audited_snapshot_path=chapter["audited"],
        audit_path=chapter["audit"], pending_path=chapter["pending"],
        round_no=round_no, novel_text_path=chapter["novel"])


def test_manifest_has_both_blocks_and_next_action(chapter):
    from step01_script_gen import resume

    man = _build(chapter)
    assert man["next_action"] == "reaudit_pending_candidate"
    assert man["audited"]["script_revision"] == chapter["revision"]
    assert man["audited"]["audit_path"] == str(chapter["audit"].resolve())
    pc = man["pending_candidate"]
    assert pc["requires_fresh_audit"] is True
    assert pc["status"] == "pending_reaudit" and pc["round"] == 1
    assert pc["script_revision"] != man["script_revision"]
    # 清单本身仍能过静态校验（快照↔审计同 revision 的硬约束没被破坏）
    resume.validate_static(man)


def test_load_pending_script_reads_actual_file(chapter):
    from step01_script_gen import resume

    man = _build(chapter)
    script, note = resume.load_pending_script(man)
    assert script == chapter["pending_script"]
    assert "待复审稿继续" in note


def test_no_pending_returns_none(chapter):
    from step01_script_gen import resume

    assert resume.load_pending_script({}) == (None, "")


def test_same_revision_pending_is_rejected(chapter):
    from step01_script_gen import resume

    man = _build(chapter)
    man["pending_candidate"]["path"] = str(chapter["audited"])   # 拿已审稿冒充待复审稿
    man["pending_candidate"].pop("sha256", None)
    man["pending_candidate"]["script_revision"] = man["script_revision"]
    with pytest.raises(resume.ResumeError, match="同 revision"):
        resume.load_pending_script(man)


def test_build_rejects_same_revision_pending(chapter):
    from step01_script_gen import resume

    with pytest.raises(resume.ResumeError, match="同 revision"):
        resume.build_pending_manifest(
            chapter["dir"], audited_snapshot_path=chapter["audited"],
            audit_path=chapter["audit"], pending_path=chapter["audited"],
            round_no=1, novel_text_path=chapter["novel"])


def test_sha_mismatch_is_rejected(chapter):
    from step01_script_gen import resume

    man = _build(chapter)
    chapter["pending"].write_text(json.dumps(_script("C"), ensure_ascii=False),
                                  encoding="utf-8")
    with pytest.raises(resume.ResumeError, match="摘要不符"):
        resume.load_pending_script(man)


def test_missing_pending_is_rejected(chapter):
    from step01_script_gen import resume

    man = _build(chapter)
    chapter["pending"].unlink()
    with pytest.raises(resume.ResumeError, match="不存在"):
        resume.load_pending_script(man)


def test_audited_snapshot_must_match_audit_revision(chapter):
    """审计只证明同 revision 稿件：已审快照与审计不符时，清单构建直接拒."""
    from shared.quality_gate import compute_script_revision
    from step01_script_gen import resume

    other = chapter["dir"] / "snapshots" / "script_result_2.json"
    other.write_text(json.dumps(_script("Z"), ensure_ascii=False), encoding="utf-8")
    assert compute_script_revision(_script("Z")) != chapter["revision"]
    with pytest.raises(resume.ResumeError, match="同一修订点"):
        resume.build_pending_manifest(
            chapter["dir"], audited_snapshot_path=other,
            audit_path=chapter["audit"], pending_path=chapter["pending"],
            round_no=1, novel_text_path=chapter["novel"])
