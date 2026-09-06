from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_agent_plan_selects_doubao_system_prompt():
    from step01_script_gen.prompts import (
        DOUBAO_SYSTEM_PROMPT,
        SYSTEM_PROMPT,
        auto_select_system_prompt,
    )

    assert auto_select_system_prompt("agent_plan") is DOUBAO_SYSTEM_PROMPT
    assert DOUBAO_SYSTEM_PROMPT is not SYSTEM_PROMPT


def test_other_providers_select_default_system_prompt():
    from step01_script_gen.prompts import SYSTEM_PROMPT, auto_select_system_prompt

    assert auto_select_system_prompt("deepseek") is SYSTEM_PROMPT
    assert auto_select_system_prompt("unknown") is SYSTEM_PROMPT


def test_agent_plan_user_prompt_does_not_embed_system_prompt(monkeypatch):
    from step01_script_gen.prompts import (
        DOUBAO_SYSTEM_PROMPT,
        build_doubao_prompt,
    )

    monkeypatch.setattr("step01_script_gen.prompts.PROMPT_TEMPLATE", "{novel_text}")
    prompt = build_doubao_prompt("novel")

    assert "novel" in prompt
    assert DOUBAO_SYSTEM_PROMPT not in prompt


def test_auto_select_prompt_builder_returns_main_and_hook_builders():
    from step01_script_gen.prompts import (
        build_doubao_hook_prompt,
        build_doubao_prompt,
        build_hook_prompt,
        build_prompt,
        auto_select_prompt_builder,
    )

    assert auto_select_prompt_builder("agent_plan") == (
        build_doubao_prompt,
        build_doubao_hook_prompt,
    )
    assert auto_select_prompt_builder("deepseek") == (
        build_prompt,
        build_hook_prompt,
    )
