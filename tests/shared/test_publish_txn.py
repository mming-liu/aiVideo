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


def _seed_full_six(chapter, rev="rev-v1"):
    import hashlib
    from shared.json_io import atomic_dump_json
    script = {"title": "v1", "shots": [], "script_revision": rev}
    atomic_dump_json(chapter / "script_result.json", script)
    atomic_dump_json(chapter / "chapter_outline.cache.json", {"schema": "step01.chapter_outline.cache.v2"})
    atomic_dump_json(chapter / "step01_quality.json", {"status": "PASS", "duration_band": {"min": 60, "max": 120, "target_min": 85, "target_max": 110}})
    atomic_dump_json(chapter / "step01_audit.json", {"status": "ok", "issues": []})
    atomic_dump_json(chapter / "step01_commit.json", {"status": "COMMITTED", "script_revision": rev})
    sha = hashlib.sha256((chapter / "script_result.json").read_bytes()).hexdigest()
    atomic_dump_json(chapter / "step01_adoption.json", {
        "schema": "step01.formal_adoption/1", "status": "adopted_and_verified",
        "formal_chapter": str(chapter), "output_base_override": str(chapter.parent),
        "script_revision": rev,
        "scope": {"novel": "匿名作品", "book_index": 1, "book_name": "第一部", "chapter": 1},
        "formal_sha256": {"script_result.json": sha},
        "effective_config": {"DURATION_MIN_SEC": 60, "DURATION_MAX_SEC": 90,
                             "DURATION_TARGET_MIN_SEC": 70, "DURATION_TARGET_MAX_SEC": 80},
    })


def test_first_publish_without_previous_files_skips_empty_zip(tmp_path):
    txn = PublishTransaction(tmp_path, label="first")
    txn.begin()
    (tmp_path / "script_result.json").write_text('{"shots":[]}', encoding="utf-8")
    txn.commit()
    from shared.paths import step01_previous_zip_for
    assert not step01_previous_zip_for(tmp_path).exists()
    assert not list(tmp_path.glob("_publish_backup_*"))


def test_commit_creates_verified_previous_zip_and_rotates_backups(tmp_path):
    import zipfile
    from shared.paths import step01_previous_zip_for

    _seed_full_six(tmp_path, "rev-v1")
    old_script_bytes = (tmp_path / "script_result.json").read_bytes()
    txn = PublishTransaction(tmp_path, label="v2")
    txn.begin()
    (tmp_path / "script_result.json").write_text('{"title":"v2","shots":[]}', encoding="utf-8")
    txn.commit()

    zpath = step01_previous_zip_for(tmp_path)
    manifest = pt.verify_previous_backup(zpath)
    assert manifest["previous_revision"] == "rev-v1"
    assert manifest["scope"]["novel"] == "匿名作品"
    assert manifest["effective_config"]["DURATION_MAX_SEC"] == 90
    assert all(manifest["files"][name]["exists"] for name in pt.PUBLISH_FILES)
    with zipfile.ZipFile(zpath, "r") as zf:
        assert zf.read("script_result.json") == old_script_bytes
    assert not list(tmp_path.glob("_publish_backup_*"))


def test_failed_publish_preserves_existing_previous_zip_and_removes_created(tmp_path):
    from shared.paths import step01_previous_zip_for

    # 只先有 script_result.json 和 step01_audit.json，生成第一份 step01_previous.zip
    _seed(tmp_path)
    txn1 = PublishTransaction(tmp_path, label="v2")
    txn1.begin()
    (tmp_path / "script_result.json").write_text(json.dumps({"title": "v2", "shots": [1]}), encoding="utf-8")
    txn1.commit()
    zpath = step01_previous_zip_for(tmp_path)
    zbytes_v1 = zpath.read_bytes()

    # 第二次发布中途失败并回滚：新增的 chapter_outline.cache.json 应删除，旧 step01_previous.zip 应完好保留
    txn2 = PublishTransaction(tmp_path, label="v3-fail")
    txn2.begin()
    (tmp_path / "script_result.json").write_text(json.dumps({"title": "v3", "shots": [2]}), encoding="utf-8")
    (tmp_path / "chapter_outline.cache.json").write_text("{}", encoding="utf-8")
    txn2.rollback("injected failure")

    assert json.loads((tmp_path / "script_result.json").read_text(encoding="utf-8"))["title"] == "v2"
    assert not (tmp_path / "chapter_outline.cache.json").exists()
    assert zpath.read_bytes() == zbytes_v1


