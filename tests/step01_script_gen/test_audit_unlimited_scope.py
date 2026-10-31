"""`budget.unlimited` 的作用域（P1，2026-10-29 复核）.

免逐次授权只对**已验证的订阅制通道**（OpenCode Go：provider / 模型 / 端点三者都对）生效；
仅凭 provider 名字不能认定套餐，其他通道继续走原一次性授权规则。
"""
from __future__ import annotations

import copy

import pytest

import tools.audit_frozen_candidate as entry
from _text_gen import config as cfg

_BASE = {
    "audit_channel": {"provider": "agent_plan", "model": "deepseek-v4.1-flash"},
    "budget": {
        "authorized_total": 13, "used": 12, "remaining": 0, "unlimited": True,
        "new_authorization": {"authorization_id": "", "binding_digest": ""},
    },
}


@pytest.fixture(autouse=True)
def _pinned_subscription(monkeypatch):
    monkeypatch.setattr(cfg, "AGENT_PLAN_TEXT_MODEL", "deepseek-v4.1-flash", raising=False)
    monkeypatch.setattr(cfg, "AGENT_PLAN_TEXT_BASE_URL",
                        "https://opencode.ai/zen/go/v1", raising=False)


def test_unlimited_active_on_verified_subscription_channel():
    pkg = copy.deepcopy(_BASE)
    assert entry._subscription_problem(pkg) == ""
    assert entry._unlimited_active(pkg) is True
    assert entry._validate_budget(pkg, require_authorization=True) == ""


def test_unlimited_ignored_for_other_provider():
    pkg = copy.deepcopy(_BASE)
    pkg["audit_channel"] = {"provider": "deepseek", "model": "deepseek-flash"}
    assert entry._unlimited_active(pkg) is False
    err = entry._validate_budget(pkg, require_authorization=True)
    assert err and "budget.unlimited 未生效" in err, err


def test_unlimited_ignored_when_model_not_the_package_model():
    pkg = copy.deepcopy(_BASE)
    pkg["audit_channel"]["model"] = "deepseek-flash"
    assert entry._unlimited_active(pkg) is False
    err = entry._validate_budget(pkg, require_authorization=True)
    assert "model" in err and "budget.unlimited 未生效" in err, err


def test_unlimited_ignored_when_endpoint_is_not_subscription(monkeypatch):
    monkeypatch.setattr(cfg, "AGENT_PLAN_TEXT_BASE_URL", "https://api.deepseek.com",
                        raising=False)
    pkg = copy.deepcopy(_BASE)
    assert entry._unlimited_active(pkg) is False
    err = entry._validate_budget(pkg, require_authorization=True)
    assert "endpoint" in err and "budget.unlimited 未生效" in err, err


def test_binding_covers_resolved_endpoint(monkeypatch):
    pkg = copy.deepcopy(_BASE)
    base = entry._authorization_binding(pkg, revision="r")
    monkeypatch.setattr(cfg, "AGENT_PLAN_TEXT_BASE_URL",
                        "https://opencode.ai/zen/go/v1/other", raising=False)
    moved = entry._authorization_binding(pkg, revision="r")
    assert base["endpoint"] != moved["endpoint"], "换端点必须改变绑定摘要的输入"


def test_ambiguous_endpoint_is_rejected(monkeypatch):
    """F3：/zen/go/../../zen/v1 这类歧义路径不能通过套餐核验。"""
    from _text_gen.agent_plan_provider import normalize_endpoint
    with pytest.raises(ValueError):
        normalize_endpoint("https://opencode.ai/zen/go/../../zen/v1")
    monkeypatch.setattr(cfg, "AGENT_PLAN_TEXT_BASE_URL",
                        "https://opencode.ai/zen/go/../../zen/v1", raising=False)
    pkg = copy.deepcopy(_BASE)
    assert entry._unlimited_active(pkg) is False
    err = entry._validate_budget(pkg, require_authorization=True)
    assert "endpoint" in err and "budget.unlimited 未生效" in err, err


def test_double_encoded_traversal_is_rejected(monkeypatch):
    """F3 二轮：%252e%252e 只能解出 %2e%2e，requests 还会再解一次 ⇒ 必须拒绝。"""
    from _text_gen.agent_plan_provider import chat_completions_url, normalize_endpoint
    with pytest.raises(ValueError):
        normalize_endpoint("https://opencode.ai/zen/go/%252e%252e/%252e%252e/zen/v1")
    with pytest.raises(ValueError):
        chat_completions_url("https://opencode.ai/zen/go/%252e%252e/zen/v1")
    monkeypatch.setattr(cfg, "AGENT_PLAN_TEXT_BASE_URL",
                        "https://opencode.ai/zen/go/%252e%252e/%252e%252e/zen/v1",
                        raising=False)
    pkg = copy.deepcopy(_BASE)
    assert entry._unlimited_active(pkg) is False


def test_subscription_requires_exact_path(monkeypatch):
    """豁免只认最终 URL 的精确 path；/zen/go/v1/extra 不得算套餐端点。"""
    from _text_gen.agent_plan_provider import is_subscription_endpoint
    assert is_subscription_endpoint("https://opencode.ai/zen/go/v1") is True
    assert is_subscription_endpoint("https://opencode.ai/zen/go/v1/") is True
    assert is_subscription_endpoint("https://opencode.ai/zen/go/v1/extra") is False
    assert is_subscription_endpoint("https://opencode.ai/zen/go/v2") is False
    assert is_subscription_endpoint("https://evil.example/zen/go/v1") is False


def test_subscription_requires_https_and_port():
    """F3 四轮：豁免 = HTTPS + 443 + 无用户名/密码 + 精确路径。"""
    from _text_gen.agent_plan_provider import is_subscription_endpoint
    assert is_subscription_endpoint("https://opencode.ai/zen/go/v1") is True
    assert is_subscription_endpoint("https://opencode.ai:443/zen/go/v1") is True
    assert is_subscription_endpoint("http://opencode.ai/zen/go/v1") is False
    assert is_subscription_endpoint("https://opencode.ai:8443/zen/go/v1") is False
    assert is_subscription_endpoint("https://user:pw@opencode.ai/zen/go/v1") is False
    # 空 userinfo（username/password 为空串而非 None）同样不得豁免
    assert is_subscription_endpoint("https://@opencode.ai/zen/go/v1") is False
    assert is_subscription_endpoint("https://:@opencode.ai/zen/go/v1") is False
