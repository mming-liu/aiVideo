# -*- coding: utf-8 -*-
"""M-A2 端到端：`resume.run()` 的 pending_candidate dry-run 分支（零调用）.

单测此前只覆盖清单构建/读取；本用例走**实际入口** `run(..., dry_run=True)`，
证明：

  1. 清单含 `pending_candidate` 时打印 `PENDING_CANDIDATE_REAUDIT`；
  2. 跳过「审计可复用」闸门（不报 `AUDIT_WILL_BE_REFRESHED`、`audit_reusable=false`）；
  3. 以**修后稿**的 revision 作为本次提交对象，`next_action` 保留；
  4. 只在临时章节目录落 CP1 检查点，不写正式产物、不发外部请求
     （外层 conftest 已安装断网护栏）。
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
    """按正式目录形状放一个最小章节，避免 run() 的 OUTPUT_BASE 推导越界."""
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


def test_run_dry_run_pending_candidate_skips_reuse(chapter, monkeypatch, capsys):
    from step01_script_gen import resume
    import step01_script_gen.main as m

    # main 的 CHAPTER_DIR 在 import 期固定；测试里显式指向临时章节，
    # 保证 run() 的「章节目录相符」断言成立且不碰生产目录。
    monkeypatch.setattr(m, "CHAPTER_DIR", chapter["dir"])

    man = resume.build_pending_manifest(
        chapter["dir"], audited_snapshot_path=chapter["audited"],
        audit_path=chapter["audit"], pending_path=chapter["pending"],
        round_no=1, novel_text_path=chapter["novel"])
    manifest_path = chapter["dir"] / "step01_resume.json"
    manifest_path.write_text(json.dumps(man, ensure_ascii=False, indent=1),
                             encoding="utf-8")

    rc = resume.run(manifest_path, dry_run=True)
    out = capsys.readouterr().out
    assert rc == 0
    assert "PENDING_CANDIDATE_REAUDIT" in out
    assert "AUDIT_WILL_BE_REFRESHED" not in out

    start = out.rindex('{\n "ok"')
    data = json.loads(out[start:])
    assert data["ok"] is True
    assert data["audit_reusable"] is False
    assert data["next_action"] == "reaudit_pending_candidate"
    assert data["script_revision"] == chapter["pending_revision"]
    assert data["pending_candidate"] == str(chapter["pending"].resolve())

    # 只在临时章节落 CP1 检查点；没有正式产物路径参与。
    ckpt = chapter["dir"] / "snapshots" / "cp_CP1_PLAN_VERIFIED.json"
    assert ckpt.is_file()
    assert json.loads(ckpt.read_text(encoding="utf-8"))["stage"] == "CP1_PLAN_VERIFIED"
    assert str(chapter["dir"]) in str(ckpt)
