# -*- coding: utf-8 -*-
"""恢复入口发送预算文件初始化（2026-11-16 冻结续验缺口）.

根因：resume 只设置 STEP01_SEND_BUDGET_FILE 指向章节目录，却不创建文件；
shared.send_budget.allow() 读不到即 fail-closed「发送预算文件不可读」，
导致修复/审计在发送前被拒、零 HTTP 尝试就 BLOCK。
"""
from __future__ import annotations

import json

from step01_script_gen.resume import _ensure_send_budget_file


def test_creates_zero_budget_when_missing(tmp_path):
    p = tmp_path / "chapter_001" / "step01_send_budget.json"
    assert not p.exists()
    assert _ensure_send_budget_file(p) is True
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data == {"sends": 0, "repairs": 0, "audits": 0}


def test_does_not_overwrite_existing_budget(tmp_path):
    p = tmp_path / "step01_send_budget.json"
    p.write_text(json.dumps({"sends": 5, "repairs": 2, "audits": 3,
                             "note": "keep-me"}), encoding="utf-8")
    before = p.read_bytes()
    assert _ensure_send_budget_file(p) is False
    assert p.read_bytes() == before
    assert json.loads(before)["sends"] == 5


def test_does_not_clobber_malformed_existing_file(tmp_path):
    """已存在但损坏的文件也不覆盖——由 allow() 按 fail-closed 处理，不掩盖。"""
    p = tmp_path / "step01_send_budget.json"
    p.write_text("{broken", encoding="utf-8")
    assert _ensure_send_budget_file(p) is False
    assert p.read_text(encoding="utf-8") == "{broken"


def test_send_budget_allows_after_init(tmp_path, monkeypatch):
    """新建后 allow() 能正常计数（证明修复真的打通发送边界）。"""
    from shared import send_budget
    p = tmp_path / "step01_send_budget.json"
    _ensure_send_budget_file(p)
    monkeypatch.setenv("STEP01_SEND_BUDGET_FILE", str(p))
    send_budget.allow("script_audit")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["sends"] == 1
    assert data["audits"] == 1
