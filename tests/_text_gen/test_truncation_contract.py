# -*- coding: utf-8 -*-
"""v6.62.6（09-09）截断契约跨 provider 一致性单测.

背景：`TextGenerationRequest.allow_truncated` 被定义为**供应商无关**的请求语义，
但 v6.62.6 首版只有 agent_plan 读它。其它 provider 连截断都不检测——于是同一段
调用点代码换 TEXT_PROVIDER 后语义**反转**：
  - 显式 allow_truncated=False（全脚本生成，「半个 JSON 不可用」）→ DeepSeek 仍
    把截断输出当 success=True 静默下发，正是 v6.62.5 修掉的那个根因；
  - 显式 allow_truncated=True（列表型提取）→ 拿不到 truncated 标记，调用方无法
    打 WARN，「提取到 N 个」静默少报。

本文件对全部 4 个 provider 断言同一张真值表：
  | 截断 | allow_truncated | success | truncated |
  |------|-----------------|---------|-----------|
  | 是   | False           | False   | True      |
  | 是   | True            | True    | True      |
  | 否   | 任意            | True    | False     |
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_HALF_JSON = '{"scene_objects": [{"identifier": "小舟"}, {"identifier": "马'


# ───────────────────────── 通用 helper 本身 ─────────────────────────


def test_helper_hard_fails_when_not_allowed():
    from _text_gen.models import truncated_result

    r = truncated_result(text=_HALF_JSON, usage={"output_tokens": 9}, allow_truncated=False,
                         error="被截断")
    assert r.success is False
    assert r.truncated is True, "硬失败也要置 truncated，调用方据此区分截断与其它失败"
    assert r.usage == {"output_tokens": 9}


def test_helper_passes_through_when_allowed():
    from _text_gen.models import truncated_result

    r = truncated_result(text=_HALF_JSON, usage=None, allow_truncated=True, error="被截断")
    assert (r.success, r.truncated, r.text) == (True, True, _HALF_JSON)


def test_helper_hard_fails_on_empty_text_even_when_allowed():
    """放行的前提是**有正文可抢救**；空正文放行等于把 success=True 交给空字符串."""
    from _text_gen.models import truncated_result

    r = truncated_result(text="   ", usage=None, allow_truncated=True, error="被截断")
    assert r.success is False


# ───────────────────────── DeepSeek（默认 provider）─────────────────────────


def _deepseek_service(monkeypatch):
    from _text_gen import deepseek_provider as ds_mod

    monkeypatch.setattr(ds_mod.config, "DEEPSEEK_API_KEY", "sk-test", raising=False)
    return ds_mod.DeepSeekTextService()


def _responses_obj(status: str, reason: str | None, text: str):
    """构造 Responses API 响应对象（v4-flash 路径）."""
    return SimpleNamespace(
        status=status,
        incomplete_details=SimpleNamespace(reason=reason) if reason else None,
        output=[SimpleNamespace(
            type="message",
            content=[SimpleNamespace(type="output_text", text=text)],
        )],
        usage=None,
    )


@pytest.mark.parametrize("allow,expect_success", [(False, False), (True, True)])
def test_deepseek_responses_truncation(monkeypatch, allow, expect_success):
    """Responses 路径 incomplete/max_output_tokens 走同一张真值表."""
    from _text_gen import models

    svc = _deepseek_service(monkeypatch)
    client = MagicMock()
    client.responses.create.return_value = _responses_obj(
        "incomplete", "max_output_tokens", _HALF_JSON
    )

    req = models.TextGenerationRequest(
        system_prompt="s", user_prompt="u", allow_truncated=allow,
    )
    with patch("openai.OpenAI", return_value=client):
        result = svc.generate(req)

    assert result.success is expect_success
    assert result.truncated is True, "DeepSeek 也必须标记截断（此前完全不标）"
    if expect_success:
        assert result.text == _HALF_JSON


def test_deepseek_responses_non_truncation_incomplete_still_hard_fails(monkeypatch):
    """incomplete 但原因不是 max_output_tokens（如 content_filter）→ 与 allow 无关，硬失败."""
    from _text_gen import models

    svc = _deepseek_service(monkeypatch)
    client = MagicMock()
    client.responses.create.return_value = _responses_obj(
        "incomplete", "content_filter", _HALF_JSON
    )

    req = models.TextGenerationRequest(
        system_prompt="s", user_prompt="u", allow_truncated=True,
    )
    with patch("openai.OpenAI", return_value=client):
        result = svc.generate(req)

    assert result.success is False
    assert result.truncated is False, "非截断原因不该冒充截断"
    assert "content_filter" in (result.error or "")


@pytest.mark.parametrize("allow,expect_success", [(False, False), (True, True)])
def test_deepseek_chat_truncation(monkeypatch, allow, expect_success):
    """Chat Completions 回退路径（reasoner 老模型）同样受契约约束.

    旧实现只在**正文为空**时才看 finish_reason，非空的半个 JSON 照样
    success=True 下发——与 v6.62.5 在 agent_plan 修掉的是同一个缺陷。
    """
    from _text_gen import models

    svc = _deepseek_service(monkeypatch)
    svc._model = "deepseek-reasoner"  # 触发 chat 回退路径
    client = MagicMock()
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=_HALF_JSON, reasoning_content=None),
            finish_reason="length",
        )],
        usage=None,
    )

    req = models.TextGenerationRequest(
        system_prompt="s", user_prompt="u", allow_truncated=allow,
    )
    with patch("openai.OpenAI", return_value=client):
        result = svc.generate(req)

    assert result.success is expect_success
    assert result.truncated is True


# ───────────────────────── DashScope ─────────────────────────


def _dashscope_response(finish: str, text: str):
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    resp.json.return_value = {
        "output": {"choices": [{
            "message": {"content": text},
            "finish_reason": finish,
        }]}
    }
    return resp


@pytest.mark.parametrize("allow,expect_success", [(False, False), (True, True)])
def test_dashscope_truncation(monkeypatch, allow, expect_success):
    from _text_gen import dashscope_provider as dm
    from _text_gen import models

    monkeypatch.setattr(dm.config, "DASHSCOPE_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr(dm.config, "validate", lambda: None, raising=False)
    svc = dm.DashScopeTextService()

    req = models.TextGenerationRequest(
        system_prompt="s", user_prompt="u", allow_truncated=allow,
    )
    with patch("requests.post", return_value=_dashscope_response("length", _HALF_JSON)):
        result = svc.generate(req)

    assert result.success is expect_success
    assert result.truncated is True


def test_dashscope_normal_stop_not_marked_truncated(monkeypatch):
    from _text_gen import dashscope_provider as dm
    from _text_gen import models

    monkeypatch.setattr(dm.config, "DASHSCOPE_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr(dm.config, "validate", lambda: None, raising=False)
    svc = dm.DashScopeTextService()

    req = models.TextGenerationRequest(system_prompt="s", user_prompt="u")
    with patch("requests.post", return_value=_dashscope_response("stop", '{"ok": 1}')):
        result = svc.generate(req)

    assert (result.success, result.truncated) == (True, False)


# ───────────────────────── Agnes ─────────────────────────


def _agnes_response(finish: str, text: str):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "choices": [{"message": {"content": text}, "finish_reason": finish}]
    }
    return resp


@pytest.mark.parametrize("allow,expect_success", [(False, False), (True, True)])
def test_agnes_truncation(monkeypatch, allow, expect_success):
    """Agnes 截断直接返回，**不进模型回退队列**——换模型不解决「输出超上限」."""
    from _text_gen import agnes_provider as am
    from _text_gen import models

    monkeypatch.setattr(am.config, "validate", lambda: None, raising=False)
    monkeypatch.setattr(am.config, "AGNES_API_KEY", "sk-test", raising=False)
    monkeypatch.setattr(am.config, "AGNES_BASE_URL", "https://example.invalid", raising=False)
    monkeypatch.setattr(am.config, "AGNES_TEXT_FALLBACK_MODELS", ["backup-model"], raising=False)
    svc = am.AgnesTextService()

    req = models.TextGenerationRequest(
        system_prompt="s", user_prompt="u", allow_truncated=allow,
    )
    post = MagicMock(return_value=_agnes_response("length", _HALF_JSON))
    with patch("requests.post", post):
        result = svc.generate(req)

    assert result.success is expect_success
    assert result.truncated is True
    assert post.call_count == 1, "截断不该触发回退模型重试"
