# -*- coding: utf-8 -*-
"""M1：RunContext 只读守卫（2026-11-18）."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from shared.json_io import atomic_dump_json
from shared.log_utils import resolve_log_file
from shared.run_context import ReadOnlyViolation, RunContext


def test_read_only_redirects_step01_log_and_blocks_chapter_writes(tmp_path, monkeypatch):
    import shared.paths as paths
    monkeypatch.setattr(paths, "chapter_dir", lambda: tmp_path)
    ctx = RunContext(chapter=1, read_only=True)
    with ctx.activate():
        assert Path(resolve_log_file("step01.log")) == Path(os.devnull)
        assert Path(resolve_log_file("step02b.log")) == tmp_path / "step02b.log"
        with pytest.raises(ReadOnlyViolation):
            atomic_dump_json(tmp_path / "step01_diag.json", {"x": 1})
        with pytest.raises(ReadOnlyViolation):
            atomic_dump_json(tmp_path / "step01.log", {"x": 1})
        # 章外临时文件仍可写（探针自己的隔离根）
        atomic_dump_json(tmp_path.parent / "probe.json", {"x": 1})


def test_contexts_do_not_leak_between_chapters(tmp_path, monkeypatch):
    import shared.paths as paths
    ch1, ch2 = tmp_path / "c1", tmp_path / "c2"
    ch1.mkdir(); ch2.mkdir()
    monkeypatch.setattr(paths, "chapter_dir", lambda: ch1)
    with RunContext(chapter=1, read_only=True).activate():
        assert Path(resolve_log_file("step01.log")) == Path(os.devnull)
        monkeypatch.setattr(paths, "chapter_dir", lambda: ch2)
        with RunContext(chapter=2, read_only=False).activate():
            assert Path(resolve_log_file("step01.log")) == ch2 / "step01.log"
        assert Path(resolve_log_file("step01.log")) == Path(os.devnull)


def test_from_env_read_only(monkeypatch):
    monkeypatch.setenv("STEP01_READ_ONLY", "1")
    monkeypatch.setenv("NOVEL_CHAPTER", "3")
    ctx = RunContext.from_env()
    assert ctx.read_only is True and ctx.chapter == 3
