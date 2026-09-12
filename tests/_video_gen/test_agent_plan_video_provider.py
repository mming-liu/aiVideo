"""agent_plan 视频 provider 的 draft/service_tier 透传测试（2026-09-12）.

移植自官方 byted-ark-seedance-skill：payload 顶层 draft（布尔）与
service_tier（default/flex）字段，仅能力开启的模型透传（1.5 pro 支持，
2.5 官方矩阵明确不支持）。能力位单源 shared/model_caps.SeedanceCap。
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class _FakeResp:
    status_code = 200
    text = '{"id": "cgt-test"}'

    def json(self):
        return {"id": "cgt-test"}


def _make_service(model: str):
    from _video_gen.agent_plan_provider import AgentPlanVideoService
    svc = AgentPlanVideoService()
    svc._model = model  # 测试用直接指定，绕开 .env 全局配置
    return svc


def _capture_post(monkeypatch):
    captured = []

    def _fake_post(url, **kwargs):
        captured.append(kwargs)
        return _FakeResp()

    monkeypatch.setattr("requests.post", _fake_post)
    return captured


def _req():
    from _video_gen.models import VideoGenerationRequest
    return VideoGenerationRequest(prompt="测试", clip_index=1, duration=5)


# ---------------------------------------------------------------------------
# 能力位：官方矩阵 → SeedanceCap
# ---------------------------------------------------------------------------

def test_cap_flags_from_official_matrix():
    from shared.model_caps import get_seedance_cap
    assert get_seedance_cap("doubao-seedance-1.5-pro").supports_draft is True
    assert get_seedance_cap("doubao-seedance-1.5-pro").supports_flex_tier is True
    # 2.5 官方矩阵明确不支持 draft/flex
    assert get_seedance_cap("doubao-seedance-2.5").supports_draft is False
    assert get_seedance_cap("doubao-seedance-2.5").supports_flex_tier is False
    assert get_seedance_cap("doubao-seedance-2.0").supports_draft is False


# ---------------------------------------------------------------------------
# payload 透传：能力开启 + 环境变量 → 字段出现；否则不出现
# ---------------------------------------------------------------------------

def test_draft_passed_when_enabled_and_supported(monkeypatch):
    svc = _make_service("doubao-seedance-1.5-pro")
    captured = _capture_post(monkeypatch)
    monkeypatch.setenv("AGENT_PLAN_VIDEO_DRAFT", "1")
    svc.create_task(_req())
    assert captured and captured[0]["json"]["draft"] is True


def test_draft_absent_by_default(monkeypatch):
    svc = _make_service("doubao-seedance-1.5-pro")
    captured = _capture_post(monkeypatch)
    monkeypatch.delenv("AGENT_PLAN_VIDEO_DRAFT", raising=False)
    svc.create_task(_req())
    assert captured and "draft" not in captured[0]["json"]


def test_service_tier_flex_passed_when_supported(monkeypatch):
    svc = _make_service("doubao-seedance-1.5-pro")
    captured = _capture_post(monkeypatch)
    monkeypatch.setenv("AGENT_PLAN_VIDEO_SERVICE_TIER", "flex")
    svc.create_task(_req())
    assert captured and captured[0]["json"]["service_tier"] == "flex"


def test_service_tier_skipped_on_unsupported_model(monkeypatch):
    """2.0 能力位关闭 → 即使 env 开着也不透传（防把不支持的字段发给服务端）."""
    svc = _make_service("doubao-seedance-2.0")
    captured = _capture_post(monkeypatch)
    monkeypatch.setenv("AGENT_PLAN_VIDEO_SERVICE_TIER", "flex")
    monkeypatch.setenv("AGENT_PLAN_VIDEO_DRAFT", "1")
    svc.create_task(_req())
    payload = captured[0]["json"]
    assert "service_tier" not in payload and "draft" not in payload
