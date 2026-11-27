# -*- coding: utf-8 -*-
"""S06/M01：Step04 发布批次的事务、回滚、恢复与并发（0 模型调用）.

覆盖：正常发布、备份阶段失败、替换中途失败、恢复失败（发布未确认）、
下次启动恢复（幂等）、版本冲突不覆盖、同目标并发写入被拒绝。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from step04_merge import publication as pub


def _file(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _batch(tmp_path: Path):
    work = tmp_path / "work"
    targets = {
        "final": tmp_path / "deliver" / "final.mp4",
        "srt": tmp_path / "deliver" / "subtitles.srt",
        "quality": tmp_path / "deliver" / "step04_quality.json",
    }
    _file(targets["final"], "OLD-FINAL")
    _file(targets["srt"], "OLD-SRT")
    _file(targets["quality"], "OLD-QUALITY")
    staged = {}
    for role in ("final", "srt", "quality"):
        staged[role] = _file(work / ("staged_" + role), "NEW-" + role.upper())
    moves = [{"role": role, "staged": staged[role], "target": targets[role]}
             for role in ("final", "srt", "quality")]
    return work, targets, staged, moves


def test_publish_success_replaces_all_and_cleans_backups(tmp_path):
    work, targets, staged, moves = _batch(tmp_path)
    result = pub.publish_batch(moves, work_dir=work, journal_path=work / "journal.json",
                               batch_id="ok")
    assert result["status"] == pub.STATUS_PUBLISHED
    for role, target in targets.items():
        assert target.read_text(encoding="utf-8") == "NEW-" + role.upper()
        assert not staged[role].exists()
    assert not (work / "journal.json").exists()
    assert not list(work.glob("backup-*/*"))


def test_publish_failure_rolls_back_all_targets(tmp_path, monkeypatch):
    work, targets, staged, moves = _batch(tmp_path)
    monkeypatch.setenv(pub.ENV_FAULT, "quality")
    with pytest.raises(pub.PublicationError) as exc:
        pub.publish_batch(moves, work_dir=work,
                          journal_path=work / "journal.json", batch_id="fail")
    assert exc.value.code == pub._CODE_FAILED
    assert exc.value.status == pub.STATUS_ROLLED_BACK
    for role, target in targets.items():
        assert target.read_text(encoding="utf-8") == "OLD-" + role.upper()
    assert staged["final"].exists()            # 新内容退回暂存，可重试
    data = json.loads((work / "journal.json").read_text(encoding="utf-8"))
    assert data["status"] == pub.STATUS_ROLLED_BACK


def test_publish_failure_with_rollback_error_is_unconfirmed(tmp_path, monkeypatch):
    work, targets, staged, moves = _batch(tmp_path)
    monkeypatch.setenv(pub.ENV_FAULT, "quality,rollback")
    with pytest.raises(pub.PublicationError) as exc:
        pub.publish_batch(moves, work_dir=work,
                          journal_path=work / "journal.json", batch_id="bad")
    assert exc.value.code == pub._CODE_UNCONFIRMED
    assert exc.value.exit_code == 3
    assert (work / "journal.json").is_file()    # 保留恢复材料
    assert list(work.glob("backup-*/*"))         # 备份不被清理


def test_recover_pending_restores_and_is_idempotent(tmp_path, monkeypatch):
    work, targets, staged, moves = _batch(tmp_path)
    monkeypatch.setenv(pub.ENV_FAULT, "quality,rollback")
    with pytest.raises(pub.PublicationError):
        pub.publish_batch(moves, work_dir=work,
                          journal_path=work / "journal.json", batch_id="recover")
    monkeypatch.delenv(pub.ENV_FAULT)
    first = pub.recover_pending(work / "journal.json")
    assert first["action"] == "restored"
    for role, target in targets.items():
        assert target.read_text(encoding="utf-8") == "OLD-" + role.upper()
    assert pub.recover_pending(work / "journal.json") is None   # 幂等：无残留


def test_recover_conflict_does_not_overwrite(tmp_path):
    work = tmp_path / "work"
    target = _file(tmp_path / "deliver" / "final.mp4", "THIRD-PARTY")
    staged = _file(work / "staged_final", "NEW")
    backup = _file(work / "backup-1" / "final.mp4", "OLD")
    journal = {
        "schema": pub.SCHEMA, "status": pub.STATUS_ROLLBACK_FAILED, "entries": [{
            "role": "final", "staged": str(staged), "target": str(target),
            "backup": str(backup), "staged_sha": "x", "target_sha_before": "y",
        }]}
    (work / "journal.json").write_text(json.dumps(journal), encoding="utf-8")
    with pytest.raises(pub.PublicationError) as exc:
        pub.recover_pending(work / "journal.json")
    assert exc.value.code == pub._CODE_CONFLICT
    assert target.read_text(encoding="utf-8") == "THIRD-PARTY"


def test_recover_does_not_delete_when_old_and_new_bytes_identical(tmp_path):
    """重渲染字节相同（旧值==新值）时，恢复不得把正常文件当"新文件"删除."""
    work = tmp_path / "work"
    target = _file(tmp_path / "deliver" / "final.mp4", "SAME")
    staged = _file(work / "staged_final", "SAME")
    sha = "x"
    import hashlib
    sha = hashlib.sha256(b"SAME").hexdigest()
    journal = {
        "schema": pub.SCHEMA, "status": pub.STATUS_ROLLED_BACK, "entries": [{
            "role": "final", "staged": str(staged), "target": str(target),
            "backup": "", "staged_sha": sha, "target_sha_before": sha,
        }]}
    (work / "journal.json").write_text(json.dumps(journal), encoding="utf-8")
    result = pub.recover_pending(work / "journal.json")
    assert result["action"] == "restored"
    assert target.read_text(encoding="utf-8") == "SAME"


def test_publish_busy_when_same_target_locked(tmp_path):
    from shared.enhancement_io import AttemptLock

    work, targets, staged, moves = _batch(tmp_path)
    lock = AttemptLock(work / "publish" / ".publish.lock", purpose="test").acquire()
    try:
        with pytest.raises(pub.PublicationError) as exc:
            pub.publish_batch(moves, work_dir=work / "publish",
                              journal_path=work / "journal.json", batch_id="busy")
        assert exc.value.code == pub._CODE_BUSY
        assert exc.value.exit_code == 5
        assert targets["final"].read_text(encoding="utf-8") == "OLD-FINAL"
    finally:
        lock.release()
