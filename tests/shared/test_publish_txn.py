# -*- coding: utf-8 -*-
"""S06：发布事务（真实产物清单 / 锁 / 回滚 / 崩溃恢复）."""
import json

import pytest

import shared.publish_txn as pt
from shared.publish_txn import PublishError, PublishTransaction, recover_pending


def _seed(chapter):
    (chapter / "script_result.json").write_text(
        json.dumps({"title": "t", "shots": []}, ensure_ascii=False), encoding="utf-8")
    (chapter / "step01_audit.json").write_text(
        json.dumps({"status": "ok"}), encoding="utf-8")


def test_rollback_restores_real_artifacts_and_drops_created(tmp_path):
    _seed(tmp_path)
    txn = PublishTransaction(tmp_path, label="t")
    txn.begin()
    (tmp_path / "script_result.json").write_text(
        json.dumps({"title": "t", "shots": [1]}), encoding="utf-8")
    (tmp_path / "step01_quality.json").write_text("{}", encoding="utf-8")
    (tmp_path / "step01_commit.json").write_text("{}", encoding="utf-8")
    restored = txn.rollback("test")
    assert "script_result.json" in restored
    assert json.loads((tmp_path / "script_result.json").read_text(encoding="utf-8"))["shots"] == []
    assert not (tmp_path / "step01_quality.json").exists()
    assert not (tmp_path / "step01_commit.json").exists()
    assert not (tmp_path / ".publish.lock").exists()
    journal = json.loads((tmp_path / "step01_publish_journal.json").read_text(encoding="utf-8"))
    assert journal["state"] == "rolled_back"


def test_second_begin_is_refused_while_locked(tmp_path):
    _seed(tmp_path)
    first = PublishTransaction(tmp_path, label="a").begin()
    with pytest.raises(PublishError):
        PublishTransaction(tmp_path, label="b").begin()
    first.commit()
    assert not (tmp_path / ".publish.lock").exists()


def test_expected_old_revision_mismatch_refuses(tmp_path):
    _seed(tmp_path)
    with pytest.raises(PublishError):
        PublishTransaction(tmp_path, expected_old="deadbeef" * 8, label="t").begin()
    assert not (tmp_path / ".publish.lock").exists()


def test_commit_keeps_new_files_and_clears_lock(tmp_path):
    _seed(tmp_path)
    txn = PublishTransaction(tmp_path, label="t")
    txn.begin()
    (tmp_path / "script_result.json").write_text(
        json.dumps({"title": "t", "shots": [1]}), encoding="utf-8")
    txn.commit()
    assert json.loads((tmp_path / "script_result.json").read_text(encoding="utf-8"))["shots"] == [1]
    assert not (tmp_path / ".publish.lock").exists()


def test_partial_rollback_failure_keeps_lock_and_reports(tmp_path, monkeypatch):
    _seed(tmp_path)
    txn = PublishTransaction(tmp_path, label="fail")
    txn.begin()
    real = pt._atomic_write_bytes

    def fake(path, data):
        if str(path).endswith("script_result.json"):
            raise OSError("disk full")
        return real(path, data)

    monkeypatch.setattr(pt, "_atomic_write_bytes", fake)
    with pytest.raises(PublishError):
        txn.rollback("test")
    assert (tmp_path / ".publish.lock").exists()
    journal = json.loads((tmp_path / "step01_publish_journal.json").read_text(encoding="utf-8"))
    assert journal["state"] == "rollback_failed"


def test_recover_pending_restores_crashed_publish(tmp_path):
    _seed(tmp_path)
    txn = PublishTransaction(tmp_path, label="crash")
    txn.begin()
    (tmp_path / "script_result.json").write_text(
        json.dumps({"title": "t", "shots": [9]}), encoding="utf-8")
    (tmp_path / "step01_adoption.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".publish.lock").write_text("999999 dead", encoding="utf-8")
    out = recover_pending(tmp_path)
    assert out["state"] == "recovered"
    assert json.loads((tmp_path / "script_result.json").read_text(encoding="utf-8"))["shots"] == []
    assert not (tmp_path / "step01_adoption.json").exists()

def test_missing_backup_dir_is_not_reported_as_recovered(tmp_path):
    _seed(tmp_path)
    (tmp_path / "step01_publish_journal.json").write_text(json.dumps({
        "state": "begun", "backup_dir": "_publish_backup_gone",
        "backed_up": ["script_result.json"], "created": [],
    }), encoding="utf-8")
    out = recover_pending(tmp_path)
    assert out["ok"] is False and out["state"] == "rollback_failed"
    assert out["failed"]
    journal = json.loads((tmp_path / "step01_publish_journal.json").read_text(encoding="utf-8"))
    assert journal["state"] == "rollback_failed"


def test_unreadable_journal_blocks_new_transaction(tmp_path):
    _seed(tmp_path)
    (tmp_path / "step01_publish_journal.json").write_text("{not json", encoding="utf-8")
    out = recover_pending(tmp_path)
    assert out["ok"] is False and out["state"] == "unreadable"
    with pytest.raises(PublishError):
        PublishTransaction(tmp_path, label="after-bad-journal").begin()
    assert not (tmp_path / ".publish.lock").exists()


def test_incomplete_recovery_blocks_begin_and_keeps_old_journal(tmp_path):
    _seed(tmp_path)
    (tmp_path / "step01_publish_journal.json").write_text(json.dumps({
        "state": "begun", "backup_dir": "_publish_backup_gone",
        "backed_up": ["script_result.json"], "created": [],
    }), encoding="utf-8")
    with pytest.raises(PublishError):
        PublishTransaction(tmp_path, label="blocked").begin()
    journal = json.loads((tmp_path / "step01_publish_journal.json").read_text(encoding="utf-8"))
    assert journal["state"] == "rollback_failed"
    assert not (tmp_path / ".publish.lock").exists()

