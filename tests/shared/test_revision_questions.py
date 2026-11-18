# -*- coding: utf-8 -*-
"""R7：修订/核验提示词三问（2026-11-18）."""
from __future__ import annotations

from shared.revision_questions import (REVISION_SELF_CHECK_QUESTIONS,
                                       render_revision_questions)


def test_three_questions_are_rendered():
    text = render_revision_questions()
    assert "摘句" in text and "条件" in text
    assert "动机" in text and "威胁" in text
    assert "出发地" in text and "目的地" in text
    for q in REVISION_SELF_CHECK_QUESTIONS:
        assert q in text


def test_storyboard_feedback_includes_questions(monkeypatch):
    from step01_script_gen import feedback, validation
    monkeypatch.setattr(validation, "_run_prompt_checklist", lambda script: {
        "results": {"x": {"warnings": ["镜头1 测试告警"]}}})
    text = feedback.build_regen_feedback({})
    assert "修订前自检三问" in text
    assert "动机与威胁" in text


def test_content_verification_prompt_includes_questions():
    from step01_script_gen import content_verify
    for q in REVISION_SELF_CHECK_QUESTIONS:
        assert q in content_verify.SYSTEM_PROMPT
