# -*- coding: utf-8 -*-
"""提交门锁定字段降级：仅 narration/subtitle 目标的 blocking 不再死锁."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _audit(*issues):
    return {"issues": list(issues)}


def _issue(field_path, *, severity="blocking", repair_targets=None, code="X"):
    rec = {"code": code, "severity": severity, "shot_id": "11", "field_path": field_path}
    if repair_targets is not None:
        rec["repair_targets"] = repair_targets
    return rec


def test_locked_only_field_path_is_downgraded():
    from step01_script_gen.main import _downgrade_locked_only_audit_issues
    audit = _audit(_issue("narration_segment"))
    assert _downgrade_locked_only_audit_issues(audit, contracts_ok=True) == 1
    assert audit["issues"][0]["severity"] == "warning"
    assert audit["issues"][0].get("_downgraded_locked_only") is True


def test_locked_only_repair_targets_are_downgraded():
    from step01_script_gen.main import _downgrade_locked_only_audit_issues
    audit = _audit(_issue("", repair_targets=[{"shot_id": "11", "field_path": "subtitle"}]))
    assert _downgrade_locked_only_audit_issues(audit, contracts_ok=True) == 1
    assert audit["issues"][0]["severity"] == "warning"


def test_executable_visual_target_keeps_blocking():
    from step01_script_gen.main import _downgrade_locked_only_audit_issues
    audit = _audit(_issue("", repair_targets=[{"shot_id": "11", "field_path": "motion_desc"}]))
    assert _downgrade_locked_only_audit_issues(audit, contracts_ok=True) == 0
    assert audit["issues"][0]["severity"] == "blocking"


def test_mixed_locked_and_executable_keeps_blocking():
    from step01_script_gen.main import _downgrade_locked_only_audit_issues
    audit = _audit(_issue("narration_segment", repair_targets=[
        {"shot_id": "11", "field_path": "narration_segment"},
        {"shot_id": "11", "field_path": "frame_layouts.ff"},
    ]))
    assert _downgrade_locked_only_audit_issues(audit, contracts_ok=True) == 0
    assert audit["issues"][0]["severity"] == "blocking"


def test_contract_gate_failure_disables_downgrade():
    from step01_script_gen.main import _downgrade_locked_only_audit_issues
    audit = _audit(_issue("narration_segment"))
    assert _downgrade_locked_only_audit_issues(audit, contracts_ok=False) == 0
    assert audit["issues"][0]["severity"] == "blocking"


def test_audit_template_declares_locked_narration_rules():
    from step01_script_gen.audit import _AUDIT_RULES_TEMPLATE
    assert "narration_segment" in _AUDIT_RULES_TEMPLATE
    assert "不得" in _AUDIT_RULES_TEMPLATE
    assert "跨相邻镜头分句衔接" in _AUDIT_RULES_TEMPLATE
