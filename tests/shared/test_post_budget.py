# -*- coding: utf-8 -*-
"""POST 预算账本：跨进程持久化、并发原子、损坏 fail-closed、额度熔断."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from shared import post_budget


@pytest.fixture(autouse=True)
def _clean_budget(monkeypatch):
    monkeypatch.delenv("STEP02A_IMAGE_BUDGET_FILE", raising=False)
    monkeypatch.delenv("STEP02A_IMAGE_BUDGET_TOTAL", raising=False)
    monkeypatch.delenv("STEP02B_VIDEO_BUDGET_FILE", raising=False)
    monkeypatch.delenv("STEP02B_VIDEO_BUDGET_TOTAL", raising=False)
    post_budget.reset()
    yield
    post_budget.reset()


def _enable(monkeypatch, tmp_path, total=3, kind='image'):
    prefix = "STEP02A_IMAGE" if kind == "image" else "STEP02B_VIDEO"
    monkeypatch.setenv(prefix + "_BUDGET_FILE", str(tmp_path / "budget.json"))
    monkeypatch.setenv(prefix + "_BUDGET_TOTAL", str(total))
    return tmp_path / "budget.json"


def test_disabled_when_total_unset():
    assert post_budget.acquire("image") == 0
    assert post_budget.usage("image") == {}
    assert post_budget.quota_exhausted("video") == ""


def test_acquire_persists_and_exhausts(monkeypatch, tmp_path):
    ledger = _enable(monkeypatch, tmp_path, total=2)
    assert post_budget.acquire("image") == 1
    assert post_budget.acquire("image") == 2
    with pytest.raises(post_budget.PostBudgetExhausted):
        post_budget.acquire("image")
    data = json.loads(ledger.read_text(encoding="utf-8"))
    assert data["used"] == 2 and data["limit"] == 2


def test_exhaustion_survives_memory_reset(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path, total=1)
    post_budget.acquire("image")
    post_budget._QUOTA.clear()
    with pytest.raises(post_budget.PostBudgetExhausted):
        post_budget.acquire("image")


def test_corrupt_ledger_fails_closed(monkeypatch, tmp_path):
    ledger = _enable(monkeypatch, tmp_path, total=5)
    ledger.write_text("{not json", encoding="utf-8")
    with pytest.raises(post_budget.PostBudgetExhausted):
        post_budget.acquire("image")
    assert post_budget.quota_exhausted("image")


def test_quota_marker_persists_and_blocks(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path, total=5, kind='video')
    post_budget.mark_quota_exhausted("video", "403 SetLimitExceeded")
    post_budget._QUOTA.clear()
    assert "SetLimitExceeded" in post_budget.quota_exhausted("video")
    with pytest.raises(post_budget.PostBudgetExhausted):
        post_budget.acquire("video")


def test_concurrent_acquire_never_exceeds_limit(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path, total=5)
    ok = []
    bad = []
    barrier = threading.Barrier(20)

    def worker():
        barrier.wait()
        try:
            ok.append(post_budget.acquire("image"))
        except post_budget.PostBudgetExhausted:
            bad.append(1)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(ok) == 5 and len(bad) == 15
    assert post_budget.usage("image")["used"] == 5


def test_is_quota_error_markers():
    assert post_budget.is_quota_error('{"code":"SetLimitExceeded"}')
    assert post_budget.is_quota_error("QuotaExceeded: no balance")
    assert post_budget.is_quota_error("账户额度不足")
    assert not post_budget.is_quota_error("400 bad request")
