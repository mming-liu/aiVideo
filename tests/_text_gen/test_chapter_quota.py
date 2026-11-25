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
