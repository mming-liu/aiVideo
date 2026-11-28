# -*- coding: utf-8 -*-
"""D：**实发的结构化输出格式**必须可核（复核 2026-10-06 第八轮）.

「请求被接受 / schema 被遵守 / 候选通过验收」是**三件事**，不能混成一个"成功"。
本文件钉住中间那一项的事实依据：本通道恒发 `json_object`，调用方给的 `json_schema`
**不下发**——所以不得据此声称响应受 strict 约束。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from _text_gen import agent_plan_provider as ap                             # noqa: E402
from _text_gen import models as tm                                          # noqa: E402


def _payload(**over):
    _req = tm.TextGenerationRequest(system_prompt="s", user_prompt="u",
                                    temperature=0.3, **over)
    _svc = ap.AgentPlanTextService.__new__(ap.AgentPlanTextService)
    _svc._base_url = "https://example.invalid/v1"
    _svc._model = "m"
    return _svc._build_payload(_req)


def test_the_sent_format_is_readable_and_never_claims_more_than_it_sent():
    assert ap.sent_structured_output({}) == "（未发）"
    assert ap.sent_structured_output({"response_format": {"type": "json_object"}}) \
        == "json_object"
    _js = {"name": "plan_revision_delta_v1", "strict": True,
           "schema": {"type": "object"}}
    assert ap.sent_structured_output(
        {"response_format": {"type": "json_schema", "json_schema": _js}}) \
        == "json_schema:plan_revision_delta_v1"


def test_a_caller_supplied_schema_is_not_silently_claimed_as_enforced():
    """调用方给了 `json_schema`，本通道**实发**的仍是 `json_object`——这一事实必须可读.

    形状的兜底在**解析端**（结构化拒收），不在服务端；凭"我传了 schema"推断"响应受
    strict 约束"是错的（复核 2026-10-06 更正③）。
    """
    _js = {"name": "plan_revision_delta_v1", "strict": True,
           "schema": {"type": "object"}}
    _p = _payload(json_schema=_js)
    assert _p["response_format"] == {"type": "json_object"}
    assert ap.sent_structured_output(_p) != "json_schema:plan_revision_delta_v1"
