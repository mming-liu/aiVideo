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
  17. 连接重置 ×2 后成功 ⇒ **真实循环发生 3 次 POST**（v6.152：循环上限取类别最宽值）
  18. 连接重置全失败 ⇒ 3 次收口，文案含 `transport_connect` 与底层异常，不出现「未知原因」
  19. 连接类次数仍由策略单一来源决定：`TEXT_TRANSPORT_CONNECT_RETRIES=0` ⇒ 共 2 次
  20. 429 耗尽仍按**自身**次数收口（2 次），不随连接类放宽
  21. 5xx 耗尽同样 2 次，且收口文案带上服务端原因
  22. 网关会话头 `x-opencode-session`：未配置 ⇒ 自动生成**进程内稳定**值；显式配置优先
      （opencode go 网关缺它直接 400 MissingSessionID，2026-10-02 实测）
  23. 文本专用端点 `AGENT_PLAN_TEXT_BASE_URL`：配了用它、留空回落共享端点（图片侧不动）

设计原则：mock HTTP、不真调 API；与 _image_gen/test_agent_plan_provider.py 同源。
"""
from __future__ import annotations

import json as _json_wire


def _sent_payload(call_args):
    """S02：provider 以 data=<utf-8 bytes> 发送；测试统一从这里取回 payload."""
    kw = getattr(call_args, "kwargs", None) or call_args[1]
    body = kw.get("json")
    if body is None:
        raw = kw.get("data")
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        body = _json_wire.loads(raw) if raw else None
    return body


import os
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


#: 两个端点的**请求方言**（2026-10-03）：端点即契约，测试必须显式钉住自己测的是哪一个。
ARK_URL = "https://ark.cn-beijing.volces.com/api/plan/v3"
OPENCODE_URL = "https://opencode.ai/zen/go/v1"


def _make_text_service(monkeypatch, base_url: str = ARK_URL,
                       shared_base_url: str = ARK_URL):
    """构造一个最小可用的 AgentPlanTextService（注入假 key + 显式端点）.

    `base_url` 默认给**方舟**端点：本文件历史上写的是 Doubao 方言的断言（`thinking.type`），
    显式钉住端点后这些断言才名副其实。
    """
    from _text_gen import agent_plan_provider as ap_mod
    from _text_gen import config as tgcfg
    monkeypatch.setattr(tgcfg, "AGENT_PLAN_API_KEY", "test-key")
    monkeypatch.setattr(ap_mod.config, "AGENT_PLAN_API_KEY", "test-key")
    monkeypatch.setattr(ap_mod.config, "AGENT_PLAN_BASE_URL", shared_base_url)
    monkeypatch.setattr(ap_mod.config, "AGENT_PLAN_TEXT_BASE_URL", base_url)
    monkeypatch.setattr(ap_mod.config, "AGENT_PLAN_TEXT_MODEL", "doubao-seed-2-1-turbo")
    # 60 RPM → 1s cooldown，acquire 几乎不阻塞
    monkeypatch.setattr(ap_mod.config, "AGENT_PLAN_TEXT_RPM", 60)
    return ap_mod.AgentPlanTextService()


from _text_gen.models import TextGenerationRequest as _TextReq


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


def test_empty_content_with_length_is_truncated_not_plain_failure(monkeypatch):
    """v6.144：空正文 + `finish_reason=length` = **截断**，不是普通失败.

    2026-09-28 实测（审计跑批）：思维链吃光 32000 配额、正文一个字没吐，旧实现
    把它当「返回文本为空」→ 上层按 `request_failed`（不可重试）处置 → 直接 BLOCK。
    v6.62.6 契约要求**四个 provider 的截断都经 `truncated_result`**（统一落法），
    本分支此前漏了；漏掉的后果是「换一次采样就能好」的失败被当成终局失败。
    """
    svc = _make_text_service(monkeypatch)
    body = {
        "choices": [
            {
                "index": 0,
                "finish_reason": "length",
                "message": {"role": "assistant", "content": "",
                            "reasoning_content": "Let me analyze the shots for issues."},
            }
        ],
        "usage": {"prompt_tokens": 21170, "completion_tokens": 32000,
                  "total_tokens": 53170},
    }
    resp = MagicMock(status_code=200, text=str(body)[:200])
    resp.json.return_value = body

    from _text_gen import models
    req = models.TextGenerationRequest(system_prompt="s", user_prompt="u")
    with patch("requests.post", return_value=resp) as mock_post:
        result = svc.generate(req)

    assert result.success is False
    assert getattr(result, "truncated", False) is True, "截断必须带 truncated=True"
    assert "截断" in result.error, result.error
    assert mock_post.call_count == 1, "截断不在 provider 层重试（由调用点决定换采样）"


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

    payload = _sent_payload(call_args)
    assert payload["model"] == "doubao-seed-2-1-turbo"
    assert payload["messages"] == [
        {"role": "system", "content": "你是分镜师"},
        {"role": "user", "content": "写分镜"},
    ]
    assert payload["temperature"] == 0.05
    assert payload["top_p"] == 0.2
    assert payload["seed"] == 42
    # 火山方舟 Doubao 专用：max_completion_tokens（不是 max_tokens）
    # v6.62.5：断言**读 provider 实际引用的 config 而非硬写数字**——该值单一来源是
    # .env.content，测试里再写一遍就成了第三处副本（改一次要动三个文件）。
    from _text_gen import agent_plan_provider as _ap
    assert payload["max_completion_tokens"] == _ap.config.AGENT_PLAN_TEXT_MAX_TOKENS
    # 火山方舟 Doubao 专用：thinking.type=disabled（默认 reasoning_effort=none 映射）
    assert payload["thinking"] == {"type": "disabled"}
    # 默认 response_format=json_object（方舟 Doubao 稳定；json_schema strict 视模型而定）
    assert payload["response_format"] == {"type": "json_object"}
    # 客户端 timeout 可配置（AGENT_PLAN_TEXT_TIMEOUT_SEC，默认 600——doubao 长任务实测可超 300s）
    from _text_gen import agent_plan_provider as _ap_mod
    assert call_args.kwargs["timeout"] == _ap_mod.AGENT_PLAN_TEXT_TIMEOUT_SEC


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

    payload = _sent_payload(mock_post.call_args)
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

    payload = _sent_payload(mock_post.call_args)
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

    payload = _sent_payload(mock_post.call_args)
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

    payload = _sent_payload(mock_post.call_args)
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
    """空 content + `finish_reason=stop` → 普通失败（真·空响应，带 finish_reason 诊断）.

    注意与下一条的分工（v6.144）：**空正文 + `length` 是截断**，走 `truncated=True`；
    本条的 `stop` 才是「模型什么都没给」的普通失败。
    """
    svc = _make_text_service(monkeypatch)
    body = {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": ""},
                "finish_reason": "stop",
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
    assert getattr(result, "truncated", False) is False
    assert "文本为空" in result.error
    assert "finish_reason=stop" in result.error, "诊断必须带 finish_reason"


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
    """TextGenCfg.AGENT_PLAN_TEXT_MODEL：代码默认 doubao-seed-2-1-turbo.

    允许的环境覆盖值随生产决策增补（2026-09-26 deepseek-v4-flash；
    2026-09-28 用户决定 step01 只走 agent_plan 的 **deepseek-v4.1-flash**）。
    这条是「模型名没被手误写坏」的粗护栏，不是行为断言。
    """
    from _text_gen import config as tgcfg
    assert tgcfg.AGENT_PLAN_TEXT_MODEL in ("doubao-seed-2-1-turbo",
                                           "deepseek-v4-flash",
                                           "deepseek-v4.1-flash")


def test_text_config_default_reasoning_effort():
    """TextGenCfg.AGENT_PLAN_TEXT_REASONING_EFFORT 默认 none."""
    from _text_gen import config as tgcfg
    assert tgcfg.AGENT_PLAN_TEXT_REASONING_EFFORT == "none"


def test_text_config_default_max_tokens_is_none(monkeypatch):
    """TextGenCfg.AGENT_PLAN_TEXT_MAX_TOKENS 未配置（0/空）时归一为 None（=不传）.

    .env.content 显式 pin 了值，不能直接断言已加载单例——置空串模拟「未配置」
    （load_dotenv 不覆盖已存在 env），重载产线 config 模块后断言归一为 None。

    v6.62.5 修测试污染：原实现 finally 里 `setenv("...", "16384")` 再 reload，
    于是**把 16384 泄漏给后续所有测试**（monkeypatch 只还原 env 变量，无法
    还原已 reload 的模块），且硬写的 16384 与 .env 真值脱钩。改为 delenv 后
    reload——此时 load_dotenv 会重新从 .env.content 读回真值。
    """
    import importlib
    _cfg = importlib.import_module("_text_gen.config")
    monkeypatch.setenv("AGENT_PLAN_TEXT_MAX_TOKENS", "")
    importlib.reload(_cfg)
    try:
        assert _cfg.config.AGENT_PLAN_TEXT_MAX_TOKENS is None
    finally:
        monkeypatch.delenv("AGENT_PLAN_TEXT_MAX_TOKENS", raising=False)
        importlib.reload(_cfg)
    # 还原后应等于 .env.content 的真值（不硬写数字）
    assert _cfg.config.AGENT_PLAN_TEXT_MAX_TOKENS is not None


# ───────────────── v6.62.5（09-08）截断硬失败 ─────────────────
#
# 根因：旧实现只在 content **为空** 时才看 finish_reason；非空但被截断的半个 JSON
# 被当成 success=True 交给下游，失败以「无法解析 JSON」的形态出现在很远的调用点。
# 09-08 实测：单次跑批 20 次调用 3 次 finish_reason=length，与 3 次 JSON 解析失败
# 1:1 对应。用户决策：截断硬失败，不做自动升档重试。

def _truncated_response(completion_tokens: int = 32768) -> MagicMock:
    """finish_reason=length 的响应：content 非空但 JSON 被切断."""
    body = {
        "choices": [{
            "index": 0,
            "message": {"role": "assistant",
                        "content": '{"shots": [{"index": 1, "prompt": "半句被切'},
            "finish_reason": "length",
        }],
        "usage": {"prompt_tokens": 40000, "completion_tokens": completion_tokens,
                  "total_tokens": 40000 + completion_tokens},
    }
    resp = MagicMock(status_code=200, text=str(body)[:200])
    resp.json.return_value = body
    return resp


def test_truncated_output_is_hard_failure(monkeypatch):
    """finish_reason=length + 非空内容 → success=False（旧行为是 success=True）.

    附带断言 usage 回传：_usage_dict 把 completion_tokens 重命名为 output_tokens，
    错误文案取值时用错 key 会静默打出 None（实现时踩过）。
    """
    svc = _make_text_service(monkeypatch)
    from _text_gen import models
    req = models.TextGenerationRequest(system_prompt="s", user_prompt="u")

    with patch("requests.post", return_value=_truncated_response(completion_tokens=12345)):
        result = svc.generate(req)

    assert result.success is False, "截断必须硬失败，不能把半个 JSON 当成功交给下游"
    assert "截断" in (result.error or "")
    assert result.usage["output_tokens"] == 12345
    assert result.truncated is True


def test_truncated_output_passes_through_when_allowed(monkeypatch):
    """allow_truncated=True → success=True + truncated=True（v6.62.6）.

    列表型提取（scene_objects 等）走 json_repair._truncate_fix，能丢弃未完成的
    尾对象、保留前面完整对象；v6.62.5 的无条件硬失败把这条抢救路径也掐了。
    """
    svc = _make_text_service(monkeypatch)
    from _text_gen import models
    req = models.TextGenerationRequest(
        system_prompt="s", user_prompt="u", allow_truncated=True
    )

    with patch("requests.post", return_value=_truncated_response()):
        result = svc.generate(req)

    assert result.success is True, "列表型提取要放行截断内容，交给 _truncate_fix 抢救"
    assert result.truncated is True, "放行也必须置 truncated，供调用方打 WARN"
    assert result.text, "放行时必须把（截断的）文本交给下游解析"


def _payloads(svc, monkeypatch, pairs):
    """{(effort, cap): payload} —— 一律从**真实 provider 循环**里取 serialized payload."""
    from unittest.mock import patch

    from _text_gen import TextGenerationRequest
    from _text_gen import agent_plan_provider as _ap

    # 这些用例只看 payload 字段；协议流式与本断言无关，钉掉以免 mock 响应被当 SSE 读。
    monkeypatch.setattr(_ap, "AGENT_PLAN_TEXT_STREAM", False, raising=False)
    monkeypatch.setattr(_ap, "AGENT_PLAN_TEXT_STREAM_AUTO", False, raising=False)

    resp = _ok_response('{"issues": []}')
    out = {}
    for effort, cap in pairs:
        with patch("requests.post", return_value=resp) as mock_post:
            svc.generate(TextGenerationRequest(
                system_prompt="s", user_prompt="u",
                model="deepseek-v4.1-flash",
                reasoning_effort=effort, max_tokens=cap))
        out[(effort, cap)] = _sent_payload(mock_post.call_args)
    return out


def test_effort_downgrade_is_wire_identical_but_max_tokens_is_not(monkeypatch):
    """**方舟端点**的既有结论（复核 2026-09-29）：判据在 serialized payload 层.

    方舟只有思考**开关**：provider 把 `low`/`minimal` 都序列化成
    `thinking.type=enabled`——**两份 HTTP payload 完全相同**。故「改变发出的请求」这类
    修复必须用 payload 层回归守住（审计的截断重采样因此改的是 `max_tokens`）。

    ⚠️ 这条**只对方舟成立**：OpenCode Go 端点是**档位**契约，档位会真的进 payload
    （见 `test_opencode_endpoint_sends_effort_tiers`）。
    """
    svc = _make_text_service(monkeypatch, ARK_URL)
    p = _payloads(svc, monkeypatch, [("low", 32000), ("minimal", 32000),
                                     ("low", 64000), ("none", 32000)])
    lo, mi, big, off = p[("low", 32000)], p[("minimal", 32000)], p[("low", 64000)], p[("none", 32000)]
    assert lo["thinking"] == mi["thinking"]                   # 方舟：档位改不动 payload
    assert lo["max_completion_tokens"] == 32000
    assert big["max_completion_tokens"] == 64000              # 上限**确实**进 payload
    assert big != lo
    assert off["thinking"] != lo["thinking"]                  # 关思考才是真降档


def test_opencode_endpoint_sends_effort_tiers(monkeypatch):
    """**现行文本通道（OpenCode Go）**：档位必须真的进 payload（复核 2026-10-03 的确定缺陷）.

    缺陷原状：切通道后仍按 Doubao 映射，`low`/`high`/`max` 序列化成同一份
    `thinking.type=enabled`，三者请求体 SHA-256 相同 ⇒ 审计配置的 `low` **没传出去**。
    契约依据：官方模型目录 `[[reasoning_options]] type="effort" values=["low","high","max"]`。
    """
    svc = _make_text_service(monkeypatch, OPENCODE_URL)
    p = _payloads(svc, monkeypatch,
                  [("low", 32000), ("high", 32000), ("max", 32000), ("none", 32000)])
    assert p[("low", 32000)]["reasoning_effort"] == "low"
    assert p[("high", 32000)]["reasoning_effort"] == "high"
    assert p[("max", 32000)]["reasoning_effort"] == "max"
    # 三个档位**两两不同**（旧写法三者逐字相同）
    assert p[("low", 32000)] != p[("high", 32000)] != p[("max", 32000)] != p[("low", 32000)]
    # `none`（契约：关闭思考）在该端点**没有对应档位** ⇒ 不发强度字段，请求与改动前**逐字相同**
    assert "reasoning_effort" not in p[("none", 32000)]
    # 旧字段**一律照旧发出**（它在该端点上是否被采纳未经证实；撤掉它是另一件事）
    for _k, _t in ((("low", 32000), "enabled"), (("high", 32000), "enabled"),
                   (("max", 32000), "enabled"), (("none", 32000), "disabled")):
        assert p[_k]["thinking"] == {"type": _t}, p[_k].get("thinking")


def test_opencode_dialect_downgrades_unregistered_tier(monkeypatch):
    """未登记档位取同侧最低档（`minimal` → `low`）；**完全不认识**的值不发并告警."""
    from _text_gen import agent_plan_provider as ap_mod
    assert ap_mod._openai_effort_tier("minimal") == "low"
    assert ap_mod._openai_effort_tier("weird") == ""
    assert ap_mod._openai_effort_tier("none") == ""
    assert ap_mod._openai_effort_tier("") == ""
    assert ap_mod._api_dialect(OPENCODE_URL) == "openai"
    assert ap_mod._api_dialect(ARK_URL) == "doubao"

# ─────────── 循环级：连接类次数 / 收口文案 / 429·5xx 各自收口（v6.152） ───────────
# 2026-09-29 五次跑实测：循环上限写成**无类别**的 `submit_attempts()`（= 2），于是
# 「连接类共 3 次」这条判据永远够不到第 3 次——第 2 次失败时 `should_retry` 仍判可重试，
# 退避一次后 `for` 已尽，落到尾部报「未知原因」（run 02：16.1s 内两次连接重置）。
# 下面几条钉在**真实 provider 循环**上：断言 POST 次数，即线上真实提交次数。


def _connect_reset():
    """run 02 现场形态：ConnectionResetError(10054) 包在 requests.ConnectionError 里."""
    import requests as _req
    return _req.exceptions.ConnectionError(
        "('Connection aborted.', ConnectionResetError(10054, '远程主机强迫关闭了一个现有的连接。'))"
    )


def test_connect_reset_gets_three_provider_attempts_then_succeeds(monkeypatch):
    """连接重置 ×2 后成功 ⇒ 必须发生 **3 次** POST（连接类的第 3 次真能跑到）."""
    svc = _make_text_service(monkeypatch)
    from _text_gen import models
    req = models.TextGenerationRequest(system_prompt="x", user_prompt="y")
    with patch("requests.post",
               side_effect=[_connect_reset(), _connect_reset(), _ok_response()]) as mock_post,          patch("time.sleep", return_value=None):
        result = svc.generate(req)
    assert result.success is True, result.error
    assert mock_post.call_count == 3, "连接类应共 3 次提交，实际 %d" % mock_post.call_count


def test_connect_reset_exhausted_reports_classified_reason(monkeypatch):
    """连接重置全失败 ⇒ 3 次后收口，文案带分类与底层异常，**不得**是「未知原因」."""
    svc = _make_text_service(monkeypatch)
    from _text_gen import models
    req = models.TextGenerationRequest(system_prompt="x", user_prompt="y")
    with patch("requests.post", side_effect=_connect_reset()) as mock_post,          patch("time.sleep", return_value=None):
        result = svc.generate(req)
    assert result.success is False
    assert mock_post.call_count == 3, mock_post.call_count
    assert "transport_connect" in result.error, result.error
    assert ("10054" in result.error) or ("ConnectionResetError" in result.error), result.error
    assert "未知原因" not in result.error, result.error


def test_connect_attempts_follow_env_override(monkeypatch):
    """连接类次数仍由策略单一来源决定：`TEXT_TRANSPORT_CONNECT_RETRIES=0` ⇒ 共 2 次."""
    from _text_gen import retry_policy as rp
    monkeypatch.setenv(rp.ENV_RETRIES_CONNECT, "0")
    svc = _make_text_service(monkeypatch)
    from _text_gen import models
    req = models.TextGenerationRequest(system_prompt="x", user_prompt="y")
    with patch("requests.post", side_effect=_connect_reset()) as mock_post,          patch("time.sleep", return_value=None):
        result = svc.generate(req)
    assert result.success is False
    assert mock_post.call_count == 2, mock_post.call_count


def test_429_exhaustion_still_capped_at_two(monkeypatch):
    """429 仍按**它自己**的次数收口（2 次），不因连接类放宽而跟着变."""
    svc = _make_text_service(monkeypatch)
    resp = MagicMock(status_code=429, text='{"error":"rate limited","retry_after":1}')
    from _text_gen import models
    req = models.TextGenerationRequest(system_prompt="x", user_prompt="y")
    with patch("requests.post", return_value=resp) as mock_post,          patch("time.sleep", return_value=None):
        result = svc.generate(req)
    assert result.success is False
    assert mock_post.call_count == 2, mock_post.call_count
    assert "429" in result.error, result.error


def test_5xx_exhaustion_still_capped_at_two_and_keeps_reason(monkeypatch):
    """5xx 同样只 2 次，且收口文案带上服务端原因（不再只说「未知原因」）."""
    svc = _make_text_service(monkeypatch)
    resp = MagicMock(status_code=503, text='{"error":"busy"}')
    from _text_gen import models
    req = models.TextGenerationRequest(system_prompt="x", user_prompt="y")
    with patch("requests.post", return_value=resp) as mock_post,          patch("time.sleep", return_value=None):
        result = svc.generate(req)
    assert result.success is False
    assert mock_post.call_count == 2, mock_post.call_count
    assert "503" in result.error, result.error


# ───────────────────── 网关会话头 `x-opencode-session`（2026-10-02）─────────────────────
# opencode go 网关**要求**该头（缺失直接 400 MissingSessionID，实测）；火山方舟不需要。
# 官方文档只要求"每次会话一个稳定 id"、不限格式，故未配置时自动生成**进程内稳定**值。


def test_text_session_header_auto_generated_when_unset(monkeypatch):
    """未配置 `AGENT_PLAN_SESSION_ID` ⇒ 头必须有值，且**进程内稳定**（两次调用同值）."""
    from _text_gen import agent_plan_provider as ap_mod

    monkeypatch.setattr(ap_mod.config, "AGENT_PLAN_SESSION_ID", "", raising=False)
    svc = _make_text_service(monkeypatch)
    _h1 = svc._headers["x-opencode-session"]
    _h2 = svc._headers["x-opencode-session"]
    assert _h1 and _h1 == _h2, (_h1, _h2)
    assert _h1 == ap_mod._AUTO_SESSION_ID


def test_text_session_header_follows_explicit_config(monkeypatch):
    """显式配置优先：配了就用配置值（网关/方舟都可自带路由 id）."""
    from _text_gen import agent_plan_provider as ap_mod

    monkeypatch.setattr(ap_mod.config, "AGENT_PLAN_SESSION_ID", "sess-explicit-1",
                        raising=False)
    svc = _make_text_service(monkeypatch)
    assert svc._headers["x-opencode-session"] == "sess-explicit-1"


def test_text_endpoint_is_text_specific_with_fallback(monkeypatch):
    """**文本专用端点**：配了用它；留空回落到共享的 `AGENT_PLAN_BASE_URL`（图片侧不受影响）."""
    from _text_gen import agent_plan_provider as ap_mod
    from _text_gen import config as tgcfg

    # ① 配了文本专用端点 ⇒ 只影响文本通道
    assert _make_text_service(
        monkeypatch, "https://text.example/api")._base_url == "https://text.example/api"
    # ② 留空 ⇒ 回落**共享端点**
    assert _make_text_service(
        monkeypatch, "", "http://fake")._base_url == "http://fake"
    # ③ 共享端点的**默认值**仍是方舟（图片通道依赖它）
    assert "plan/v3" in os.getenv(
        "AGENT_PLAN_BASE_URL", "https://ark.cn-beijing.volces.com/api/plan/v3")

# ───────────────── 传输阶段轨迹（复核 2026-10-03：区分"排队/在算/传输停顿"） ─────────────────
def _loopback(body_chunks, *, headers_delay=0.0, chunk_delay=0.0, with_headers=True,
              declare_extra=0, status=200):
    """本地回环 HTTP 服务（**不出网**）：按需在响应头/正文之间制造停顿."""
    import http.server
    import socketserver
    import threading
    import time as _time

    class _H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):                                   # noqa: N802
            _n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(_n)
            if headers_delay:
                _time.sleep(headers_delay)
            if not with_headers:
                return                                        # 连接已建立、**一个字节都不回**
            _total = sum(len(c) for c in body_chunks) + int(declare_extra)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("x-request-id", "req-trace-1")
            self.send_header("Content-Length", str(_total))
            self.end_headers()
            for _c in body_chunks:
                self.wfile.write(_c)
                self.wfile.flush()
                if chunk_delay:
                    _time.sleep(chunk_delay)

        def log_message(self, *a):                           # 静音
            pass

    _srv = socketserver.TCPServer(("127.0.0.1", 0), _H)
    _srv.daemon_threads = True
    threading.Thread(target=_srv.serve_forever, daemon=True).start()
    return _srv, "http://127.0.0.1:%d" % _srv.server_address[1]


def test_trace_records_stage_timings_on_a_streaming_response(monkeypatch):
    """正文分片到达 ⇒ 轨迹记下 响应头/首字节/字节数/关联 ID（后续单变量对照的基线）."""
    from _text_gen import agent_plan_provider as ap_mod
    _lines: list = []
    monkeypatch.setattr(ap_mod, "_verbose_write", lambda s: _lines.append(str(s)))
    svc = _make_text_service(monkeypatch, ARK_URL)
    _body = b'{"choices":[{"message":{"content":"x"},"finish_reason":"stop"}]}'
    _srv, url = _loopback([_body[:20], _body[20:]], chunk_delay=0.05)
    try:
        svc._base_url = url
        res = svc.generate(_TextReq(system_prompt="s", user_prompt="u"))
    finally:
        _srv.shutdown()
    assert res.success is True, res.error
    _trace = [x for x in _lines if "[TRACE]" in x and "阶段轨迹" in x]
    assert len(_trace) == 1, _lines
    assert "响应头" in _trace[0] and "正文 %d 字节" % len(_body) in _trace[0], _trace[0]
    assert "首字节" not in _trace[0], "测不准的量不许出现在证据里：" + _trace[0]
    assert "关联ID=req-trace-1" in _trace[0], _trace[0]
    assert "正文 %d 字节" % len(_body) in _trace[0], _trace[0]
    # 第二段：响应体自报的思考用量（判断"没发强度字段时网关给的是哪一档"的现场证据）
    _usage = [x for x in _lines if "[TRACE]" in x and "思维链" in x]
    assert len(_usage) == 1, _lines


def test_trace_note_distinguishes_the_three_stages():
    """阶段文案三分（**确定性**，不看时序）：未发出 / 发出了但响应头未到 / 响应头已到.

    这正是复核 2026-10-03 要的证据：现有超时文案只能证明"没拿到完整响应"，
    无法区分"网关排队"与"远端在算"。
    """
    from _text_gen import agent_plan_provider as ap_mod
    _payload = {"model": "m", "reasoning_effort": "low"}

    _no_send = ap_mod.AgentPlanTextService._trace_note({}, _payload, "low", sent=False)
    assert "未发出" in _no_send, _no_send

    _no_hdr = ap_mod.AgentPlanTextService._trace_note({}, _payload, "low", sent=True)
    assert "无任何响应头/字节" in _no_hdr, _no_hdr
    assert "排队与在算不可分" in _no_hdr, _no_hdr      # 不许断言是网关排队（实测过不了）
    assert "强度=low" in _no_hdr and "指纹=" in _no_hdr, _no_hdr

    _partial = ap_mod.AgentPlanTextService._trace_note(
        {"headers_at": 0.12, "bytes": 2048, "done_at": 3.1, "request_id": "req-1"},
        _payload, "low", sent=True)
    assert "响应头 0.12s" in _partial, _partial
    assert "正文 2048 字节" in _partial and "收完 3.1" in _partial, _partial
    assert "关联ID=req-1" in _partial, _partial


def test_stalled_body_is_a_failure_not_a_truncated_success(monkeypatch):
    """正文停在中途（声明长度大于实发）⇒ **必须是失败**，不许把半截正文当成功下发.

    （回归：`iter_content` 期间的传输异常如果被追踪代码吞掉，`{"choices":` 这种半截
    正文会被交给 `.json()`，报成"响应不是 JSON"——把传输故障误报成内容故障。）
    """
    svc = _make_text_service(monkeypatch, ARK_URL)
    _srv, url = _loopback([b'{"choices":'], chunk_delay=3.0, declare_extra=500)
    try:
        svc._base_url = url
        res = svc.generate(_TextReq(system_prompt="s", user_prompt="u", timeout_sec=1.0))
    finally:
        _srv.shutdown()
    assert res.success is False, res.text
    _e = res.error or ""
    assert ("墙钟上限" in _e) or ("客户端超时" in _e), _e

def test_401_with_empty_body_returns_auth_error_through_the_real_entry(monkeypatch):
    """**复核 2026-10-03 P1**：401 + **空正文**必须走鉴权失败出口，不许抛异常.

    缺陷原状：只在缓冲非空时回填 `_content` ⇒ `_content_consumed=True` 但 `_content=False`
    ⇒ 真实入口读 `resp.text` 时抛 `RuntimeError: content ... already consumed`，
    把鉴权失败**遮蔽**成解析崩溃。桩必须经**真实 `generate()`**（不是直接调 `_send_once`）。
    """
    svc = _make_text_service(monkeypatch, ARK_URL)
    _srv, url = _loopback([], status=401)
    try:
        svc._base_url = url
        res = svc.generate(_TextReq(system_prompt="s", user_prompt="u"))
    finally:
        _srv.shutdown()
    assert res.success is False
    _e = res.error or ""
    assert "already consumed" not in _e, _e
    assert "401" in _e or "鉴权" in _e or "Key" in _e, _e


def test_small_prefix_then_pause_still_reads_the_whole_body(monkeypatch):
    """**复核 2026-10-03 P2 的收尾**：不做"首字节"读数（测不准），但**正文必须读全**.

    反例场景：11 字节先到、余下正文 0.6s 后才到 —— 旧实现（`chunk_size=65536`）会在
    "前缀已到"时仍显示正文未开始（读数不可信）；而换读取方式的三种改法都**改变了被观测
    对象**（分块编码下截断成 1 字节 / 等到 EOF / 慢到不可用）。所以这里钉住的是**正确性**：
    无论快慢，读回来的字节必须等于服务端实发字节。
    """
    svc = _make_text_service(monkeypatch, ARK_URL)
    _head, _tail = b'{"choices":', b'[]}'
    _srv, url = _loopback([_head, _tail], chunk_delay=0.6)
    try:
        _tr: dict = {}
        resp = svc._send_once(url, {"model": "m"}, 5.0, _tr)
    finally:
        _srv.shutdown()
    assert _tr["bytes"] == len(_head) + len(_tail), _tr
    assert resp.content == _head + _tail
    assert "first_byte_at" not in _tr          # 字段已删：测不准的量不许出现在证据里


def test_chunked_transfer_encoding_body_is_not_truncated(monkeypatch):
    """**分块传输编码**下正文不许被截断（仪表回归：两段读曾把 150041 字节读成 1 字节）."""
    import http.server
    import socketserver
    import threading

    _p1 = b'{"choices":'
    _p2 = (b'"x' * 70000) + b'"}'
    _full = _p1 + _p2

    class _H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):                                   # noqa: N802
            _n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(_n)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            for _part in (_p1, _p2):
                _hdr = ("%x" % len(_part)).encode()
                self.wfile.write(_hdr + bytes((13, 10)) + _part + bytes((13, 10)))
                self.wfile.flush()
            self.wfile.write(b"0" + bytes((13, 10)) + bytes((13, 10)))
            self.wfile.flush()
            self.close_connection = True                     # 免得 shutdown 卡在 keep-alive

        def log_message(self, *a):
            pass

    _srv = socketserver.TCPServer(("127.0.0.1", 0), _H)
    _srv.daemon_threads = True
    threading.Thread(target=_srv.serve_forever, daemon=True).start()
    svc = _make_text_service(monkeypatch, ARK_URL)
    try:
        svc._base_url = "http://127.0.0.1:%d" % _srv.server_address[1]
        _tr: dict = {}
        resp = svc._send_once(svc._base_url + "/chat/completions", {"model": "m"}, 20.0, _tr)
    finally:
        _srv.shutdown()
    assert _tr["bytes"] == len(_full), _tr
    assert len(resp.content) == len(_full), (len(resp.content), len(_full))
