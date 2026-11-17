# -*- coding: utf-8 -*-
"""S02-C3：有声模型 payload 对照（探索结果固定为回归断言）.

先跑两组完整链（``_build_prompt`` → ``_make_request`` → provider
``prepare_request``），只切 ``MERGE_AUDIO_MODE``；观测结果是：
- prompt / parameters / materials **逐字相同**；
- 有效策略与 ``requirements.audio_policy`` 不同（native vs tts_mix）；
- ``request_key`` 不同，差异来自后处理声音合同，不是视频模型输入。

因此本文件把「payload 相同、策略与 key 不同」固定为回归预期；若未来 provider
开始按混音模式改 payload，这里会先失败，要求重新给出解释，而不是默默放过。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from _video_gen import create_service
from shared.audio_policy import resolve_audio_policy
from step02_video_gen.execution_requirements import build_requirements, merge_into_frozen
from step02_video_gen.prompt_builder import _build_prompt, _make_request
from step02_video_gen.request_plan import (
    _requirements_for_key, build_frozen_request,
)
from step02_video_gen.submit_poll import generation_requirements

SCOPE = {"chapter": 1, "book_index": 1, "book_name": ""}
VOICED = "doubao-seedance-1-5-pro-251215"
SILENT = "doubao-seedance-1-0-pro-250528"


def _shot() -> dict:
    return {"index": 1, "prompt": "[中景] 甲站在船头", "ff_desc": "甲站在船头",
            "lf_desc": "甲转身离开", "motion_desc": "甲转身", "duration_sec": 5.0}


def _script(shot: dict) -> dict:
    return {"characters": [], "shots": [shot]}


@pytest.fixture()
def frames(tmp_path):
    d = tmp_path / "frames"
    d.mkdir()
    ff, lf = d / "first_frame.png", d / "last_frame.png"
    Image.new("RGB", (64, 64), (12, 24, 36)).save(ff)
    Image.new("RGB", (64, 64), (200, 30, 40)).save(lf)
    return [str(ff), str(lf)]


def _env(monkeypatch, tmp_path, model: str):
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path / "out"))
    monkeypatch.setenv("NOVEL_CHAPTER", "1")
    monkeypatch.setenv("BOOK_INDEX", "1")
    monkeypatch.setenv("BOOK_NAME", "")
    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    monkeypatch.setenv("VIDEO_MODEL_VOICE", "1")
    monkeypatch.setenv("VOLCENGINE_I2V_MODEL", model)


def _run(shot: dict, script: dict, frame_paths: list, mode: str):
    prompt = _build_prompt(shot, script, mode="A")
    req = _make_request(None, prompt, frame_paths, shot, characters=[], mode="A")
    prepared = create_service("volcengine").prepare_request(req)
    reqs = build_requirements(shot, script, visual_plan=None,
                              frame_paths=frame_paths, reference_mode="image",
                              model_id=prepared.model_id)
    frozen = build_frozen_request(
        req, shot=shot, scope=SCOPE, script_revision="rev",
        provider=prepared.provider, model_id=prepared.model_id,
        capability_revision=prepared.capability_revision,
        requirements=generation_requirements(), prepared=prepared)
    merge_into_frozen(frozen, reqs)
    policy = resolve_audio_policy(narration_present=True)
    return prepared, frozen, policy


def test_voiced_payload_same_but_policy_and_key_differ(tmp_path, monkeypatch, frames):
    _env(monkeypatch, tmp_path, VOICED)
    shot, script = _shot(), _script(_shot())

    monkeypatch.setenv("MERGE_AUDIO_MODE", "native")
    p_native, f_native, pol_native = _run(shot, script, frames, "native")
    monkeypatch.setenv("MERGE_AUDIO_MODE", "tts_mix")
    p_tts, f_tts, pol_tts = _run(shot, script, frames, "tts_mix")

    # 视频模型输入逐字相同（这是本轮要确定的事实，不是先验假设）。
    assert p_native.prompt == p_tts.prompt
    assert p_native.parameters == p_tts.parameters
    assert ([m.key_facts() for m in p_native.materials]
            == [m.key_facts() for m in p_tts.materials])

    # 后处理声音合同不同 ⇒ key 不同；差异必须能由合同字段解释。
    assert pol_native.effective_merge_mode == "native"
    assert pol_tts.effective_merge_mode == "tts_mix"
    assert generation_requirements() == {"audio_policy": "tts_mix"}
    assert f_native.request_key != f_tts.request_key

    native_key_facts = _requirements_for_key(f_native.requirements)
    tts_key_facts = _requirements_for_key(f_tts.requirements)
    assert "reason" not in native_key_facts["execution"]["audio"]["policy"]
    assert "reason" not in tts_key_facts["execution"]["audio"]["policy"]
    assert native_key_facts["execution"]["audio"]["policy"]["effective_merge_mode"] == "native"
    assert tts_key_facts["execution"]["audio"]["policy"]["effective_merge_mode"] == "tts_mix"
    # 冻结记录仍保留完整 reason（可审计）。
    assert f_native.requirements["execution"]["audio"]["policy"]["reason"]
    assert f_tts.requirements["execution"]["audio"]["policy"]["reason"]


def test_silent_payload_same_and_key_stable(tmp_path, monkeypatch, frames):
    _env(monkeypatch, tmp_path, SILENT)
    shot, script = _shot(), _script(_shot())

    monkeypatch.setenv("MERGE_AUDIO_MODE", "native")
    p_native, f_native, pol_native = _run(shot, script, frames, "native")
    monkeypatch.setenv("MERGE_AUDIO_MODE", "tts_mix")
    p_tts, f_tts, pol_tts = _run(shot, script, frames, "tts_mix")

    assert p_native.prompt == p_tts.prompt
    assert p_native.parameters == p_tts.parameters
    assert ([m.key_facts() for m in p_native.materials]
            == [m.key_facts() for m in p_tts.materials])
    assert pol_native.effective_merge_mode == "tts_mix"
    assert pol_tts.effective_merge_mode == "tts_mix"
    assert generation_requirements() == {}
    assert f_native.request_key == f_tts.request_key
