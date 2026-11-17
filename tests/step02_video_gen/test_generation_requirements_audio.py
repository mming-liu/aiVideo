# -*- coding: utf-8 -*-
"""S02：`generation_requirements()` 的判据层级（2026-11-17）.

要证明的：
  1. **无声模型**（volcengine + seedance-1-0-pro）下，`MERGE_AUDIO_MODE`
     不再进入视频生成 key —— 该模型没有原生音轨可混，切换它不可能改变输入，
     却会因为 `requirements` 整体进 `request_key` 而白白失效缓存；
  2. **有声模型**（volcengine + seedance-1-5-pro）下行为保持不变（仍进 key）；
  3. 非原生发声通道（bailian）行为保持不变（不进 key）；
  4. `request_key` 的实际差异与 `requirements` 一致（key 含 requirements）。
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def volcengine(monkeypatch):
    from _video_gen import config  # noqa: F401  触发 .env.content 加载
    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    return monkeypatch


def _reqs() -> dict:
    from step02_video_gen.submit_poll import generation_requirements

    return generation_requirements()


def test_silent_model_ignores_merge_audio_mode(volcengine):
    volcengine.setenv("VOLCENGINE_I2V_MODEL", "doubao-seedance-1-0-pro-250528")
    volcengine.setenv("MERGE_AUDIO_MODE", "native")
    assert _reqs() == {}
    volcengine.setenv("MERGE_AUDIO_MODE", "tts_mix")
    assert _reqs() == {}, "无声模型下切换混音模式不得改变视频生成 key"


def test_audio_capable_model_keeps_mode_in_key(volcengine):
    volcengine.setenv("VOLCENGINE_I2V_MODEL", "doubao-seedance-1-5-pro-251215")
    volcengine.setenv("MERGE_AUDIO_MODE", "native")
    assert _reqs() == {"audio_policy": "native"}
    volcengine.setenv("MERGE_AUDIO_MODE", "tts_mix")
    assert _reqs() == {"audio_policy": "tts_mix"}


def test_non_native_provider_unchanged(volcengine):
    volcengine.setenv("VIDEO_PROVIDER", "bailian")
    volcengine.setenv("MERGE_AUDIO_MODE", "native")
    assert _reqs() == {}, "非原生发声通道本就不进 key（既有行为）"


def test_request_key_contains_requirements(volcengine):
    """`requirements` 确实进 `request_key`——所以上面两条不是纸面差异."""
    from step02_video_gen.request_plan import FrozenVideoRequest

    volcengine.setenv("VOLCENGINE_I2V_MODEL", "doubao-seedance-1-5-pro-251215")
    base = dict(request_id="req-test", schema_version="video_request.v1", provider="volcengine",
                model_id="m", mode="image", prompt="p", parameters={},
                references=[], source={"scope": {"chapter": 1},
                                       "script_revision": "r", "shot_id": "shot-1"})
    volcengine.setenv("MERGE_AUDIO_MODE", "native")
    a = FrozenVideoRequest(requirements=_reqs(), **base).compute_key()
    volcengine.setenv("MERGE_AUDIO_MODE", "tts_mix")
    b = FrozenVideoRequest(requirements=_reqs(), **base).compute_key()
    assert a != b

    # 无声模型：同样切换，key 必须一致
    volcengine.setenv("VOLCENGINE_I2V_MODEL", "doubao-seedance-1-0-pro-250528")
    volcengine.setenv("MERGE_AUDIO_MODE", "native")
    c = FrozenVideoRequest(requirements=_reqs(), **base).compute_key()
    volcengine.setenv("MERGE_AUDIO_MODE", "tts_mix")
    d = FrozenVideoRequest(requirements=_reqs(), **base).compute_key()
    assert c == d


