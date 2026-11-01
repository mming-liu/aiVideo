# -*- coding: utf-8 -*-
"""S01-S09：素材策略（参考图 vs 首尾帧）的离线契约测试.

覆盖：策略解析/冲突矩阵、显式策略的素材计划、缓存门、成品门、media_mode
派生与校验、参考块压缩、音频守卫、时长回填、双 provider 一致性。
"""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest


def _png(path: Path) -> str:
    from PIL import Image
    Image.new("RGB", (4, 4), (10, 10, 10)).save(path)
    return str(path)


def _req(**kw):
    base = dict(prompt="x", clip_index=1, duration=5, image_path=None,
                last_frame_image_path=None, extra_keyframe_paths=[],
                reference_paths=[], reference_audio=None,
                media_selection=None, seed=None, size="720x1280",
                camera_movement="", negative_prompt="")
    base.update(kw)
    return SimpleNamespace(**base)


# ---------------------------------------------------------------- S01 策略

def test_policy_invalid_value_fails_loudly(monkeypatch):
    from shared.video_media_plan import active_media_policy
    monkeypatch.setenv("VIDEO_MEDIA_POLICY", "first-last")
    with pytest.raises(ValueError, match="VIDEO_MEDIA_POLICY"):
        active_media_policy()


@pytest.mark.parametrize("policy,provider,mode,blocked", [
    ("auto", "volcengine", "image", False),
    ("first_last", "volcengine", "image", False),
    ("first_last", "agent_plan", "image", False),
    ("first_last", "volcengine", "r2v", True),
    ("first_only", "volcengine", "text", True),
    ("reference", "bailian", "image", True),
    ("reference", "minimax", "image", True),
])
def test_policy_conflict_matrix(policy, provider, mode, blocked):
    from shared.video_media_plan import validate_media_policy_context
    got = validate_media_policy_context(policy=policy, provider=provider,
                                        reference_mode=mode)
    assert bool(got) is blocked
    if blocked:
        assert got["code"] == "media_policy_conflict"


def test_first_last_drops_keyframes_and_records(tmp_path):
    from shared.video_media_plan import plan_image_media
    ff, lf = _png(tmp_path / "ff.png"), _png(tmp_path / "lf.png")
    k0, k1 = _png(tmp_path / "kf0.png"), _png(tmp_path / "kf1.png")
    plan = plan_image_media(supports_reference_image=True, image_path=ff,
                            last_frame_path=lf, keyframe_paths=[k0, k1],
                            policy="first_last")
    assert plan["mode"] == "first_last"
    assert [i["role"] for i in plan["items"]] == ["first_frame", "last_frame"]
    assert {d["path"] for d in plan["dropped"]} == {k0, k1}
    assert all(d["reason"] == "policy_dropped_keyframes" for d in plan["dropped"])
    assert plan["modifications"][0]["kind"] == "media_policy"
    assert plan["blocked"] is None


def test_first_last_missing_last_frame_is_blocked(tmp_path):
    from shared.video_media_plan import plan_image_media
    ff = _png(tmp_path / "ff.png")
    plan = plan_image_media(supports_reference_image=True, image_path=ff,
                            last_frame_path=None, keyframe_paths=[],
                            policy="first_last")
    assert plan["blocked"]["code"] == "media_policy_missing_frame"
    assert plan["items"] == []


def test_first_only_drops_last_frame(tmp_path):
    from shared.video_media_plan import plan_image_media
    ff, lf = _png(tmp_path / "ff.png"), _png(tmp_path / "lf.png")
    plan = plan_image_media(supports_reference_image=True, image_path=ff,
                            last_frame_path=lf, keyframe_paths=[],
                            policy="first_only")
    assert plan["mode"] == "first"
    assert [i["role"] for i in plan["items"]] == ["first_frame"]
    assert any(d["reason"] == "policy_dropped_last_frame" for d in plan["dropped"])


def test_reference_accepts_keyframes_only(tmp_path):
    from shared.video_media_plan import plan_image_media
    k0, k1 = _png(tmp_path / "kf0.png"), _png(tmp_path / "kf1.png")
    plan = plan_image_media(supports_reference_image=True, image_path=None,
                            last_frame_path=None, keyframe_paths=[k0, k1],
                            policy="reference")
    assert plan["mode"] == "reference"
    assert [i["role"] for i in plan["items"]] == ["reference_image"] * 2
    assert plan["blocked"] is None


def test_reference_without_capability_is_blocked(tmp_path):
    from shared.video_media_plan import plan_image_media
    ff = _png(tmp_path / "ff.png")
    plan = plan_image_media(supports_reference_image=False, image_path=ff,
                            keyframe_paths=[], policy="reference")
    assert plan["blocked"]["code"] == "media_policy_unsupported"


