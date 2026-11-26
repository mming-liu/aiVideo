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
