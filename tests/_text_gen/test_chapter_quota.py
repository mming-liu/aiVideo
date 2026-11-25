# -*- coding: utf-8 -*-
"""Chapter quota reservation: cap refusal, settlement, lost-batch conservatism."""
from __future__ import annotations

import json

import pytest

from _text_gen import chapter_quota as q


def _ledger(tmp_path, used=0, cap=20):
    p = tmp_path / "chapter.json"
    p.write_text(json.dumps({"max": cap, "used": used, "attempts": []}), encoding="utf-8")
    return p


def test_reservation_refuses_before_send_at_cap(tmp_path):
    p = _ledger(tmp_path, used=19)
    with pytest.raises(q.QuotaError):
        q.reserve(p, 4)
    assert json.loads(p.read_text(encoding="utf-8"))["used"] == 19
    assert q.pending(p) == []


def test_readable_batch_refunds_unspent(tmp_path):
    p = _ledger(tmp_path, used=16)
    rid = q.reserve(p, 4)
    assert json.loads(p.read_text(encoding="utf-8"))["used"] == 20
    q.settle(p, rid, 1, [{"n": 1}])
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["used"] == 17 and data["reservations"] == []
    assert data["attempts"] == [{"n": 1}]


def test_lost_batch_never_refunds(tmp_path):
    p = _ledger(tmp_path, used=16)
    rid = q.reserve(p, 4)
    q.settle_unknown(p, rid, reason="batch file missing")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["used"] == 20                      # 保守：不退款
    assert len(data["reservations"]) == 1
    assert data["reservations"][0]["state"] == "unknown"
    # 未知预占仍占额度，后续 batch 不能借它通过
    with pytest.raises(q.QuotaError):
        q.reserve(p, 1)


def test_duplicate_settlement_is_rejected(tmp_path):
    p = _ledger(tmp_path, used=16)
    rid = q.reserve(p, 4)
    q.settle(p, rid, 1)
    with pytest.raises(q.QuotaError):
        q.settle(p, rid, 1)


def test_malformed_batch_is_unknown_not_zero(tmp_path):
    for bad in ({}, {"used": -1}, {"used": "1"}, {"used": 1, "attempts": 1}):
        p = _ledger(tmp_path, used=16)
        rid = q.reserve(p, 4)
        batch = tmp_path / "batch.json"
        batch.write_text(json.dumps(bad), encoding="utf-8")
        out = q.settle_batch(p, rid, batch)
        data = json.loads(p.read_text(encoding="utf-8"))
        assert out["status"] == "unknown", (bad, out)
        assert data["used"] == 20, (bad, data)
        assert data["reservations"][0]["state"] == "unknown"


def test_valid_batch_settles_and_refunds(tmp_path):
    p = _ledger(tmp_path, used=16)
    rid = q.reserve(p, 4)
    batch = tmp_path / "batch.json"
    batch.write_text(json.dumps({"used": 1, "attempts": [{"n": 1}]}), encoding="utf-8")
    out = q.settle_batch(p, rid, batch)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert out["status"] == "settled" and data["used"] == 17


def test_service_blocks_send_under_read_only(monkeypatch):
    from _text_gen.models import TextGenerationRequest, TextGenerationResult
    from _text_gen.service import TextGenerationService, _LedgeredService
    from shared.run_context import ReadOnlyViolation

    called = []

    class _Dummy(TextGenerationService):
        def generate(self, req):
            called.append(req)
            return TextGenerationResult(success=True, text="ok")

    svc = _LedgeredService(_Dummy(), "agent_plan")
    monkeypatch.setenv("STEP01_READ_ONLY", "1")
    with pytest.raises(ReadOnlyViolation):
        svc.generate(TextGenerationRequest(system_prompt="s", user_prompt="u"))
    assert called == []


def test_service_blocks_send_when_chapter_quota_exhausted(tmp_path, monkeypatch):
    from _text_gen.models import TextGenerationRequest, TextGenerationResult
    from _text_gen.service import TextGenerationService, _LedgeredService

    called = []

    class _Dummy(TextGenerationService):
        def generate(self, req):
            called.append(req)
            return TextGenerationResult(success=True, text="ok")

    svc = _LedgeredService(_Dummy(), "agent_plan")
    p = _ledger(tmp_path, used=20, cap=20)
    monkeypatch.setenv(q.ENV_FILE, str(p))
    with pytest.raises(q.QuotaError):
        svc.generate(TextGenerationRequest(system_prompt="s", user_prompt="u"))
    assert called == []

    p2 = _ledger(tmp_path, used=19, cap=20)
    monkeypatch.setenv(q.ENV_FILE, str(p2))
    res = svc.generate(TextGenerationRequest(system_prompt="s", user_prompt="u"))
    assert res.success is True and len(called) == 1


def test_send_quota_per_post_debits_chapter_without_double_charge(tmp_path, monkeypatch):
    from _text_gen import send_quota as sq

    ch = _ledger(tmp_path, used=18, cap=20)
    monkeypatch.setenv(q.ENV_FILE, str(ch))
    monkeypatch.delenv(sq.ENV_FILE, raising=False)

    # 1. 直接发送（无批次包装器）：每次 POST 原子扣减 1 次（含重试），第 3 次阻断
    assert sq.reserve(provider="agent_plan", endpoint="https://example.test") == 19
    assert sq.reserve(provider="agent_plan", endpoint="https://example.test") == 20
    assert json.loads(ch.read_text(encoding="utf-8"))["used"] == 20
    with pytest.raises(q.QuotaError):
        sq.reserve(provider="agent_plan", endpoint="https://example.test")

    # 2. 批次包装器走 reserve_batch：只预占一次，POST 扣批次不重复扣章节
    ch2 = _ledger(tmp_path, used=15, cap=20)
    ch2_path = tmp_path / "ch2.json"
    ch2.replace(ch2_path)
    batch = tmp_path / "batch.json"
    rid = q.reserve_batch(ch2_path, batch, 3, tag="b1")
    assert json.loads(ch2_path.read_text(encoding="utf-8"))["used"] == 18
    monkeypatch.setenv(q.ENV_FILE, str(ch2_path))
    monkeypatch.setenv(sq.ENV_FILE, str(batch))
    assert sq.reserve(provider="agent_plan", endpoint="https://example.test") == 1
    assert json.loads(ch2_path.read_text(encoding="utf-8"))["used"] == 18
    assert json.loads(batch.read_text(encoding="utf-8"))["used"] == 1
    st = q.settle_batch(ch2_path, rid, batch)
    assert st["status"] == "settled" and st["spent"] == 1
    assert json.loads(ch2_path.read_text(encoding="utf-8"))["used"] == 16

    # 3. 无效/伪造批次文件（缺章节预占 reservation_id）在同时设 STEP01_CHAPTER_QUOTA_FILE 时拒发
    fake_batch = tmp_path / "fake_batch.json"
    fake_batch.write_text(json.dumps({"max": 5, "used": 0, "attempts": []}), encoding="utf-8")
    monkeypatch.setenv(sq.ENV_FILE, str(fake_batch))
    with pytest.raises(sq.SendQuotaUnavailable):
        sq.reserve(provider="agent_plan", endpoint="https://example.test")
