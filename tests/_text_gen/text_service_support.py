# -*- coding: utf-8 -*-
"""受控测试支持：构造最小可用的 AgentPlan 文本服务与 200 响应桩.

只被测试使用，不含生产逻辑；离线、零网络（调用方仍必须 patch `requests.post`）。
"""
from __future__ import annotations

from unittest.mock import MagicMock

#: 两个端点的**请求方言**：端点即契约，测试必须显式钉住自己测的是哪一个。
ARK_URL = "https://ark.cn-beijing.volces.com/api/plan/v3"
OPENCODE_URL = "https://opencode.ai/inference/go/openai/v1"


def make_text_service(monkeypatch, base_url: str = ARK_URL,
                      shared_base_url: str = ARK_URL):
    """构造一个最小可用的 AgentPlanTextService（注入假 key + 显式端点）."""
    import importlib

    from _text_gen import agent_plan_provider as ap_mod
    cfg_mod = importlib.import_module("_text_gen.config")
    tgcfg = cfg_mod.config
    # `validate()` 是 classmethod，读的是类属性；只给实例/模块属性会在没有
    # `.env.keys` 的干净检出里失败（S04 隔离副本实测）。
    monkeypatch.setattr(cfg_mod.TextGenCfg, "AGENT_PLAN_API_KEY", "test-key")
    monkeypatch.setattr(tgcfg, "AGENT_PLAN_API_KEY", "test-key")
    monkeypatch.setattr(ap_mod.config, "AGENT_PLAN_API_KEY", "test-key")
    monkeypatch.setattr(ap_mod.config, "AGENT_PLAN_BASE_URL", shared_base_url)
    monkeypatch.setattr(ap_mod.config, "AGENT_PLAN_TEXT_BASE_URL", base_url)
    monkeypatch.setattr(ap_mod.config, "AGENT_PLAN_TEXT_MODEL", "doubao-seed-2-1-turbo")
    # 60 RPM → 1s cooldown，acquire 几乎不阻塞
    monkeypatch.setattr(ap_mod.config, "AGENT_PLAN_TEXT_RPM", 60)
    return ap_mod.AgentPlanTextService()


def ok_response(content: str = "分镜脚本内容", usage: dict | None = None) -> MagicMock:
    """构造一个 200 chat completions 响应 mock."""
    body = {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": usage or {"prompt_tokens": 100, "completion_tokens": 200,
                           "total_tokens": 300},
    }
    resp = MagicMock(status_code=200, text=str(body)[:200])
    resp.json.return_value = body
    return resp
