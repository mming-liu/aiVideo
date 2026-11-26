# -*- coding: utf-8 -*-
"""S44/P1：计划缓存纳入发布事务的备份/回滚."""
from __future__ import annotations

from shared.publish_txn import PublishTransaction

NAME = "chapter_outline.cache.json"


def test_new_plan_cache_is_removed_on_rollback(tmp_path):
    txn = PublishTransaction(tmp_path, label="test")
    txn.begin()
    (tmp_path / NAME).write_text("new", encoding="utf-8")
    assert (tmp_path / NAME).is_file()
    txn.rollback("test")
    assert not (tmp_path / NAME).exists()


def test_old_plan_cache_is_restored_on_rollback(tmp_path):
    (tmp_path / NAME).write_text("old", encoding="utf-8")
    txn = PublishTransaction(tmp_path, label="test")
    txn.begin()
    (tmp_path / NAME).write_text("new", encoding="utf-8")
    txn.rollback("test")
    assert (tmp_path / NAME).read_text(encoding="utf-8") == "old"


def test_load_script_plan_rejects_snapshot_and_enforces_required_cache(tmp_path):
    import json
    import pytest
    from shared.plan_cache import PlanCacheError, load_script_plan, write_plan_cache

    with pytest.raises(PlanCacheError):
        load_script_plan(tmp_path / "snapshots", {"shots": []})
    with pytest.raises(PlanCacheError):
        load_script_plan(tmp_path / "step01_previous.zip", {"shots": []})

    (tmp_path / "step01_commit.json").write_text(
        json.dumps({"status": "COMMITTED", "plan_cache_required": True}), encoding="utf-8")
    with pytest.raises(PlanCacheError):
        load_script_plan(tmp_path, {"book_name": "甲部", "chapter_index": 2, "script_revision": "r1"})

    write_plan_cache(tmp_path / NAME, {"dialogue_lines": ["测试台词"]},
                     scope={"book_name": "甲部", "chapter": 2}, script_revision="r1")
    loaded = load_script_plan(tmp_path, {"book_name": "甲部", "chapter_index": 2, "script_revision": "r1"})
    assert loaded["dialogue_lines"] == ["测试台词"]
