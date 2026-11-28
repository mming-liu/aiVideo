# -*- coding: utf-8 -*-
"""step01 chapter single-flight lock tests."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _setup(monkeypatch, tmp_path):
    import step01_script_gen.main as m
    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    return m


def _write_lock(path: Path, pid: int) -> None:
    path.write_text(
        json.dumps({"pid": pid, "started_at": "2026-09-15 00:00:00"}),
        encoding="utf-8",
    )


def test_acquire_release_roundtrip(monkeypatch, tmp_path):
    m = _setup(monkeypatch, tmp_path)
    lock_path = tmp_path / ".step01.lock"

    assert m._acquire_step01_lock() is True
    assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid()

    m._release_step01_lock()
    assert not lock_path.exists()


def test_acquire_skips_live_lock(monkeypatch, tmp_path):
    m = _setup(monkeypatch, tmp_path)
    lock_path = tmp_path / ".step01.lock"
    _write_lock(lock_path, 424242)
    monkeypatch.setattr(m, "_pid_is_alive", lambda pid: True)

    assert m._acquire_step01_lock() is False
    assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == 424242


def test_acquire_replaces_stale_lock(monkeypatch, tmp_path):
    m = _setup(monkeypatch, tmp_path)
    lock_path = tmp_path / ".step01.lock"
    _write_lock(lock_path, 424242)
    monkeypatch.setattr(m, "_pid_is_alive", lambda pid: False)

    assert m._acquire_step01_lock() is True
    assert json.loads(lock_path.read_text(encoding="utf-8"))["pid"] == os.getpid()
    m._release_step01_lock()


def test_release_does_not_delete_other_process_lock(monkeypatch, tmp_path):
    m = _setup(monkeypatch, tmp_path)
    lock_path = tmp_path / ".step01.lock"
    _write_lock(lock_path, 424242)

    m._release_step01_lock()
    assert lock_path.exists()


def test_pid_is_alive_current_process():
    import step01_script_gen.main as m
    assert m._pid_is_alive(os.getpid()) is True
    assert m._pid_is_alive(-1) is False


@pytest.mark.skipif(os.name == "nt", reason="Windows uses OpenProcess instead of os.kill")
def test_pid_is_alive_permission_error_means_alive(monkeypatch):
    import step01_script_gen.main as m

    def _denied(pid, sig):
        raise PermissionError

    monkeypatch.setattr(os, "kill", _denied)
    assert m._pid_is_alive(424242) is True
