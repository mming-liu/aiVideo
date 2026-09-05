# -*- coding: utf-8 -*-
"""2026-09-03 Agent Plan (/api/plan/v3) 文本 provider 接入单测.

覆盖：
  1. 注册：service._auto_register() 把 agent_plan 写入注册表
  2. key 缺失：构造时 ValueError 文案含「AGENT_PLAN_API_KEY」「专属 key」
  3. basic submit：POST URL 走 /chat/completions、payload 含 model/messages/温度参数
  4. user_inputs 多轮：messages 数组按 system + user_inputs 拼接
  5. 401 → error 文案含「Agent Plan 鉴权失败」+「专属 key」
  6. 403 → error 文案含「套餐无权限或燃料耗尽」
  7. 400 → error 文案含「参数错误」
  8. 5xx 4 次重试用尽后报错
  9. 429 重试：按 retry-after 等待后再次提交
  10. max_tokens 透传：req 显式 > 0 → payload 必含 max_tokens
  11. reasoning_effort 透传：=high → payload 必含 reasoning_effort
  12. json_schema 透传：payload 含 response_format.type=json_schema + schema
  13. 响应解析：choices[0].message.content 提取正确（OpenAI 兼容）
  14. usage dict 提取：prompt_tokens / completion_tokens / total_tokens
  15. KeyScheduler cooldown = 60 / AGENT_PLAN_TEXT_RPM
  16. _text_gen/config.py validate 路径：PROVIDER=agent_plan 且无 key → RuntimeError

设计原则：mock HTTP、不真调 API；与 _image_gen/test_agent_plan_provider.py 同源。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ───────────────────────── 注册测试 ─────────────────────────


def test_text_service_registered():
    """agent_plan 必须在 _text_gen/service._REGISTRY 中."""
    from _text_gen import service
    service._auto_register()
    assert "agent_plan" in service._REGISTRY
    from _text_gen.agent_plan_provider import AgentPlanTextService
    assert service._REGISTRY["agent_plan"] is AgentPlanTextService


# ───────────────────────── 配置 + key 缺失 fail-fast ─────────────────────────


def test_text_service_missing_key_raises(monkeypatch):
    """key 缺失时 ValueError 文案必须含「AGENT_PLAN_API_KEY」+「专属 key」提示."""
    from _text_gen import agent_plan_provider as ap_mod
    from _text_gen import config as tgcfg

    # 双层 patch：tgcfg.AGENT_PLAN_API_KEY + ap_mod.config.AGENT_PLAN_API_KEY
    # （provider 在 import 时绑定了 `from .config import config` 的引用）
    monkeypatch.setattr(tgcfg, "AGENT_PLAN_API_KEY", "")
    monkeypatch.setattr(ap_mod.config, "AGENT_PLAN_API_KEY", "")

    with pytest.raises(ValueError) as excinfo:
        ap_mod.AgentPlanTextService()
    msg = str(excinfo.value)
    assert "AGENT_PLAN_API_KEY" in msg
    assert "专属 key" in msg


def test_config_validate_agent_plan_missing_key(monkeypatch):
    """TextGenCfg.validate() 在 PROVIDER=agent_plan 且无 key 时 RuntimeError.

    注意：PROVIDER/API_KEY 是 class attribute（在 import 时由 os.getenv 赋值），
    validate() 用 `cls.X` 读取，monkeypatch 必须打到 class 上才生效。
    """
    from _text_gen import config as tgcfg
    cls = type(tgcfg)
    monkeypatch.setattr(cls, "AGENT_PLAN_API_KEY", "")
    monkeypatch.setattr(cls, "PROVIDER", "agent_plan")

    with pytest.raises(RuntimeError) as excinfo:
        cls.validate()
    assert "AGENT_PLAN_API_KEY" in str(excinfo.value)


# ───────────────────────── provider 单测 helper ─────────────────────────


def _make_text_service(monkeypatch):
    """构造一个最小可用的 AgentPlanTextService（注入假 key + 默认 base URL）."""
    from _text_gen import agent_plan_provider as ap_mod
    from _text_gen import config as tgcfg
    monkeypatch.setattr(tgcfg, "AGENT_PLAN_API_KEY", "test-key")
    monkeypatch.setattr(ap_mod.config, "AGENT_PLAN_API_KEY", "test-key")
    monkeypatch.setattr(ap_mod.config, "AGENT_PLAN_BASE_URL", "http://fake")
    monkeypatch.setattr(ap_mod.config, "AGENT_PLAN_TEXT_MODEL", "doubao-seed-2-1-turbo")
    # 60 RPM → 1s cooldown，acquire 几乎不阻塞
    monkeypatch.setattr(ap_mod.config, "AGENT_PLAN_TEXT_RPM", 60)
    return ap_mod.AgentPlanTextService()


def _ok_response(content: str = "分镜脚本内容", usage: dict | None = None) -> MagicMock:
    """构造一个 200 chat completions 响应 mock."""
    body = {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": usage or {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300},
    }
    resp = MagicMock(status_code=200, text=str(body)[:200])
    resp.json.return_value = body
    return resp


# ───────────────────────── 基础提交 ─────────────────────────


def test_text_submit_basic_payload(monkeypatch):
    """基本提交：POST URL 走 /chat/completions、payload 含 model/messages/温度参数.

    v2 修正（2026-09-03）：火山方舟 Doubao 专用字段命名：
      - max_completion_tokens（不是 max_tokens）
      - thinking.type=disabled（不是 reasoning_effort）
      - response_format.type=json_object（不是 json_schema strict）
    """
    svc = _make_text_service(monkeypatch)
    submit = _ok_response()

    from _text_gen import models
    req = models.TextGenerationRequest(system_prompt="你是分镜师", user_prompt="写分镜")

    with patch("requests.post", return_value=submit) as mock_post:
        result = svc.generate(req)

    assert result.success is True, f"got error: {result.error}"
    assert result.text == "分镜脚本内容"

    # 验证 POST URL 走 /chat/completions 通道
    call_args = mock_post.call_args
    assert "/chat/completions" in call_args[0][0]
    assert call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"

    payload = call_args.kwargs.get("json") or call_args[1].get("json")
    assert payload["model"] == "doubao-seed-2-1-turbo"
    assert payload["messages"] == [
        {"role": "system", "content": "你是分镜师"},
        {"role": "user", "content": "写分镜"},
    ]
    assert payload["temperature"] == 0.05
    assert payload["top_p"] == 0.2
    assert payload["seed"] == 42
    # 火山方舟 Doubao 专用：max_completion_tokens（不是 max_tokens）
    assert payload["max_completion_tokens"] == 16384
    # 火山方舟 Doubao 专用：thinking.type=disabled（默认 reasoning_effort=none 映射）
    assert payload["thinking"] == {"type": "disabled"}
    # 默认 response_format=json_object（方舟 Doubao 稳定；json_schema strict 视模型而定）
    assert payload["response_format"] == {"type": "json_object"}
    # 客户端 timeout 升到 300s（长任务）
    assert call_args.kwargs["timeout"] == 300


def test_text_submit_user_inputs(monkeypatch):
    """user_inputs 多轮：messages 数组按 system + user_inputs 拼接."""
    svc = _make_text_service(monkeypatch)
    submit = _ok_response()

    from _text_gen import models
    req = models.TextGenerationRequest(
        system_prompt="你是分镜师",
        user_prompt="写分镜",  # 应被 user_inputs 覆盖
        user_inputs=[
            {"role": "user", "content": "INPUT(原文+当前分镜)"},
            {"role": "assistant", "content": "首版分镜"},
            {"role": "user", "content": "REPAIR FEEDBACK"},
        ],
    )

    with patch("requests.post", return_value=submit) as mock_post:
        svc.generate(req)

    payload = mock_post.call_args.kwargs["json"]
    assert payload["messages"] == [
        {"role": "system", "content": "你是分镜师"},
        {"role": "user", "content": "INPUT(原文+当前分镜)"},
        {"role": "assistant", "content": "首版分镜"},
        {"role": "user", "content": "REPAIR FEEDBACK"},
    ]


def test_text_submit_max_completion_tokens_passthrough(monkeypatch):
    """max_completion_tokens 透传：req 显式 > 0 → payload 必含 max_completion_tokens.

    v2 修正：方舟 Doubao 字段名是 max_completion_tokens（不是 max_tokens）。
    """
    svc = _make_text_service(monkeypatch)
    submit = _ok_response()

    from _text_gen import models
    req = models.TextGenerationRequest(system_prompt="x", user_prompt="y", max_tokens=8000)

    with patch("requests.post", return_value=submit) as mock_post:
        svc.generate(req)

    payload = mock_post.call_args.kwargs["json"]
    assert payload["max_completion_tokens"] == 8000


def test_text_submit_thinking_enabled(monkeypatch):
    """thinking 模式开启：req.reasoning_effort=high → payload.thinking.type=enabled.

    v2 修正：方舟 Doubao 字段名是 thinking.type（不是 reasoning_effort）。
    """
    svc = _make_text_service(monkeypatch)
    submit = _ok_response()

    from _text_gen import models
    req = models.TextGenerationRequest(
        system_prompt="x", user_prompt="y", reasoning_effort="high"
    )

    with patch("requests.post", return_value=submit) as mock_post:
        svc.generate(req)

    payload = mock_post.call_args.kwargs["json"]
    assert payload["thinking"] == {"type": "enabled"}


def test_text_submit_json_object_response_format(monkeypatch):
    """response_format 默认 json_object（方舟 Doubao 稳定；json_schema strict 视模型而定）.

    v2 修正：request 即使带 json_schema，provider 也走 json_object 强约束；
    严格 schema 校验放到 _validate_script 端二次兜底。
    """
    svc = _make_text_service(monkeypatch)
    submit = _ok_response()

    from _text_gen import models
    req = models.TextGenerationRequest(
        system_prompt="x",
        user_prompt="y",
        json_schema={
            "name": "shot_breakdown",
            "schema": {"type": "object", "properties": {"shots": {"type": "array"}}},
        },
    )

    with patch("requests.post", return_value=submit) as mock_post:
        svc.generate(req)

    payload = mock_post.call_args.kwargs["json"]
    # v2 决策：走 json_object 而非 json_schema strict（方舟 Doubao 稳定）
    assert payload["response_format"] == {"type": "json_object"}


# ───────────────────────── 错误码映射 ─────────────────────────


def test_text_submit_401_returns_auth_error(monkeypatch):
    """401 → error 文案含「Agent Plan 鉴权失败」+「专属 key」."""
    svc = _make_text_service(monkeypatch)
    resp = MagicMock(status_code=401, text='{"error":{"code":"Unauthorized"}}')

    from _text_gen import models
    req = models.TextGenerationRequest(system_prompt="x", user_prompt="y")
    with patch("requests.post", return_value=resp):
        result = svc.generate(req)

    assert result.success is False
    assert "Agent Plan 鉴权失败" in result.error
    assert "专属 key" in result.error


def test_text_submit_403_returns_quota_error(monkeypatch):
    """403 → error 文案含「套餐无权限或燃料耗尽」."""
    svc = _make_text_service(monkeypatch)
    resp = MagicMock(status_code=403, text='{"error":"quota exhausted"}')

    from _text_gen import models
    req = models.TextGenerationRequest(system_prompt="x", user_prompt="y")
    with patch("requests.post", return_value=resp):
        result = svc.generate(req)

    assert result.success is False
    assert "套餐无权限" in result.error
    assert "燃料耗尽" in result.error


def test_text_submit_400_returns_param_error(monkeypatch):
    """400 → error 文案含「参数错误」."""
    svc = _make_text_service(monkeypatch)
    resp = MagicMock(status_code=400, text='{"error":"bad param"}')

    from _text_gen import models
    req = models.TextGenerationRequest(system_prompt="x", user_prompt="y")
    with patch("requests.post", return_value=resp):
        result = svc.generate(req)

    assert result.success is False
    assert "参数错误" in result.error
    assert "400" in result.error


def test_text_submit_5xx_retries_then_fails(monkeypatch):
    """5xx 走 1 次退避，重试用尽后报错.

    v2 修正：长任务不重复重试浪费额度，只做 1 次（初始 + 1 次 = 共 2 次提交）。
    """
    svc = _make_text_service(monkeypatch)
    resp = MagicMock(status_code=503, text='{"error":"busy"}')

    from _text_gen import models
    req = models.TextGenerationRequest(system_prompt="x", user_prompt="y")
    with patch("requests.post", return_value=resp) as mock_post, \
         patch("time.sleep", return_value=None):
        result = svc.generate(req)

    assert result.success is False
    assert "503" in result.error
    # 2 次提交（初始 + 1 次重试）
    assert mock_post.call_count == 2


def test_text_submit_429_retries_after_retry_after(monkeypatch):
    """429 走 retry-after 等待后再次提交，第二次 200 则成功."""
    svc = _make_text_service(monkeypatch)
    rate_limited = MagicMock(
        status_code=429, text='{"error":"rate limited","retry_after":3}'
    )
    ok = _ok_response()

    from _text_gen import models
    req = models.TextGenerationRequest(system_prompt="x", user_prompt="y")
    with patch("requests.post", side_effect=[rate_limited, ok]) as mock_post, \
         patch("time.sleep", return_value=None) as mock_sleep:
        result = svc.generate(req)

    assert result.success is True, f"got error: {result.error}"
    assert mock_post.call_count == 2
    # 429 走 retry-after 解析（3.0）
    assert any(
        call.args and call.args[0] == 3.0 for call in mock_sleep.call_args_list
    ), f"expected sleep(3.0), got: {[c.args for c in mock_sleep.call_args_list]}"


def test_text_submit_client_timeout_no_retry(monkeypatch):
    """客户端超时（read timeout）→ 立即报错不重试（v2 长任务不重发原则）."""
    import requests as _req
    svc = _make_text_service(monkeypatch)

    from _text_gen import models
    req = models.TextGenerationRequest(system_prompt="x", user_prompt="y")
    # mock requests.post 抛 Timeout
    with patch("requests.post", side_effect=_req.exceptions.Timeout("read out")) as mock_post:
        result = svc.generate(req)

    assert result.success is False
    # 长任务客户端超时：只 1 次提交，不重试
    assert mock_post.call_count == 1
    assert "客户端超时" in result.error
    assert "不重试" in result.error or "不应自动重试" in result.error


# ───────────────────────── 响应解析 + usage ─────────────────────────


def test_text_response_choices_message_content(monkeypatch):
    """响应解析：choices[0].message.content 提取正确（OpenAI 兼容）."""
    svc = _make_text_service(monkeypatch)
    submit = _ok_response(content="特定分镜内容")

    from _text_gen import models
    req = models.TextGenerationRequest(system_prompt="x", user_prompt="y")
    with patch("requests.post", return_value=submit):
        result = svc.generate(req)

    assert result.success is True
    assert result.text == "特定分镜内容"


def test_text_response_empty_choices_returns_error(monkeypatch):
    """空 choices → 报错."""
    svc = _make_text_service(monkeypatch)
    submit = MagicMock(status_code=200)
    submit.json.return_value = {"choices": [], "usage": {}}

    from _text_gen import models
    req = models.TextGenerationRequest(system_prompt="x", user_prompt="y")
    with patch("requests.post", return_value=submit):
        result = svc.generate(req)

    assert result.success is False
    assert "无 choices" in result.error


def test_text_response_empty_content_returns_error(monkeypatch):
    """空 content → 报错（带 finish_reason 诊断）."""
    svc = _make_text_service(monkeypatch)
    body = {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": ""},
                "finish_reason": "length",
            }
        ],
        "usage": {},
    }
    submit = MagicMock(status_code=200)
    submit.json.return_value = body

    from _text_gen import models
    req = models.TextGenerationRequest(system_prompt="x", user_prompt="y")
    with patch("requests.post", return_value=submit):
        result = svc.generate(req)

    assert result.success is False
    assert "文本为空" in result.error
    assert "length" in result.error


def test_text_response_usage_dict(monkeypatch):
    """usage dict 提取：prompt_tokens / completion_tokens / reasoning_tokens / total_tokens.

    v2 修正：方舟 Doubao 的 usage 含 completion_tokens_details.reasoning_tokens
    （思考模式开启时才有），供 bench 分析思维链开销。
    """
    svc = _make_text_service(monkeypatch)
    submit = _ok_response(
        usage={
            "prompt_tokens": 1234,
            "completion_tokens": 567,
            "total_tokens": 1801,
            "completion_tokens_details": {"reasoning_tokens": 800},
        }
    )

    from _text_gen import models
    req = models.TextGenerationRequest(system_prompt="x", user_prompt="y")
    with patch("requests.post", return_value=submit):
        result = svc.generate(req)

    assert result.success is True
    assert result.usage == {
        "input_tokens": 1234,
        "output_tokens": 567,
        "reasoning_tokens": 800,
        "total_tokens": 1801,
    }


def test_text_response_usage_no_reasoning_when_disabled(monkeypatch):
    """thinking.disabled 时 usage.completion_tokens_details 可能缺省，reasoning_tokens 应为 None."""
    svc = _make_text_service(monkeypatch)
    submit = _ok_response(
        usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
    )

    from _text_gen import models
    req = models.TextGenerationRequest(system_prompt="x", user_prompt="y")
    with patch("requests.post", return_value=submit):
        result = svc.generate(req)

    assert result.success is True
    assert result.usage["reasoning_tokens"] is None


# ───────────────────────── KeyScheduler 限速 ─────────────────────────


def test_text_key_scheduler_cooldown_matches_rpm(monkeypatch):
    """KeyScheduler cooldown = 60 / AGENT_PLAN_TEXT_RPM."""
    from _text_gen import agent_plan_provider as ap_mod
    from _text_gen import config as tgcfg

    monkeypatch.setattr(tgcfg, "AGENT_PLAN_API_KEY", "test-key")
    monkeypatch.setattr(ap_mod.config, "AGENT_PLAN_API_KEY", "test-key")
    monkeypatch.setattr(ap_mod.config, "AGENT_PLAN_TEXT_RPM", 20)  # 60/20=3s

    svc = ap_mod.AgentPlanTextService()
    assert svc._key_scheduler._cooldown == 3.0


# ───────────────────────── config 字段默认值 ─────────────────────────


def test_text_config_default_model():
    """TextGenCfg.AGENT_PLAN_TEXT_MODEL 默认 doubao-seed-2-1-turbo."""
    from _text_gen import config as tgcfg
    assert tgcfg.AGENT_PLAN_TEXT_MODEL == "doubao-seed-2-1-turbo"


def test_text_config_default_reasoning_effort():
    """TextGenCfg.AGENT_PLAN_TEXT_REASONING_EFFORT 默认 none."""
    from _text_gen import config as tgcfg
    assert tgcfg.AGENT_PLAN_TEXT_REASONING_EFFORT == "none"


def test_text_config_default_max_tokens_is_none(monkeypatch):
    """TextGenCfg.AGENT_PLAN_TEXT_MAX_TOKENS 默认 None（0=不传）.

    .env.content 显式 pin 了 16384（2026-09-04 三处对齐决策），不能直接断言
    已加载单例——置空串模拟「未配置」（load_dotenv 不覆盖已存在 env），
    重载产线 config 模块后断言归一为 None（provider 侧另有 16384 fallback）。
    finally 里按 .env 同值重载还原，避免污染后续测试。
    """
    import importlib
    monkeypatch.setenv("AGENT_PLAN_TEXT_MAX_TOKENS", "")
    _cfg = importlib.import_module("_text_gen.config")
    importlib.reload(_cfg)
    try:
        assert _cfg.config.AGENT_PLAN_TEXT_MAX_TOKENS is None
    finally:
        monkeypatch.setenv("AGENT_PLAN_TEXT_MAX_TOKENS", "16384")
        importlib.reload(_cfg)
    assert _cfg.config.AGENT_PLAN_TEXT_MAX_TOKENS == 16384
