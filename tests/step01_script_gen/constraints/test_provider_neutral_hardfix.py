# -*- coding: utf-8 -*-
"""S49 migrated test: final hard_fix chain stays provider-neutral across text providers."""
from __future__ import annotations

from step01_script_gen.constraints import engine, run_hard_fix


def test_final_hardfix_is_provider_neutral(monkeypatch):
    """全局硬约束链对所有文本通道保持 provider-neutral（不含特定模型私有约束）."""
    script = {
        "shots": [{
            "index": 1,
            "scene": "庭院",
            "location": "庭院",
            "main_subject": "旅人说话",
            "prompt": "中景，固定机位。",
            "ff_desc": "旅人抬眼望向门口。",
            "lf_desc": "旅人仍在庭院内站立。",
            "motion_desc": "前2秒:旅人抬眼；2-4秒:旅人停下说话。",
            "core_keywords": "旅人,庭院",
            "narration_segment": "",
            "subtitle": "",
            "duration_sec": 4,
            "keyframes": [],
        }],
        "characters": [],
    }

    monkeypatch.setenv("TEXT_PROVIDER", "agent_plan")
    fixed, report = run_hard_fix(
        script,
        is_in_repair_loop=False,
        is_final_pass=True,
        return_report=True,
    )

    names = {c.name for c in engine.ALL_CONSTRAINTS}
    assert not any(name.startswith("doubao_") for name in names)
    assert isinstance(fixed, dict) and fixed.get("shots")
    assert isinstance(len(getattr(report, "hard_unresolved", [])), int)
