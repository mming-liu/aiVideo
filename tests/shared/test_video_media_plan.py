# -*- coding: utf-8 -*-
"""视频素材计划：提示词与请求必须用**同一份**最终清单（离线，0 次生成）.

背景：旧实现里提示词按 "ff + lf + 3 张关键帧" 声明参考图，provider 又按模型能力
裁成 2 张（且用 kf0/kf2 顶替首尾帧）。本用例锁三件事：

1. 有真正的 ff/lf 时优先用它们，关键帧显式丢弃并记账；
2. 提示词的参考图条数与最终 items 逐项一致，不出现"提到但没发"；
3. provider 的 _plan_content 与 plan_image_media 输出一致（同一条链）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from shared.video_media_plan import plan_image_media, reference_labels


@pytest.fixture(autouse=True)
def offline_video_credentials(monkeypatch):
    from _video_gen import volcengine_provider as provider
    monkeypatch.setenv("VOLCENGINE_API_KEY", "offline-fixture")
    monkeypatch.setattr(provider, "VOLCENGINE_API_KEY", "offline-fixture")


def _files(tmp_path, *names):
    out = []
    for n in names:
        p = tmp_path / n
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
        out.append(str(p))
    return out


def test_real_first_last_frames_win_over_keyframes(tmp_path):
    ff, lf, k0, k1, k2 = _files(tmp_path, "ff.png", "lf.png", "kf0.png", "kf1.png", "kf2.png")
    plan = plan_image_media(supports_reference_image=False, image_path=ff,
                            last_frame_path=lf, keyframe_paths=[k0, k1, k2])
    assert plan["mode"] == "first_last"
    assert [i["role"] for i in plan["items"]] == ["first_frame", "last_frame"]
    assert [i["path"] for i in plan["items"]] == [ff, lf]
    assert {d["path"] for d in plan["dropped"]} == {k0, k1, k2}
    assert plan["modifications"][0]["kind"] == "keyframe_degrade"


def test_reference_capable_model_uses_keyframes(tmp_path):
    ff, lf, k0, k1 = _files(tmp_path, "ff.png", "lf.png", "kf0.png", "kf1.png")
    plan = plan_image_media(supports_reference_image=True, image_path=ff,
                            last_frame_path=lf, keyframe_paths=[k0, k1])
    assert plan["mode"] == "reference"
    assert [i["role"] for i in plan["items"]] == ["reference_image", "reference_image"]
    assert plan["dropped"] and {d["path"] for d in plan["dropped"]} == {ff, lf}


def test_without_first_last_frames_keyframe_endpoints_are_explicit(tmp_path):
    k0, k1, k2 = _files(tmp_path, "kf0.png", "kf1.png", "kf2.png")
    plan = plan_image_media(supports_reference_image=False, keyframe_paths=[k0, k1, k2])
    assert plan["mode"] == "keyframe_fallback"
    assert [i["path"] for i in plan["items"]] == [k0, k2]
    assert plan["modifications"][0]["kind"] == "keyframe_substituted"


def test_reference_labels_follow_items_exactly(tmp_path):
    ff, lf, k0 = _files(tmp_path, "ff.png", "lf.png", "kf0.png")
    plan = plan_image_media(supports_reference_image=False, image_path=ff,
                            last_frame_path=lf, keyframe_paths=[k0])
    labels = reference_labels(plan["items"])
    assert len(labels) == len(plan["items"]) == 2
    assert labels[0].startswith("参考图1：视频起点画面")
    assert labels[1].startswith("参考图2：视频终点画面")


def test_prompt_list_mentions_only_planned_items(tmp_path):
    """提示词里不能再出现"关键帧"——关键帧已被计划丢弃."""
    from step02_video_gen.prompt_builder import _build_reference_list
    ff, lf, k0, k1, k2 = _files(tmp_path, "ff.png", "lf.png", "kf0.png", "kf1.png", "kf2.png")
    plan = plan_image_media(supports_reference_image=False, image_path=ff,
                            last_frame_path=lf, keyframe_paths=[k0, k1, k2])
    text = _build_reference_list(ff, lf, [k0, k1, k2], media_items=plan["items"])
    assert "关键帧" not in text
    assert "参考图1：" in text and "参考图2：" in text
    assert "参考图3：" not in text          # 第 3 张已被计划丢弃，提示词不得再提


def test_provider_plan_content_matches_shared_plan(tmp_path):
    """provider 的请求构造与共享计划逐项一致（同一条链，不再各裁各的）."""
    from _video_gen.models import VideoGenerationRequest
    from _video_gen.volcengine_provider import VolcengineVideoService
    from shared.model_caps import active_video_model

    ff, lf, k0, k1, k2 = _files(tmp_path, "ff.png", "lf.png", "kf0.png", "kf1.png", "kf2.png")
    req = VideoGenerationRequest(prompt="x", clip_index=8, duration=3, image_path=ff,
                                 last_frame_image_path=lf,
                                 extra_keyframe_paths=[k0, k1, k2])
    items, mods = VolcengineVideoService._plan_content(None, req, active_video_model())
    plan = plan_image_media(
        supports_reference_image=bool(getattr(
            __import__("shared.model_caps", fromlist=["get_seedance_cap"]).get_seedance_cap(
                active_video_model()), "supports_reference_image", False)),
        image_path=ff, last_frame_path=lf, keyframe_paths=[k0, k1, k2])
    assert [(Path(i["source_path"]).name, i["role"]) for i in items] == \
           [(Path(i["path"]).name, i["role"]) for i in plan["items"]]
    assert any(m.get("kind") == "keyframe_degrade" for m in mods)

def test_frozen_request_chain_is_consistent(tmp_path, monkeypatch):
    """从**实际请求构造入口**贯通：build_frozen_for_shot → prompt → provider 同一份素材."""
    import os
    from _video_gen.volcengine_provider import VolcengineVideoService
    from step02_video_gen.submit_poll import build_frozen_for_shot

    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    ff, lf, k0, k1, k2 = _files(tmp_path, "ff.png", "lf.png", "kf0.png", "kf1.png", "kf2.png")
    shot = {"index": 8, "scene": "湾", "main_subject": "甲", "duration_sec": 3.5,
            "ff_desc": "甲立于船头，右手举刀", "lf_desc": "刀已入水，甲垂手",
            "motion_desc": "前1秒：举刀；1-2秒：落刀；2-3.5秒：收手",
            "camera_movement": "固定机位", "audio_cue": "", "prompt": "甲在船头挥刀",
            "frame_layouts": {}, "keyframes": [], "clip_transition": ""}
    script = {"characters": [{"identifier": "甲", "static_features": "青年男子"}],
              "shots": [shot], "style": "", "global_lighting": "",
              "global_atmosphere": "", "negative_prompt": "",
              "resolution": "720x1280", "ratio": "9:16"}
    frozen, req = build_frozen_for_shot(
        shot=shot, script=script, video_adapter=None,
        frame_paths=[ff, lf, k0, k1, k2], mode="A", scope={"chapter": 1},
        provider="volcengine",
        model_id=os.getenv("VOLCENGINE_I2V_MODEL") or "doubao-seedance-1-0-pro-250528")

    assert Path(req.image_path).name == "ff.png"
    assert Path(req.last_frame_image_path).name == "lf.png"
    assert list(req.extra_keyframe_paths or []) == []
    assert "参考图3：" not in req.prompt and "关键帧" not in req.prompt

    items, _mods = VolcengineVideoService._plan_content(None, req, frozen.model_id)
    assert [(Path(i["source_path"]).name, i["role"]) for i in items] == \
           [("ff.png", "first_frame"), ("lf.png", "last_frame")]

# ---------------------------------------------------------------- 离线最终请求体（2026-10-31 晚）

def _png(path, color=(10, 20, 30)):
    from PIL import Image
    Image.new("RGB", (16, 16), color).save(path)
    return str(path)


def test_offline_request_body_matches_prompt_and_plan(tmp_path, monkeypatch):
    """贯通到最终序列化请求体：提示词编号 = 实际图片数量/顺序/role，且零网络."""
    from _video_gen import volcengine_provider as vp
    from _video_gen.models import VideoGenerationRequest
    from step02_video_gen.request_plan import build_frozen_request

    ff = _png(tmp_path / "ff.png", (1, 1, 1))
    lf = _png(tmp_path / "lf.png", (2, 2, 2))
    k0 = _png(tmp_path / "kf0.png", (3, 3, 3))
    k1 = _png(tmp_path / "kf1.png", (4, 4, 4))
    k2 = _png(tmp_path / "kf2.png", (5, 5, 5))

    plan = plan_image_media(supports_reference_image=False, image_path=ff,
                            last_frame_path=lf, keyframe_paths=[k0, k1, k2])
    from step02_video_gen.prompt_builder import _build_reference_list
    prompt = "甲在船头挥刀\n\n" + _build_reference_list(ff, lf, [k0, k1, k2],
                                                        media_items=plan["items"])
    req = VideoGenerationRequest(
        prompt=prompt, clip_index=8, duration=3, image_path=ff,
        last_frame_image_path=lf, extra_keyframe_paths=[],
        media_selection={"mode": plan["mode"],
                         "items": plan["items"],
                         "dropped": plan["dropped"],
                         "modifications": plan["modifications"]})

    provider = vp.VolcengineVideoService()
    prepared = provider.prepare_request(req)

    captured = {}

    class _Resp:
        status_code = 200
        text = '{"id": "task-1"}'

        @staticmethod
        def json():
            return {"id": "task-1"}

    def _fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return _Resp()

    monkeypatch.setattr(vp.requests, "post", _fake_post)
    task_id = provider.create_prepared_task(prepared)
    assert task_id == "task-1"
    assert len(captured) == 2                      # 只调用了一次 stub，零真实网络

    content = captured["payload"]["content"]
    assert content[0]["type"] == "text"
    media = [c for c in content if c["type"] != "text"]
    assert [c["role"] for c in media] == ["first_frame", "last_frame"]
    assert len(media) == 2
    assert "参考图1：" in content[0]["text"] and "参考图2：" in content[0]["text"]
    assert "参考图3：" not in content[0]["text"]

    kinds = [m.get("kind") for m in prepared.modifications]
    assert "keyframe_degrade" in kinds

    frozen = build_frozen_request(req, shot={"index": 8}, scope={"chapter": 1},
                                  script_revision="rev-1", provider="volcengine",
                                  model_id=prepared.model_id, prepared=prepared)
    saved = frozen.to_dict()
    assert saved["media_selection"]["mode"] == "first_last"
    assert any(m.get("kind") == "keyframe_degrade"
               for m in saved["media_selection"]["modifications"])
    assert "media_selection" not in frozen.key_facts()


def test_tampered_or_missing_material_is_rejected_before_send(tmp_path, monkeypatch):
    from _video_gen import volcengine_provider as vp
    from _video_gen.models import VideoGenerationRequest, VideoCapabilityError

    ff = _png(tmp_path / "ff.png", (7, 7, 7))
    lf = _png(tmp_path / "lf.png", (8, 8, 8))
    req = VideoGenerationRequest(prompt="x", clip_index=1, duration=2,
                                 image_path=ff, last_frame_image_path=lf)
    provider = vp.VolcengineVideoService()
    prepared = provider.prepare_request(req)

    calls = {"n": 0}

    def _fake_post(*a, **k):
        calls["n"] += 1
        raise AssertionError("不得发送")

    monkeypatch.setattr(vp.requests, "post", _fake_post)

    with open(ff, "ab") as fh:
        fh.write(b"x")
    with pytest.raises(VideoCapabilityError) as exc:
        provider.create_prepared_task(prepared)
    assert getattr(exc.value, "code", "") == "material_changed"

    # 缺失：另起一份未篡改的请求，删掉尾帧 ⇒ material_missing
    import os
    ff2 = _png(tmp_path / "ff2.png", (9, 9, 9))
    lf2 = _png(tmp_path / "lf2.png", (10, 10, 10))
    prepared2 = provider.prepare_request(
        VideoGenerationRequest(prompt="x", clip_index=2, duration=2,
                               image_path=ff2, last_frame_image_path=lf2))
    os.remove(lf2)
    with pytest.raises(VideoCapabilityError) as exc2:
        provider.create_prepared_task(prepared2)
    assert getattr(exc2.value, "code", "") == "material_missing"
    assert calls["n"] == 0
