# -*- coding: utf-8 -*-
"""M0.3 图片请求账本四分类：向后兼容、迁移、互斥计数与 fail-closed."""
from __future__ import annotations

import json

import pytest

from shared import post_budget


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("STEP02A_IMAGE_BUDGET_FILE", raising=False)
    monkeypatch.delenv("STEP02A_IMAGE_BUDGET_TOTAL", raising=False)
    post_budget.reset()
    yield
    post_budget.reset()


def _enable(monkeypatch, tmp_path, total=10):
    ledger = tmp_path / "image_budget.json"
    monkeypatch.setenv("STEP02A_IMAGE_BUDGET_FILE", str(ledger))
    monkeypatch.setenv("STEP02A_IMAGE_BUDGET_TOTAL", str(total))
    return ledger


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_legacy_ledger_reads_without_rewriting(monkeypatch, tmp_path):
    ledger = _enable(monkeypatch, tmp_path, total=172)
    _write(ledger, {"kind": "image", "used": 31, "limit": 172,
                    "updated_at": "2026-11-09 08:05:18"})
    before = ledger.read_bytes()

    data = post_budget.usage("image")

    assert data["used"] == 31 and data["limit"] == 172
    assert data["schema_version"] == 1
    assert data["outcomes"] == {"generated_ok": 31, "account_quota_refused": 0,
                                "transport_failed": 0, "qc_rejected": 0}
    assert data["pending"] == 0
    assert ledger.read_bytes() == before, "只读核对不得改写旧账本"


def test_explicit_migrate_preserves_used_and_limit(monkeypatch, tmp_path):
    ledger = _enable(monkeypatch, tmp_path, total=172)
    _write(ledger, {"kind": "image", "used": 31, "limit": 172,
                    "updated_at": "2026-11-09 08:05:18"})

    migrated = post_budget.migrate("image")

    on_disk = json.loads(ledger.read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == 2
    assert on_disk["used"] == 31 and on_disk["limit"] == 172
    assert on_disk["outcomes"]["generated_ok"] == 31
    assert on_disk["pending"] == 0
    assert migrated["used"] == 31
    # 幂等：再迁移一次不改变计数
    again = post_budget.migrate("image")
    assert again["used"] == 31 and again["outcomes"]["generated_ok"] == 31


def test_acquire_then_record_settles_pending(monkeypatch, tmp_path):
    ledger = _enable(monkeypatch, tmp_path, total=5)

    assert post_budget.acquire("image") == 1
    mid = post_budget.usage("image")
    assert mid["used"] == 1 and mid["pending"] == 1
    assert sum(mid["outcomes"].values()) + mid["pending"] == mid["used"]

    post_budget.record("image", "generated_ok")
    final = post_budget.usage("image")
    assert final["used"] == 1 and final["pending"] == 0
    assert final["outcomes"]["generated_ok"] == 1
    assert json.loads(ledger.read_text(encoding="utf-8"))["schema_version"] == 2


def test_all_four_outcomes_are_recorded_independently(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path, total=10)
    for _ in range(4):
        post_budget.acquire("image")
    post_budget.record("image", "generated_ok")
    post_budget.record("image", "account_quota_refused")
    post_budget.record("image", "transport_failed")
    post_budget.record("image", "qc_rejected")

    data = post_budget.usage("image")
    assert data["outcomes"] == {"generated_ok": 1, "account_quota_refused": 1,
                                "transport_failed": 1, "qc_rejected": 1}
    assert data["used"] == 4 and data["pending"] == 0


def test_qc_rejection_reclassifies_generated_ok(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path, total=5)
    post_budget.acquire("image")
    post_budget.record("image", "generated_ok")

    assert post_budget.record_qc_rejected("image") is True
    data = post_budget.usage("image")
    assert data["outcomes"]["generated_ok"] == 0
    assert data["outcomes"]["qc_rejected"] == 1
    assert data["used"] == 1

    # 没有可改判的 generated_ok：不制造负计数，也不改账本。
    assert post_budget.record_qc_rejected("image") is False
    assert post_budget.usage("image")["outcomes"]["qc_rejected"] == 1


def test_inconsistent_or_unknown_outcomes_fail_closed(monkeypatch, tmp_path):
    ledger = _enable(monkeypatch, tmp_path, total=5)
    _write(ledger, {"kind": "image", "used": 2, "limit": 5, "schema_version": 2,
                    "pending": 0,
                    "outcomes": {"generated_ok": 1, "account_quota_refused": 0,
                                 "transport_failed": 0, "qc_rejected": 0}})
    with pytest.raises(post_budget.PostBudgetExhausted):
        post_budget.acquire("image")

    _write(ledger, {"kind": "image", "used": 1, "limit": 5, "schema_version": 2,
                    "pending": 0,
                    "outcomes": {"generated_ok": 1, "account_quota_refused": 0,
                                 "transport_failed": 0, "qc_rejected": 0,
                                 "unknown": 0}})
    with pytest.raises(post_budget.PostBudgetExhausted):
        post_budget.acquire("image")


def test_negative_outcome_fails_closed(monkeypatch, tmp_path):
    ledger = _enable(monkeypatch, tmp_path, total=5)
    _write(ledger, {"kind": "image", "used": 1, "limit": 5, "schema_version": 2,
                    "pending": 0,
                    "outcomes": {"generated_ok": 2, "account_quota_refused": 0,
                                 "transport_failed": 0, "qc_rejected": -1}})
    with pytest.raises(post_budget.PostBudgetExhausted):
        post_budget.acquire("image")


def test_unknown_outcome_name_is_rejected(monkeypatch, tmp_path):
    _enable(monkeypatch, tmp_path, total=5)
    post_budget.acquire("image")
    with pytest.raises(ValueError):
        post_budget.record("image", "not_a_category")
