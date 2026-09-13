"""v6.68 per-model prompt 剖面单测：解析规则 + gen_spy_script 跟随模型.

背景：TEXT_PROVIDER=agent_plan 下此前所有模型共用 build_doubao_prompt +
DOUBAO_SYSTEM_PROMPT + novel≤1600 截断，曾造成候选结构性零高潮（高潮被
切在 prompt 外）。现在 prompt/system 跟模型走，且两个剖面都保留完整 novel。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# resolve_model_prompt_profile：解析规则
# ---------------------------------------------------------------------------

def test_profile_substring_match():
    from step01_script_gen.prompts import (
        DOUBAO_SYSTEM_PROMPT, SYSTEM_PROMPT,
        build_doubao_prompt, build_prompt,
        resolve_model_prompt_profile,
    )

    b, s = resolve_model_prompt_profile("doubao-seed-2-1-turbo")
    assert b is build_doubao_prompt and s is DOUBAO_SYSTEM_PROMPT
    b, s = resolve_model_prompt_profile("deepseek-v4-flash")
    assert b is build_prompt and s is SYSTEM_PROMPT
    # 大小写不敏感
    b, _ = resolve_model_prompt_profile("DeepSeek-V4-Flash")
    assert b is build_prompt


def test_profile_none_uses_provider_default_model(monkeypatch):
    """model=None → 按 provider 默认模型解析（GAP 重生成等调用点的一致性）."""
    from _text_gen.config import TextGenCfg
    from step01_script_gen.prompts import (
        DOUBAO_SYSTEM_PROMPT, SYSTEM_PROMPT,
        build_doubao_prompt, build_prompt,
        resolve_model_prompt_profile,
    )

    monkeypatch.setattr(TextGenCfg, "PROVIDER", "agent_plan")
    monkeypatch.setattr(TextGenCfg, "AGENT_PLAN_TEXT_MODEL", "deepseek-v4-flash")
    b, s = resolve_model_prompt_profile(None)
    assert b is build_prompt and s is SYSTEM_PROMPT

    monkeypatch.setattr(TextGenCfg, "AGENT_PLAN_TEXT_MODEL", "doubao-seed-2-1-turbo")
    b, s = resolve_model_prompt_profile(None)
    assert b is build_doubao_prompt and s is DOUBAO_SYSTEM_PROMPT


def test_profile_unknown_model_falls_back_to_provider_selection(monkeypatch):
    """未注册模型 → 回退 provider 级选择（旧行为兜底，不猜剖面）."""
    from _text_gen.config import TextGenCfg
    from step01_script_gen.prompts import (
        DOUBAO_SYSTEM_PROMPT, build_doubao_prompt,
        resolve_model_prompt_profile,
    )

    monkeypatch.setattr(TextGenCfg, "PROVIDER", "agent_plan")
    b, s = resolve_model_prompt_profile("qwen-plus")
    assert b is build_doubao_prompt and s is DOUBAO_SYSTEM_PROMPT


def test_default_text_model_accessor(monkeypatch):
    from _text_gen.config import TextGenCfg

    monkeypatch.setattr(TextGenCfg, "PROVIDER", "agent_plan")
    monkeypatch.setattr(TextGenCfg, "AGENT_PLAN_TEXT_MODEL", "m-x")
    assert TextGenCfg.default_text_model() == "m-x"
    monkeypatch.setattr(TextGenCfg, "PROVIDER", "unknown_prov")
    assert TextGenCfg.default_text_model() == ""


# ---------------------------------------------------------------------------
# gen_spy_script：prompt 跟随模型（截断差异实证）
# ---------------------------------------------------------------------------

# >1600 字：结尾高潮标记必须在两个模型剖面中都可见
_LONG_NOVEL = "开头铺垫。" + "中段过渡。" * 400 + "高潮标记词腥甜刺杀在此。"


class _OkResult:
    def __init__(self):
        self.success = True
        self.error = ""
        self.text = '{"ok": 1}'


class _CaptureService:
    def __init__(self, captured):
        self._captured = captured

    def generate(self, req):
        self._captured.append(req)
        return _OkResult()


def test_gen_spy_script_deepseek_profile_keeps_full_novel(monkeypatch):
    import step01_script_gen.main as m
    from step01_script_gen.prompts import SYSTEM_PROMPT

    captured = []
    monkeypatch.setattr(m, "create_service",
                        lambda *a, **k: _CaptureService(captured))
    m.gen_spy_script(_LONG_NOVEL, model="deepseek-v4-flash")
    assert captured[0].system_prompt is SYSTEM_PROMPT
    assert "腥甜刺杀" in captured[0].user_prompt  # 不截断：结尾高潮可见


def test_gen_spy_script_doubao_profile_keeps_full_novel(monkeypatch):
    import step01_script_gen.main as m
    from step01_script_gen.prompts import DOUBAO_SYSTEM_PROMPT

    captured = []
    monkeypatch.setattr(m, "create_service",
                        lambda *a, **k: _CaptureService(captured))
    m.gen_spy_script(_LONG_NOVEL, model="doubao-seed-2-1-turbo")
    assert captured[0].system_prompt is DOUBAO_SYSTEM_PROMPT
    assert "腥甜刺杀" in captured[0].user_prompt  # 不再私有截断：结尾高潮可见


def test_doubao_system_prompt_is_clean_and_consistent():
    from pathlib import Path

    from step01_script_gen.prompts import DOUBAO_SYSTEM_PROMPT

    path = (Path(__file__).resolve().parents[2]
            / "step01_script_gen" / "prompts" / "doubao_system.md")
    assert "\x00" not in path.read_text(encoding="utf-8")
    assert "0.05 ≤ t_sec" in DOUBAO_SYSTEM_PROMPT
    assert "威胁源统一写「神秘人」" in DOUBAO_SYSTEM_PROMPT
    assert "刺客：受死吧" not in DOUBAO_SYSTEM_PROMPT


def test_doubao_prompt_compact_outline_keeps_climax(monkeypatch):
    """超长大纲压缩时必须保留高潮锚点，不能被前置事件行挤掉."""
    from step01_script_gen.prompts import build_doubao_prompt

    monkeypatch.setattr("step01_script_gen.prompts.PROMPT_TEMPLATE", "{novel_text}")
    events = "；".join(f"事件{i:02d}" for i in range(30))
    outline_anchor = (
        f"【本章主要事件】{events}\n"
        "【情绪弧线】压抑试探，逐步升温，最后爆发。\n"
        "【高潮事件】本章刺杀反转完成。\n"
        "【结尾钩子】留下更大的阴谋伏笔。"
    )
    prompt = build_doubao_prompt("novel", outline_anchor=outline_anchor)
    outline_part = prompt.split(
        "### 本章大纲（分镜必须覆盖以下事件与高潮）\n", 1
    )[1]
    assert "【高潮事件】本章刺杀反转完成。" in outline_part
    assert "【结尾钩子】" not in outline_part
    assert len(outline_part.split("\n###", 1)[0]) <= 200
