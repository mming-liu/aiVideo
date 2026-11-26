# -*- coding: utf-8 -*-
"""S36：章节身份绑定与纯身份刷新."""
from __future__ import annotations

import copy

import pytest

from shared.quality_gate import QualityGateError, build_identity_refresh
from shared.run_scope import ensure_script_identity


def _script(**extra):
    out = {"title": "t", "shots": [{"index": 1, "motion_desc": "甲走"}],
           "book_name": "杀青州", "chapter_index": 1}
    out.update(extra)
    return out


def test_identity_fills_missing_fields():
    script = {"title": "t", "shots": []}
    assert ensure_script_identity(script, expected_book_name="杀青州",
                                  expected_chapter=2) == []
    assert script["book_name"] == "杀青州"
    assert script["chapter_index"] == 2


def test_identity_rejects_wrong_book_or_chapter():
    assert ensure_script_identity(_script(book_name="别部"), expected_book_name="杀青州",
                                  expected_chapter=1)[0]["code"] == "identity_book_conflict"
    assert ensure_script_identity(_script(chapter_index=3), expected_book_name="杀青州",
                                  expected_chapter=1)[0]["code"] == "identity_chapter_conflict"
    assert ensure_script_identity(_script(chapter_index="x"), expected_book_name="杀青州",
                                  expected_chapter=1)[0]["code"] == "identity_chapter_invalid"


def test_identity_refresh_allows_only_identity_fields():
    before = _script(chapter_index=None)
    before.pop("chapter_index")
    after = copy.deepcopy(before)
    after["chapter_index"] = 1
    after["script_revision"] = "r2"
    report = build_identity_refresh(
        before, after,
        source_audit={"status": "ok"}, source_commit={"status": "COMMITTED"},
        scope={"book_name": "杀青州", "chapter": 1}, novel_text="正文")
    assert report["identity_only"] is True
    assert report["audit"]["reused"] is True
    assert "chapter_index" in report["changed_fields"]


def test_identity_refresh_rejects_content_change():
    before = _script()
    after = copy.deepcopy(before)
    after["shots"][0]["motion_desc"] = "甲跑"
    with pytest.raises(QualityGateError, match="只允许身份字段"):
        build_identity_refresh(
            before, after,
            source_audit={"status": "ok"}, source_commit={"status": "COMMITTED"},
            scope={"book_name": "杀青州", "chapter": 1})