def test_derive_media_mode_from_roles():
    from shared.video_media_plan import derive_media_mode
    assert derive_media_mode([]) == "none"
    assert derive_media_mode(["reference_image", "reference_image"]) == "reference"
    assert derive_media_mode(["first_frame", "last_frame"]) == "first_last"
    assert derive_media_mode(["first_frame"]) == "first"
    assert derive_media_mode(["reference"]) == "reference"


def test_default_frame_selection_follows_policy(monkeypatch):
    from shared.video_media_plan import default_frame_selection
    assert default_frame_selection("first_last") == {"ff", "lf"}
    assert default_frame_selection("first_only") == {"ff"}
    assert default_frame_selection("reference") == {"ff", "lf", "kf"}


# ---------------------------------------------------------------- S02 入口

def test_collect_frame_paths_first_only_accepts_missing_last(tmp_path):
    import step02b_video_gen.main as m
    d = tmp_path / "shots" / "1"
    d.mkdir(parents=True)
    _png(d / "first_frame.png")
    assert m._collect_frame_paths(1, d, "first_only")
    with pytest.raises(FileNotFoundError):
        m._collect_frame_paths(1, d, "first_last")


def test_product_acceptance_first_only_passes_without_last(tmp_path, monkeypatch):
    import step02a_frame_gen.image_prompt_quality as q
    monkeypatch.setattr(q, "chapter_dir", lambda *a, **k: tmp_path)
    shot_dir = tmp_path / "shots" / "1"
    shot_dir.mkdir(parents=True)
    _png(shot_dir / "first_frame.png")
    script = {"shots": [{"index": 1, "ff_desc": "a", "lf_desc": "b", "keyframes": []}]}
    ok = q.write_image_product_quality(script, tmp_path, media_policy="first_only")
    assert ok["status"] == "PASS"
    blocked = q.write_image_product_quality(script, tmp_path, media_policy="first_last")
    assert blocked["status"] == "BLOCK"


def test_make_request_first_last_drops_keyframes(tmp_path, monkeypatch):
    from step02_video_gen.prompt_builder import _make_request
    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    monkeypatch.setenv("VIDEO_MEDIA_POLICY", "first_last")
    monkeypatch.setenv("VOLCENGINE_I2V_MODEL", "doubao-seedance-2-0-fast-260128")
    ff, lf = _png(tmp_path / "ff.png"), _png(tmp_path / "lf.png")
    k0 = _png(tmp_path / "kf0.png")
    shot = {"index": 1, "ff_desc": "甲站在船头", "lf_desc": "甲收刀",
            "motion_desc": "挥刀", "duration_sec": 5,
            "keyframes": [{"desc": "挥刀中段"}], "prompt": "p"}
    req = _make_request(None, "p", [ff, lf, k0], shot, characters=[])
    assert req.image_path == ff
    assert req.last_frame_image_path == lf
    assert req.extra_keyframe_paths == []
    assert req.media_selection["mode"] == "first_last"
    assert req.media_selection["policy"] == "first_last"
    assert req.media_selection["media_mode"] == "first_last"


# ---------------------------------------------------------------- S03 记录

def test_verify_detects_both_sides_wrong_media_mode(tmp_path):
    from _video_gen.models import MaterialRef
    from step02_video_gen import request_plan as rp
    ref = _png(tmp_path / "ff.png")
    prepared = SimpleNamespace(
        prompt="p", parameters={"duration": 5}, mode="image",
        media_mode="first_last",  # 故意标错：素材其实是 reference
        materials=[MaterialRef(role="reference_image", kind="image",
                               source_path=ref, sha256="x", ref_id="r1")],
        provider="volcengine", model_id="m", modifications=[],
        capability_revision=None)
    frozen = rp.build_frozen_request(
        _req(), shot={"index": 1}, scope={"chapter": 1}, script_revision="r",
        provider="volcengine", model_id="m", prepared=prepared)
    codes = [p["code"] for p in rp.verify_prepared_frozen(prepared, frozen)]
    assert "prepared_media_mode_role_mismatch" in codes
    assert "frozen_media_mode_role_mismatch" in codes


def test_prepared_media_mode_roundtrip():
    from _video_gen.models import PreparedVideoRequest
    p = PreparedVideoRequest(request_id="r", provider="volcengine",
                             model_id="m", mode="image", prompt="p",
                             media_mode="first_last")
    assert PreparedVideoRequest.from_dict(p.to_dict()).media_mode == "first_last"


# ---------------------------------------------------------------- S01 缓存

def test_require_exact_rejects_legacy_cache(tmp_path):
    from step02_video_gen.submit_poll import cache_decision
    d = tmp_path
    (d / "video.mp4").write_bytes(b"0" * 30000)
    (d / "prompt_hash.txt").write_text("h", encoding="utf-8")
    hit = cache_decision({"index": 1}, {}, d, d / "video.mp4",
                         shot_hash="h", require_exact=False)
    assert hit["hit"] and hit["relation"] == "legacy"
    miss = cache_decision({"index": 1}, {}, d, d / "video.mp4",
                          shot_hash="h", require_exact=True)
    assert not miss["hit"]


