# -*- coding: utf-8 -*-
"""S02 补正：完整执行要求合并后，缓存身份必须稳定（2026-11-17）.

用户回放指出的缺口：`merge_into_frozen()` 把**完整执行要求**（含声音合同的诊断
文本 `execution.audio.policy.reason`）并入 `requirements`，而 `requirements` 整体
进 `request_key`。无声模型下切换 `MERGE_AUDIO_MODE`，有效策略不变、只有文案变，
key 却变了 ⇒ 精确缓存判 `mismatch`、本可复用的视频被重生成。

本文件走**生产路径**（`_audio_facts()` → `merge_into_frozen()` → `compute_key()`），
不手搓只含顶层 `_reqs()` 的请求。
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def volcengine(monkeypatch):
    from _video_gen import config  # noqa: F401  触发 .env.content 加载
    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    return monkeypatch


_SCRIPT = {"chapter_outline": {"narration_lines": ["旁白一句。"]}}
_SHOT = {"index": 1}


def _frozen():
    from step02_video_gen.request_plan import FrozenVideoRequest

    return FrozenVideoRequest(
        request_id="req-s02", schema_version="video_request.v1",
        provider="volcengine", model_id="m", mode="image", prompt="p",
        parameters={"duration": 5}, references=[],
        source={"scope": {"chapter": 1}, "script_revision": "r", "shot_id": "shot-1"})


def _requirements() -> dict:
    """真实生产者：`_audio_facts()` 的输出即 `requirements.audio`."""
    from step02_video_gen.execution_requirements import _audio_facts

    return {"schema": "video_execution_requirements.v1",
            "audio": _audio_facts(_SCRIPT, _SHOT)}


def _merge_key() -> str:
    from step02_video_gen.execution_requirements import merge_into_frozen

    frozen = _frozen()
    merge_into_frozen(frozen, _requirements())
    return frozen.request_key


def test_silent_model_key_stable_across_merge_audio_mode(volcengine):
    volcengine.setenv("VOLCENGINE_I2V_MODEL", "doubao-seedance-1-0-pro-250528")
    volcengine.setenv("MERGE_AUDIO_MODE", "native")
    native_key = _merge_key()
    volcengine.setenv("MERGE_AUDIO_MODE", "tts_mix")
    tts_key = _merge_key()
    assert native_key == tts_key, "无声模型下合并执行要求后，key 不得随混音模式变化"

    # 诊断文本确实被剔出 key，但**冻结记录里仍完整保留**（可审计）
    frozen = _frozen()
    from step02_video_gen.execution_requirements import merge_into_frozen

    merge_into_frozen(frozen, _requirements())
    assert frozen.requirements["execution"]["audio"]["policy"]["reason"], \
        "reason 必须仍留在冻结记录里，只是不进 key"


def test_audio_capable_model_still_reflects_effective_policy(volcengine):
    volcengine.setenv("VOLCENGINE_I2V_MODEL", "doubao-seedance-1-5-pro-251215")
    # .env.content 默认 VIDEO_MODEL_VOICE=0（当前主模型静音）；本用例验证
    # “有声模型”分支，必须显式打开，避免受项目配置默认值影响。
    volcengine.setenv("VIDEO_MODEL_VOICE", "1")
    volcengine.setenv("MERGE_AUDIO_MODE", "native")
    a = _merge_key()
    volcengine.setenv("MERGE_AUDIO_MODE", "tts_mix")
    b = _merge_key()
    assert a != b, "有声模型的有效混音方式不同，key 必须不同（保守行为不变）"


def test_other_reason_fields_still_enter_key():
    """只剔 `execution.audio.policy.reason`；其它同名 reason 必须照常参与身份."""
    from step02_video_gen.execution_requirements import merge_into_frozen

    base = {"schema": "video_execution_requirements.v1",
            "audio": {"policy": {"reason": "诊断文本"}},
            "other": {"reason": "A"}}
    f1 = _frozen()
    merge_into_frozen(f1, dict(base))
    k1 = f1.request_key
    base["other"]["reason"] = "B"
    f2 = _frozen()
    merge_into_frozen(f2, dict(base))
    assert f1.request_key != f2.request_key


def test_prompt_change_still_changes_key():
    """非诊断字段（prompt）变化必须继续改变 key——修复不得放宽身份."""
    from step02_video_gen.execution_requirements import merge_into_frozen

    f1 = _frozen()
    merge_into_frozen(f1, _requirements())
    f2 = _frozen()
    f2.prompt = "p2"
    merge_into_frozen(f2, _requirements())
    assert f1.request_key != f2.request_key