def test_corrupted_previous_zip_verify_fails_commit_and_rolls_back(tmp_path, monkeypatch):
    from shared.paths import step01_previous_zip_for

    _seed_full_six(tmp_path, "rev-v1")
    txn1 = PublishTransaction(tmp_path, label="v2")
    txn1.begin()
    (tmp_path / "script_result.json").write_text('{"title":"v2","shots":[1]}', encoding="utf-8")
    txn1.commit()
    v1_zip_bytes = step01_previous_zip_for(tmp_path).read_bytes()

    txn2 = PublishTransaction(tmp_path, label="v3-corrupt-zip")
    txn2.begin()
    (tmp_path / "script_result.json").write_text('{"title":"v3","shots":[2]}', encoding="utf-8")
    monkeypatch.setattr(pt, "verify_previous_backup",
                        lambda zp: (_ for _ in ()).throw(PublishError("corrupted zip")))
    with pytest.raises(PublishError):
        txn2.commit()
    assert json.loads((tmp_path / "script_result.json").read_text(encoding="utf-8"))["title"] == "v2"
    assert step01_previous_zip_for(tmp_path).read_bytes() == v1_zip_bytes
    journal = json.loads((tmp_path / "step01_publish_journal.json").read_text(encoding="utf-8"))
    assert journal["state"] == "rolled_back"


def test_journal_write_failure_after_overwriting_previous_zip_restores_old_zip(tmp_path, monkeypatch):
    from shared.paths import step01_previous_zip_for

    _seed_full_six(tmp_path, "rev-v1")
    txn1 = PublishTransaction(tmp_path, label="v2")
    txn1.begin()
    (tmp_path / "script_result.json").write_text('{"title":"v2","shots":[1]}', encoding="utf-8")
    txn1.commit()
    v1_zip_bytes = step01_previous_zip_for(tmp_path).read_bytes()

    txn2 = PublishTransaction(tmp_path, label="v3-journal-fail")
    txn2.begin()
    (tmp_path / "script_result.json").write_text('{"title":"v3","shots":[2]}', encoding="utf-8")
    real_journal = txn2._journal

    def fail_on_committed(state, detail=""):
        if state == "committed":
            raise PublishError("disk full on commit journal")
        return real_journal(state, detail)

    monkeypatch.setattr(txn2, "_journal", fail_on_committed)
    with pytest.raises(PublishError):
        txn2.commit()
    assert json.loads((tmp_path / "script_result.json").read_text(encoding="utf-8"))["title"] == "v2"
    assert step01_previous_zip_for(tmp_path).read_bytes() == v1_zip_bytes
    journal = json.loads((tmp_path / "step01_publish_journal.json").read_text(encoding="utf-8"))
    assert journal["state"] == "rolled_back"


def test_recover_pending_restores_old_previous_zip_after_commit_crash(tmp_path):
    from shared.paths import step01_previous_zip_for

    _seed_full_six(tmp_path, "rev-v1")
    txn1 = PublishTransaction(tmp_path, label="v2")
    txn1.begin()
    (tmp_path / "script_result.json").write_text('{"title":"v2","shots":[1]}', encoding="utf-8")
    txn1.commit()
    v1_zip_bytes = step01_previous_zip_for(tmp_path).read_bytes()

    txn2 = PublishTransaction(tmp_path, label="v3-crash-after-zip")
    txn2.begin()
    (tmp_path / "script_result.json").write_text('{"title":"v3","shots":[2]}', encoding="utf-8")
    step01_previous_zip_for(tmp_path).write_bytes(b"overwritten-v2-zip-before-commit")
    (tmp_path / ".publish.lock").write_text("999999 dead", encoding="utf-8")

    out = recover_pending(tmp_path)
    assert out["ok"] is True and out["state"] == "recovered"
    assert json.loads((tmp_path / "script_result.json").read_text(encoding="utf-8"))["title"] == "v2"
    assert step01_previous_zip_for(tmp_path).read_bytes() == v1_zip_bytes

