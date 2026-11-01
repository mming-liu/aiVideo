# -*- coding: utf-8 -*-
from __future__ import annotations
import sys
from pathlib import Path
from types import SimpleNamespace

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _req():
    return SimpleNamespace(
        duration=6, size="720x1280", seed=1, negative_prompt="n",
        camera_movement="固定机位", prompt="正文", clip_index=1,
        media_selection={}, references=[], reference_paths=[],
        image_path=None, last_frame_image_path=None, extra_keyframe_paths=[],
        mode="image")


def _prepared():
    from _video_gen.models import MaterialRef
    return SimpleNamespace(
        prompt="运镜：固定机位\n正文",
        parameters={"duration": 6, "size": "720x1280", "seed": 1,
                    "negative_prompt": "n", "camera_movement": "固定机位",
                    "resolution": "720p"},
        materials=[MaterialRef(role="first_frame", kind="image",
                               source_path="/tmp/x.png", sha256="abc", ref_id="r1")],
        mode="image", provider="volcengine", model_id="m",
        modifications=[], capability_revision=None)


def test_verify_prepared_frozen_ok():
    from step02_video_gen import request_plan as rp
    prepared = _prepared()
    frozen = rp.build_frozen_request(
        _req(), shot={"index": 1}, scope={"chapter": 1}, script_revision="rev",
        provider="volcengine", model_id="m", prepared=prepared)
    assert rp.verify_prepared_frozen(prepared, frozen) == []


def test_verify_prepared_frozen_detects_prompt_change():
    from step02_video_gen import request_plan as rp
    prepared = _prepared()
    frozen = rp.build_frozen_request(
        _req(), shot={"index": 1}, scope={"chapter": 1}, script_revision="rev",
        provider="volcengine", model_id="m", prepared=prepared)
    prepared.prompt = "changed"
    codes = [p["code"] for p in rp.verify_prepared_frozen(prepared, frozen)]
    assert "prepared_prompt_mismatch" in codes


def test_verify_prepared_frozen_detects_parameter_change():
    from step02_video_gen import request_plan as rp
    prepared = _prepared()
    frozen = rp.build_frozen_request(
        _req(), shot={"index": 1}, scope={"chapter": 1}, script_revision="rev",
        provider="volcengine", model_id="m", prepared=prepared)
    prepared.parameters["resolution"] = "1080p"
    codes = [p["code"] for p in rp.verify_prepared_frozen(prepared, frozen)]
    assert "prepared_parameter_mismatch" in codes
