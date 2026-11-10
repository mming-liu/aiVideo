# -*- coding: utf-8 -*-
"""U4 验收：QC 身份/生命周期 + 请求事件账本，从**实际消费入口**断言.

覆盖：旧图 PASS 后换图、相同 mtime 不同内容、QC 期间换图、调用失败、写入
失败、重启恢复、并发扣减，以及 Step02b 的 _check_keyframe_hallucination 消费。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest
from PIL import Image

from shared import image_qc as q
from shared import post_budget
from shared import request_events


def _png(path: Path, color=(1, 2, 3)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (12, 12), color).save(path)
    return path


def _ok(has=False, issues=()):
    return q.QcResult(has_hallucination=has, issues=list(issues), ok=True)


def _issue(cat="bad", sev="critical"):
    return q.QcIssue(category=cat, description="x", severity=sev)


@pytest.fixture()
def events_file(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("POST_EVENTS_FILE", str(path))
    return path


def test_legacy_sidecar_is_unbound_and_gets_rechecked(tmp_path):
    frame = _png(tmp_path / "first_frame.png")
    frame.with_name("first_frame_hallucination.json").write_text(
        json.dumps({"has_hallucination": False, "issues": []}), encoding="utf-8")
    state = q.qc_state(frame)
    assert state["status"] == "qc_stale" and state["reason"] == "legacy_unbound"
    assert q.should_run_qc(frame) is True


def test_new_image_after_pass_is_stale(tmp_path):
    frame = _png(tmp_path / "first_frame.png")
    q.write_sidecar(frame, _ok())
    assert q.qc_state(frame)["status"] == "qc_passed"
    _png(frame, (200, 100, 50))
    state = q.qc_state(frame)
    assert state["status"] == "qc_stale" and state["reason"] == "image_changed"


def test_same_mtime_different_content_is_stale(tmp_path):
    frame = _png(tmp_path / "first_frame.png")
    q.write_sidecar(frame, _ok())
    st = frame.stat()
    _png(frame, (7, 7, 7))
    os.utime(frame, (st.st_atime, st.st_mtime))
    assert q.qc_state(frame)["status"] == "qc_stale"


def test_image_replaced_during_qc_is_recorded_stale(tmp_path):
    frame = _png(tmp_path / "first_frame.png")
    pre = q.frame_identity(frame)["sha256"]
    _png(frame, (9, 8, 7))          # QC 期间被替换
    q.write_sidecar(frame, _ok(), pre_digest=pre)
    state = q.qc_state(frame)
    assert state["status"] == "qc_stale"
    assert state["data"]["status_reason"] == "image_replaced_during_qc"


def test_call_failure_is_recorded_and_retried(tmp_path, events_file):
    frame = _png(tmp_path / "first_frame.png")
    q.write_sidecar(frame, _ok())
    failed = q.QcResult(has_hallucination=False, issues=[], ok=False,
                        error="SSLError: certificate verify failed")
    q.write_sidecar(frame, failed)
    state = q.qc_state(frame)
    assert state["status"] == "qc_call_failed"
    assert "SSLError" in state["data"]["error"]
    assert q.should_run_qc(frame) is True
    # 旧结果的身份保留在历史里（不是被静默覆盖成"通过"）
    assert state["data"]["history"]
    assert any(h.get("status") == "qc_passed" for h in state["data"]["history"])
    events = [e["event"] for e in request_events.read_events(events_file)]
    assert "qc_call_failed" in events


def test_sidecar_write_failure_keeps_previous_file(tmp_path, monkeypatch):
    frame = _png(tmp_path / "first_frame.png")
    q.write_sidecar(frame, _ok())
    sidecar = q.sidecar_path(frame)
    before = sidecar.read_bytes()

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        q.write_sidecar(frame, _ok(has=True, issues=[_issue()]))
    assert sidecar.read_bytes() == before


def test_state_survives_restart(tmp_path):
    frame = _png(tmp_path / "first_frame.png")
    q.write_sidecar(frame, _ok())
    # 模拟重启：丢弃一切内存状态，仅从磁盘读
    state = q.qc_state(frame)
    assert state["status"] == "qc_passed"
    assert state["data"]["image"]["sha256"] == q.frame_identity(frame)["sha256"]


def test_video_consumer_rejects_unverified_and_accepts_bound_critical(tmp_path):
    from step02_video_gen.submit_poll import _check_keyframe_hallucination
    frame = _png(tmp_path / "first_frame.png")
    # 旧 sidecar：未绑定 → 不冒充通过，也不冒充 critical
    frame.with_name("first_frame_hallucination.json").write_text(
        json.dumps({"has_hallucination": True,
                    "issues": [{"category": "old", "description": "x",
                                "severity": "critical"}]}), encoding="utf-8")
    res = _check_keyframe_hallucination(str(frame))
    assert res["has_critical"] is False and res["verified"] is False

    q.write_sidecar(frame, _ok(has=True, issues=[_issue("extra_person")]))
    res = _check_keyframe_hallucination(str(frame))
    assert res["has_critical"] is True and res["verified"] is True

    _png(frame, (5, 5, 5))          # 换图后旧结论不再适用
    res = _check_keyframe_hallucination(str(frame))
    assert res["has_critical"] is False and res["verified"] is False


def test_budget_acquire_is_concurrent_and_emits_events(tmp_path, monkeypatch, events_file):
    ledger = tmp_path / "budget.json"
    monkeypatch.setenv("STEP02A_IMAGE_BUDGET_TOTAL", "5")
    monkeypatch.setenv("STEP02A_IMAGE_BUDGET_FILE", str(ledger))
    post_budget.reset("image")
    try:
        results = []

        def worker():
            try:
                results.append(post_budget.acquire("image"))
            except Exception as exc:
                results.append(str(exc))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        data = json.loads(ledger.read_text(encoding="utf-8"))
        assert data["used"] == 5
        assert data["pending"] == 5
        assert sum(isinstance(r, int) for r in results) == 5
        sent = [e for e in request_events.read_events(events_file)
                if e["event"] == "request_sent"]
        assert len(sent) == 5
        assert all(e["used"] and e["limit"] == 5 for e in sent)
    finally:
        post_budget.reset("image")


def test_outcome_events_correlate_by_request_id(tmp_path, monkeypatch, events_file):
    ledger = tmp_path / "budget.json"
    monkeypatch.setenv("STEP02A_IMAGE_BUDGET_TOTAL", "2")
    monkeypatch.setenv("STEP02A_IMAGE_BUDGET_FILE", str(ledger))
    post_budget.reset("image")
    try:
        rid = request_events.begin_request("image")
        post_budget.acquire("image", request_id=rid)
        post_budget.record("image", "generated_ok", request_id=rid)
        chain = request_events.events_for(rid, events_file)
        assert [e["event"] for e in chain] == ["request_sent", "response_received"]
        # 成功出图后被 QC 拒收是第二个阶段事件，预算只扣一次
        request_events.emit("qc_rejected", request_id=rid, kind="image_qc")
        data = json.loads(ledger.read_text(encoding="utf-8"))
        assert data["used"] == 1
        assert data["outcomes"]["generated_ok"] == 1
    finally:
        post_budget.reset("image")


def test_unknown_request_result_stays_unknown(tmp_path, monkeypatch, events_file):
    ledger = tmp_path / "budget.json"
    monkeypatch.setenv("STEP02A_IMAGE_BUDGET_TOTAL", "2")
    monkeypatch.setenv("STEP02A_IMAGE_BUDGET_FILE", str(ledger))
    post_budget.reset("image")
    try:
        rid = request_events.begin_request("image")
        post_budget.acquire("image", request_id=rid)
        data = json.loads(ledger.read_text(encoding="utf-8"))
        assert data["used"] == 1 and data["pending"] == 1
        assert data["outcomes"] == {"generated_ok": 0, "account_quota_refused": 0,
                                    "transport_failed": 0, "qc_rejected": 0}
        names = [e["event"] for e in request_events.events_for(rid, events_file)]
        assert names == ["request_sent"]
    finally:
        post_budget.reset("image")
