# -*- coding: utf-8 -*-
"""S36/P1：章节身份绑定与纯身份刷新.

S04 从退出受控集恢复；内容改为匿名占位（甲部/乙部），断言与原文件一致。
"""
from __future__ import annotations

import copy

import pytest

from shared.quality_gate import (QualityGateError, build_identity_refresh,
                                 compute_script_revision)
from shared.run_scope import ensure_script_identity


def _script(**extra):
    out = {"title": "t", "shots": [{"index": 1, "motion_desc": "甲走"}],
           "book_name": "甲部", "chapter_index": 1}
    out.update(extra)
    return out


def _records(before):
    rev = compute_script_revision(before)
    return ({"status": "ok", "input_revision": rev},
            {"status": "COMMITTED", "script_revision": rev})


def test_identity_fills_missing_fields():
    script = {"title": "t", "shots": []}
    assert ensure_script_identity(script, expected_book_name="甲部",
                                  expected_chapter=2) == []
    assert script["book_name"] == "甲部"
    assert script["chapter_index"] == 2


def test_identity_rejects_wrong_types_and_conflicts():
    assert ensure_script_identity(_script(book_name="乙部"), expected_book_name="甲部",
                                  expected_chapter=1)[0]["code"] == "identity_book_conflict"
    assert ensure_script_identity(_script(chapter_index=3), expected_book_name="甲部",
                                  expected_chapter=1)[0]["code"] == "identity_chapter_conflict"
    for bad in (True, 1.9, "1"):
        code = ensure_script_identity(_script(chapter_index=bad),
                                      expected_book_name="甲部",
                                      expected_chapter=1)[0]["code"]
        assert code == "identity_chapter_invalid"


def test_identity_refresh_allows_only_missing_identity_fields():
    before = _script()
    before.pop("chapter_index")
    after = copy.deepcopy(before)
    after["chapter_index"] = 1
    after["script_revision"] = "r2"
    audit, commit = _records(before)
    report = build_identity_refresh(
        before, after, source_audit=audit, source_commit=commit,
        scope={"book_name": "甲部", "chapter": 1}, novel_text="正文")
    assert report["identity_only"] is True
    assert report["audit"]["reused"] is True
    assert "chapter_index" in report["changed_fields"]


def test_identity_refresh_rejects_forged_source_records():
    before = _script()
    before.pop("chapter_index")
    after = copy.deepcopy(before)
    after["chapter_index"] = 1
    with pytest.raises(QualityGateError, match="来源审计不绑定"):
        build_identity_refresh(
            before, after,
            source_audit={"status": "ok", "input_revision": "f" * 64},
            source_commit={"status": "COMMITTED", "script_revision": "e" * 64},
            scope={"book_name": "甲部", "chapter": 1})


def test_identity_refresh_rejects_changing_existing_identity():
    before = _script(book_name="乙部")
    before.pop("chapter_index")
    after = copy.deepcopy(before)
    after["book_name"] = "甲部"
    audit, commit = _records(before)
    with pytest.raises(QualityGateError, match="已有 book_name"):
        build_identity_refresh(
            before, after, source_audit=audit, source_commit=commit,
            scope={"book_name": "甲部", "chapter": 1})


def test_identity_refresh_rejects_content_change():
    before = _script()
    before.pop("chapter_index")
    after = copy.deepcopy(before)
    after["chapter_index"] = 1
    after["shots"][0]["motion_desc"] = "甲跑"
    audit, commit = _records(before)
    with pytest.raises(QualityGateError, match="只允许身份字段"):
        build_identity_refresh(
            before, after, source_audit=audit, source_commit=commit,
            scope={"book_name": "甲部", "chapter": 1})
