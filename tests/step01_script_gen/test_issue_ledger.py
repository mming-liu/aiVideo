# -*- coding: utf-8 -*-
"""M03：已确认问题状态台账（「未报出」不等于「已解决」）.

这里锁住的是**状态语义**，不是某个案例：
  · 本次没报出 → `not_detected_this_round`，**不关闭**；
  · 关闭必须有确定性复核证据或重新裁决；
  · 撤销必须给 reason + adjudicator；
  · 人工判为非缺陷的条目不计入确认缺陷统计；
  · 身份按（镜头, 字段）归并，code 漂移不改变身份。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from step01_script_gen.issue_ledger import (
    STATUS_FIXED,
    STATUS_NEEDS_REVERIFY,
    STATUS_NOT_DETECTED,
    STATUS_STILL_REPORTED,
    STATUS_WITHDRAWN,
    LedgerError,
    build_issue_ledger,
    issue_identity,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/step01_script_gen/fixtures/u6_confirmed_issues.json"
FINAL_AUDIT = (ROOT / "baselines/2026-11-12-pre-layered/s06_ab"
               / "phase2_20261112T072011/final_audit.json")


def _issue(**over):
    item = {"id": "i1", "code": "PROP_CONTINUITY", "shot_id": "7",
            "field_path": "lf_desc", "disposition": "confirmed",
            "evidence": "右手酒壶倾倒"}
    item.update(over)
    return item


def _observed(**over):
    item = {"code": "STATE_CHAIN_CONFLICT", "shot_id": "7",
            "field_path": "lf_desc", "severity": "advisory"}
    item.update(over)
    return item


def _rows(ledger):
    return {r["id"]: r for r in ledger["rows"]}


def test_not_detected_is_not_closed():
    ledger = build_issue_ledger([_issue()], [], candidate_revision="rev1")
    row = _rows(ledger)["i1"]
    assert row["status"] == STATUS_NOT_DETECTED
    assert row["closed"] is False
    assert "不等于已解决" in row["note"]
    assert ledger["summary"]["closed"] == 0


def test_still_reported_keeps_the_issue_open_even_if_code_drifted():
    """同一事实换了 code 仍是同一身份：不能记成「消失 + 新增」."""
    ledger = build_issue_ledger([_issue()], [_observed()], candidate_revision="rev1")
    row = _rows(ledger)["i1"]
    assert row["status"] == STATUS_STILL_REPORTED
    assert row["observed_codes"] == ["STATE_CHAIN_CONFLICT"]
    assert row["closed"] is False


def test_closing_requires_evidence():
    with pytest.raises(LedgerError):
        build_issue_ledger([_issue()], [], candidate_revision="rev1",
                           fixed_evidence={"i1": {"kind": "deterministic_recheck"}})
    # 有 kind + detail 才允许关闭
    ledger = build_issue_ledger(
        [_issue()], [], candidate_revision="rev1",
        fixed_evidence={"i1": {"kind": "deterministic_recheck",
                               "detail": "重复文本计数 2 → 1"}})
    row = _rows(ledger)["i1"]
    assert row["status"] == STATUS_FIXED and row["closed"] is True


def test_withdrawn_requires_reason_and_adjudicator():
    with pytest.raises(LedgerError):
        build_issue_ledger([_issue()], [], candidate_revision="rev1",
                           withdrawn={"i1": {"reason": "看错了"}})
    ledger = build_issue_ledger(
        [_issue()], [], candidate_revision="rev1",
        withdrawn={"i1": {"reason": "原文支持该写法", "adjudicator": "人工复核"}})
    row = _rows(ledger)["i1"]
    assert row["status"] == STATUS_WITHDRAWN and row["closed"] is True


def test_non_defect_dispositions_are_not_counted_as_confirmed():
    legal = _issue(id="i2", disposition="legal_expression")
    improvement = _issue(id="i3", disposition="improvement")
    undecided = _issue(id="i4", disposition="undecided")
    ledger = build_issue_ledger([_issue(), legal, improvement, undecided], [],
                                candidate_revision="rev1")
    s = ledger["summary"]
    assert s["total"] == 4 and s["confirmed"] == 1 and s["non_defect"] == 3
    assert _rows(ledger)["i2"]["closed"] is False


def test_no_observation_input_cannot_be_read_as_not_detected():
    ledger = build_issue_ledger([_issue()], None, candidate_revision="rev1")
    row = _rows(ledger)["i1"]
    assert row["status"] == STATUS_NEEDS_REVERIFY
    assert ledger["observed_provided"] is False


def test_identity_requires_shot_and_field():
    assert issue_identity({"shot_id": "7", "field_path": "lf_desc"}) == ("7", "lf_desc")
    with pytest.raises(LedgerError):
        issue_identity({"shot_id": "", "field_path": "lf_desc"})


def test_unknown_disposition_is_rejected():
    with pytest.raises(LedgerError):
        build_issue_ledger([_issue(disposition="whatever")], [],
                           candidate_revision="rev1")


@pytest.mark.skipif(not (FIXTURE.exists() and FINAL_AUDIT.exists()),
                    reason="需要 S01 裁决夹具与冻结终局审计")
def test_u6_fixture_reproduces_the_corrected_count():
    """同一份未变稿件：4 条确认缺陷仍被报出，3 条本次未检出，0 条可关闭."""
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    observed = json.loads(FINAL_AUDIT.read_text(encoding="utf-8"))
    ledger = build_issue_ledger(fixture["issues"], observed["issues"],
                                candidate_revision=observed["input_revision"])
    s = ledger["summary"]
    assert s["confirmed"] == 7
    assert s["still_reported"] == 4
    assert s["not_detected"] == 3
    assert s["closed"] == 0
    # 未检出的三条正是 #8/#9/#10
    missed = {r["id"] for r in ledger["rows"]
              if r["status"] == STATUS_NOT_DETECTED and r["disposition"] == "confirmed"}
    assert missed == {"u6-08", "u6-09", "u6-10"}
    # 合法省略与待裁决条目不得被算成「确认缺陷漏报」
    rows = _rows(ledger)
    assert rows["u6-01"]["disposition"] == "legal_expression"
    assert rows["u6-07"]["disposition"] == "undecided"
