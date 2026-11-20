# -*- coding: utf-8 -*-
"""第 1 包 M04-a：`_maybe_qc_frame` 必须真的调用图片 QC（零网络）.

8850de0（2026-11-10）改 import 时漏掉了 `maybe_qc_sync`，之后每次 QC 都是
NameError → 外层吞掉 → 记「质检异常（不阻断）」→ 返回 False，坏帧不再被拦下。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _patch(monkeypatch, result):
    import shared.image_qc as iq
    import shared.vimax_bridge as vb
    from shared import post_budget
    calls, logs = [], []
    monkeypatch.setattr(iq, "maybe_qc_sync", lambda *a, **k: calls.append(a) or result)
    monkeypatch.setattr(iq, "frame_identity", lambda p: {"sha256": "x"})
    monkeypatch.setattr(iq, "write_sidecar", lambda *a, **k: None)
    monkeypatch.setattr(iq, "qc_state", lambda p: {"status": "ok"})
    monkeypatch.setattr(post_budget, "record_qc_rejected", lambda kind: None)
    monkeypatch.setattr(vb, "_bridge_log", lambda msg: logs.append(str(msg)))
    return vb, calls, logs


def test_critical_result_is_reported(tmp_path, monkeypatch):
    issue = SimpleNamespace(category="person_count", severity="critical", description="多一人")
    result = SimpleNamespace(error="", ok=True, has_hallucination=True, issues=[issue])
    vb, calls, logs = _patch(monkeypatch, result)
    frame = tmp_path / "first_frame.png"
    frame.write_bytes(b"png")
    got = asyncio.run(vb._maybe_qc_frame({"index": 1}, str(frame), "first_frame", prompt="p"))
    assert got is True
    assert len(calls) == 1
    assert not any("质检异常" in x for x in logs), logs


def test_clean_result_passes(tmp_path, monkeypatch):
    result = SimpleNamespace(error="", ok=True, has_hallucination=False, issues=[])
    vb, calls, logs = _patch(monkeypatch, result)
    frame = tmp_path / "last_frame.png"
    frame.write_bytes(b"png")
    got = asyncio.run(vb._maybe_qc_frame({"index": 2}, str(frame), "last_frame", prompt="p"))
    assert got is False
    assert len(calls) == 1
    assert any("质检通过" in x for x in logs), logs
