# -*- coding: utf-8 -*-
"""S37：plan-only 日志上下文必须覆盖旧值并在异常后恢复."""
from __future__ import annotations

import sys

import pytest

from shared import log_utils


def test_isolated_log_context_overrides_existing_and_restores(tmp_path):
    original = tmp_path / "formal"
    log_utils.set_log_context(original)
    try:
        with log_utils.isolated_log_context() as isolated:
            assert log_utils.current_log_dir() == isolated
            assert isolated != original
        assert log_utils.current_log_dir() == original
    finally:
        log_utils.set_log_context(None)


def test_isolated_log_context_restores_on_exception(tmp_path):
    original = tmp_path / "formal"
    log_utils.set_log_context(original)
    try:
        with pytest.raises(RuntimeError):
            with log_utils.isolated_log_context():
                raise RuntimeError("boom")
        assert log_utils.current_log_dir() == original
    finally:
        log_utils.set_log_context(None)


def test_plan_only_decorator_isolates_even_with_previous_context(monkeypatch, tmp_path):
    from step02a_frame_gen import main as step02a_main

    original = tmp_path / "formal"
    log_utils.set_log_context(original)
    monkeypatch.setattr(sys, "argv", ["step02a", "--plan-only"])
    seen = {}

    @step02a_main._plan_only_log_isolated
    def _probe():
        import os
        seen["inside"] = log_utils.current_log_dir()
        seen["step01"] = os.environ.get("STEP01_LOG_PATH")
        return "ok"

    try:
        assert _probe() == "ok"
        assert seen["inside"] != original
        assert seen["step01"] and seen["step01"].endswith("step01.log")
        assert log_utils.current_log_dir() == original
    finally:
        log_utils.set_log_context(None)
