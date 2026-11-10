# -*- coding: utf-8 -*-
"""R4-2：并发预提取不得因为单个 worker 卡住而永久等待."""
from __future__ import annotations

import threading
import time

from step01_script_gen.concurrent_extract import extract_all_concurrent


def test_outline_worker_timeout_returns_structured_failure(monkeypatch):
    release = threading.Event()

    def _stuck(*a, **k):
        release.wait(10)
        return {"never": True}

    monkeypatch.setattr("step01_script_gen.outline.generate_outline_result", _stuck)
    started = time.monotonic()
    try:
        out = extract_all_concurrent(
            "原文", None, None,
            enable_characters=False, enable_scene_objects=False,
            enable_outline=True, wait_timeout_sec=0.1)
    finally:
        release.set()
    elapsed = time.monotonic() - started
    assert elapsed < 3.0, "worker 卡住时入口必须自己返回，不能等满 10s"
    assert isinstance(out.get("outline"), dict)
    assert out["outline"]["failure"]["code"] == "worker_timeout"
    assert out["outline"]["executable"] is False