def test_shot_hash_changes_only_for_non_auto_policy(monkeypatch):
    from step02_video_gen.submit_poll import _get_shot_hash
    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    monkeypatch.setenv("VIDEO_REFERENCE_MODE", "image")
    shot = {"index": 1, "prompt": "p", "ff_desc": "ff", "lf_desc": "lf",
            "narration_segment": "", "keyframes": []}
    monkeypatch.setenv("VIDEO_MEDIA_POLICY", "auto")
    h_auto = _get_shot_hash(shot, {"characters": []})
    monkeypatch.setenv("VIDEO_MEDIA_POLICY", "first_last")
    h_first = _get_shot_hash(shot, {"characters": []})
    monkeypatch.setenv("VIDEO_MEDIA_POLICY", "first_only")
    h_only = _get_shot_hash(shot, {"characters": []})
    assert h_auto != h_first
    assert h_first != h_only


# ---------------------------------------------------------------- S06 压缩

def _long_prompt(block: str) -> str:
    body = "【视频起点】" + "甲在船头挥刀。" * 60
    return body + "\n\n" + block


def test_reference_block_survives_compression():
    from _video_gen.volcengine_provider import VolcengineVideoService
    block = "【参考图清单】参考图1：视频起点画面（首帧）；参考图2：视频终点画面（尾帧）"
    req = _req(prompt=_long_prompt(block))
    text, mods = VolcengineVideoService._effective_prompt(None, req, "doubao-seedance-2-0-fast-260128")
    assert "参考图1：" in text and "参考图2：" in text
    assert len(text) <= 500


def test_reference_block_over_budget_blocks():
    from _video_gen.models import VideoCapabilityError
    from _video_gen.volcengine_provider import VolcengineVideoService
    block = "【参考图清单】" + "参考图1：视频起点画面；" * 40
    req = _req(prompt=_long_prompt(block))
    with pytest.raises(VideoCapabilityError) as excinfo:
        VolcengineVideoService._effective_prompt(None, req, "doubao-seedance-2-0-fast-260128")
    assert excinfo.value.code == "prompt_ref_block_over_budget"


# ---------------------------------------------------------------- S07 音频

def test_reference_audio_not_mixed_into_first_last(tmp_path):
    from _video_gen.volcengine_provider import VolcengineVideoService
    ff, lf = _png(tmp_path / "ff.png"), _png(tmp_path / "lf.png")
    audio = str(tmp_path / "a.mp3")
    (tmp_path / "a.mp3").write_bytes(b"ID3")
    req = _req(image_path=ff, last_frame_image_path=lf, reference_audio=audio)
    items, mods = VolcengineVideoService._plan_content(
        None, req, "doubao-seedance-2-0-fast-260128")
    assert all(i.get("role") != "reference_audio" for i in items)
    assert any(m.get("field") == "reference_audio" for m in mods)


# ---------------------------------------------------------------- S08 时长

def test_record_submitted_duration_rules():
    from _video_gen.models import VideoCapabilityError
    from step02_video_gen.submit_poll import record_submitted_duration
    shot = {"planned_duration_sec": 2.0}
    record_submitted_duration(shot, SimpleNamespace(parameters={"duration": 4}))
    assert shot["submitted_duration_sec"] == 4
    assert shot["_duration_modifications"][0]["kind"] == "duration_extended"
    short = {"planned_duration_sec": 9.0}
    with pytest.raises(VideoCapabilityError) as excinfo:
        record_submitted_duration(short, SimpleNamespace(parameters={"duration": 5}))
    assert excinfo.value.code == "duration_insufficient"


# ---------------------------------------------------------------- S05 一致性

def test_two_providers_share_media_roles(tmp_path):
    from _video_gen.agent_plan_provider import AgentPlanVideoService
    from _video_gen.volcengine_provider import VolcengineVideoService
    ff, lf = _png(tmp_path / "ff.png"), _png(tmp_path / "lf.png")
    k0 = _png(tmp_path / "kf0.png")
    req = _req(image_path=ff, last_frame_image_path=lf, extra_keyframe_paths=[k0])
    model = "doubao-seedance-2-0-fast-260128"
    os.environ["VIDEO_MEDIA_POLICY"] = "first_last"
    try:
        a, _ = VolcengineVideoService._plan_content(None, req, model)
        b, _ = AgentPlanVideoService._plan_content(None, req, model)
    finally:
        os.environ.pop("VIDEO_MEDIA_POLICY", None)
    assert [(i["role"], Path(i["source_path"]).name) for i in a] == \
           [(i["role"], Path(i["source_path"]).name) for i in b]
    assert [i["role"] for i in a] == ["first_frame", "last_frame"]
