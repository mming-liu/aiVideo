# -*- coding: utf-8 -*-
"""S36：提交点不得把错章/错部身份写成 COMMITTED.

S04 从退出受控集恢复；内容改为匿名占位，断言与原文件一致。
"""
from __future__ import annotations

from step01_script_gen.orchestrator import commit as commit_mod


def test_commit_rejects_identity_conflict_before_quality(monkeypatch):
    monkeypatch.setenv("BOOK_NAME", "甲部")
    monkeypatch.setenv("NOVEL_CHAPTER", "1")
    monkeypatch.setattr(commit_mod, "_write_blocked_manifest",
                        lambda *a, **k: None)
    script = {"title": "t", "shots": [], "book_name": "乙部", "chapter_index": 1}
    out, summary = commit_mod._commit_step01_outputs(
        script, "", label="test", bind_identity=True)
    assert summary["status"] == "BLOCK"
    assert "身份" in summary["detail"]
    assert out["book_name"] == "乙部"
