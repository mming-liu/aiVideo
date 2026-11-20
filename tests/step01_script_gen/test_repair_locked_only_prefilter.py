# -*- coding: utf-8 -*-
"""第 1 包 M04-b：只指向锁定字段的审计项必须在修复轮之前被摘掉（零模型 / 零网络）.

1e368c6（2026-11-07）加入了预过滤，但调用的 `allowed_repair_fields` 未导入，
NameError 被吞掉 → 过滤从未生效 → 锁定字段问题照样派给模型。

驱动真实 `_repair_shot_warnings`，复用事务入口用例的打桩框架。
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent
for _p in (str(_ROOT), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from step01_script_gen.quality_issues import Issue  # noqa: E402
from test_kf_frame_identity_transaction import (_TxHarness, _issue,  # noqa: E402
                                                 _warnings_fn)


def _locked_issue(shot_id="5"):
    return Issue(code="narration_mismatch", severity="visual_risk", message="旁白与原文不符",
                 shot_id=str(shot_id), frame_id="", entity_id="",
                 field_path="narration_segment", evidence="旁白",
                 repair_targets=[{"shot_id": str(shot_id), "field_path": "narration_segment"}])


def _no_deterministic_blocks():
    return _warnings_fn(frame_threshold={"5": 0, "9": 0})


def test_locked_only_audit_issue_never_reaches_the_model(monkeypatch, tmp_path):
    h = _TxHarness(monkeypatch, answers=[{"replacements": []}], issues=[_locked_issue()],
                   warnings_fn=_no_deterministic_blocks(), tmp_path=tmp_path).run()
    assert h.calls["n"] == 0, h.logs
    assert any("跳过仅指向锁定字段的审计项" in x for x in h.logs), h.logs


def test_executable_audit_issue_still_reaches_the_model(monkeypatch, tmp_path):
    h = _TxHarness(monkeypatch, answers=[{"replacements": []}], issues=[_issue("5")],
                   warnings_fn=_no_deterministic_blocks(), tmp_path=tmp_path).run()
    assert h.calls["n"] >= 1, h.logs
    assert not any("跳过仅指向锁定字段的审计项" in x for x in h.logs), h.logs


def test_locked_only_issue_does_not_join_repair_targets(monkeypatch, tmp_path):
    """混合场景：锁定字段问题（镜 5）不得进入本轮待修目标，可修问题（镜 9）照常修."""
    h = _TxHarness(monkeypatch, answers=[{"replacements": []}],
                   issues=[_locked_issue("5"), _issue("9")],
                   warnings_fn=_no_deterministic_blocks(), tmp_path=tmp_path).run()
    picked = [x for x in h.logs if "镜待修" in x]
    assert picked, h.logs
    assert all("shot_9" in x and "shot_5" not in x for x in picked), picked
