# -*- coding: utf-8 -*-
"""KF 质检提示必须读取本帧 spec / 本帧最终 prompt（2026-11-08）."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.image_qc import _shot_characters, build_qc_prompt  # noqa: E402


def _shot():
    return {
        "index": 1,
        "scene": "荷塘",
        "ff_desc_final": "FF最终提示",
        "lf_desc_final": "LF最终提示",
        "kf_0_desc_final": "KF0最终提示",
        "kf_1_desc_final": "KF1最终提示",
        "kf_2_desc_final": "KF2最终提示",
        "frame_layouts": {
            "ff": [{"name": "甲", "on_screen": True, "position": "画左"}],
            "lf": [{"name": "乙", "on_screen": True, "position": "画右"}],
            "kfs": [
                [{"name": "KF0人", "on_screen": True, "position": "船头"}],
                [{"name": "KF1人", "on_screen": True, "position": "船中"}],
                [{"name": "KF2人", "on_screen": True, "position": "船尾"}],
            ],
        },
    }


def test_keyframe_prompt_uses_its_own_spec_and_final_prompt(caplog):
    shot = _shot()
    chars = [{"identifier": n} for n in ("KF0人", "KF1人", "KF2人")]
    caplog.set_level(logging.WARNING, logger="shared.frame_spec")

    p1 = build_qc_prompt(shot, "keyframe", chars, False, kf_index=1)
    assert "KF1人" in p1 and "KF0人" not in p1 and "KF2人" not in p1
    assert "KF1最终提示" in p1 and "KF0最终提示" not in p1
    assert "kf_index=None" not in caplog.text

    p2 = build_qc_prompt(shot, "keyframe", chars, False, kf_index=2)
    assert "KF2人" in p2 and "KF2最终提示" in p2
    assert "KF0人" not in p2 and "KF1人" not in p2


def test_first_and_last_frame_prompts_remain_unchanged():
    shot = _shot()
    chars = [{"identifier": n} for n in ("甲", "乙")]
    ff = build_qc_prompt(shot, "first_frame", chars, False)
    lf = build_qc_prompt(shot, "last_frame", chars, False)
    assert "甲" in ff and "FF最终提示" in ff and "乙" not in ff
    assert "乙" in lf and "LF最终提示" in lf and "甲" not in lf


def test_shot_characters_accepts_keyframe_index():
    shot = _shot()
    chars = [{"identifier": n} for n in ("KF0人", "KF1人", "KF2人")]
    assert _shot_characters(shot, chars, "keyframe", kf_index=1) == "KF1人"
    assert _shot_characters(shot, chars, "keyframe", kf_index=2) == "KF2人"


def test_maybe_qc_sync_uses_explicit_generation_prompt(monkeypatch, tmp_path):
    import shared.image_qc as iq
    from PIL import Image

    frame = tmp_path / "kf.png"
    Image.new("RGB", (32, 32), (10, 20, 30)).save(frame)

    seen = []

    def _chat(path, prompt):
        seen.append(prompt)
        return '{"has_hallucination": false, "issues": []}'

    monkeypatch.setenv("QC_PROVIDER", "opencode")
    monkeypatch.setattr(iq, "should_run_qc", lambda p: True)
    monkeypatch.setattr(iq, "_opencode_vision_chat", _chat)
    monkeypatch.setattr(iq, "_detect_entities", lambda p: [])

    def _no_build(*a, **k):
        raise AssertionError("显式 prompt 存在时不应重建 QC prompt")

    monkeypatch.setattr(iq, "build_qc_prompt", _no_build)
    shot = {"index": 1, "frame_layouts": {"kfs": [[{"name": "甲", "on_screen": True}]]}}
    res = iq.maybe_qc_sync(str(frame), shot, "keyframe", [], True,
                           prompt="KF1实际生成提示", kf_index=0)
    assert res.ok is True
    assert seen == ["KF1实际生成提示"]


def test_maybe_qc_sync_builds_prompt_with_keyframe_index(monkeypatch, tmp_path):
    import shared.image_qc as iq
    from PIL import Image

    frame = tmp_path / "kf2.png"
    Image.new("RGB", (32, 32), (10, 20, 30)).save(frame)
    seen = []
    built = {}

    def _build(*a, **k):
        built.update(k)
        return "BUILT-KF2-PROMPT"

    def _chat(path, prompt):
        seen.append(prompt)
        return '{"has_hallucination": false, "issues": []}'

    monkeypatch.setenv("QC_PROVIDER", "opencode")
    monkeypatch.setattr(iq, "should_run_qc", lambda p: True)
    monkeypatch.setattr(iq, "_opencode_vision_chat", _chat)
    monkeypatch.setattr(iq, "_detect_entities", lambda p: [])
    monkeypatch.setattr(iq, "build_qc_prompt", _build)
    shot = {"index": 1, "frame_layouts": {"kfs": [[], [], [{"name": "乙", "on_screen": True}]]}}
    res = iq.maybe_qc_sync(str(frame), shot, "keyframe", [], True,
                           prompt=None, kf_index=2)
    assert res.ok is True
    assert built.get("kf_index") == 2
    assert seen == ["BUILT-KF2-PROMPT"]
