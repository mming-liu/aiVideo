# -*- coding: utf-8 -*-
"""S44：旧审计兼容声明必须带可核验的确定性证据."""
from __future__ import annotations

from shared.quality_gate import rule_source_compat_ok

AUDIT = "a" * 16
QUALITY = "b" * 16


def _base():
    return {
        "from": AUDIT, "to": QUALITY,
        "deterministic_recheck": "identical",
        "verified_at": "2026-11-26T12:10:00+08:00",
        "evidence": {"status": "PASS", "blocking": 0, "issues": 0},
    }


def test_verified_exact_mapping_passes():
    assert rule_source_compat_ok(_base(), audit_fp=AUDIT, quality_fp=QUALITY)


def test_missing_verification_or_bad_evidence_is_rejected():
    forged = _base()
    forged.pop("verified_at")
    assert not rule_source_compat_ok(forged, audit_fp=AUDIT, quality_fp=QUALITY)
    assert not rule_source_compat_ok(
        _base() | {"evidence": {"status": "PASS", "blocking": 1, "issues": 0}},
        audit_fp=AUDIT, quality_fp=QUALITY)


def test_wrong_mapping_is_rejected():
    assert not rule_source_compat_ok(_base() | {"to": "c" * 16},
                                     audit_fp=AUDIT, quality_fp=QUALITY)
