# -*- coding: utf-8 -*-
"""T1-T4：Seedance 官方提示词最佳实践接线.

覆盖：
- T1 原生视频参考：媒体类型识别、video_url 编码、能力位门控/回退、
  逐图+视频指代文案、continuity 可选路径；
- T2 单条连续长镜 + 多节拍时间窗（低权重）；
- T3 运镜门禁精化：矛盾/无序才警告，连续长镜+时间窗放行，blocking 不变；
- T4 类型化氛围/情绪 enrichment（低权重）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from _video_gen.media_io import encode_media, media_kind, media_mime  # noqa: E402
from _video_gen.models import (  # noqa: E402
    MaterialRef,
    PreparedVideoRequest,
    VideoCapabilityError,
    VideoGenerationRequest,
)
from shared.model_caps import (  # noqa: E402
    get_seedance_cap,
    seedance_supports_local_video_reference,
    seedance_supports_video_reference,
)
from shared.ref_compose import get_video_ref_limit  # noqa: E402
from step02_video_gen import prompt_builder as pb  # noqa: E402
from step02_video_gen.continuity import resolve_video_reference  # noqa: E402
from step02_video_gen.video_prompt_quality import _camera_stacking_issue  # noqa: E402


@pytest.fixture
def media(tmp_path):
    img = tmp_path / "characters_bank" / "角色" / "front.png"
    img.parent.mkdir(parents=True)
    img.write_bytes(b"PNG-DATA")
    vid = tmp_path / "prev.mp4"
    vid.write_bytes(b"MP4-DATA")
    return img, vid


def _service(provider: str):
    import _video_gen.agent_plan_provider as ap
    import _video_gen.volcengine_provider as vp
    cls = vp.VolcengineVideoService if provider == "volcengine" else ap.AgentPlanVideoService
    return object.__new__(cls)


def _prepared(items, model="doubao-seedance-2-0"):
    return PreparedVideoRequest(
        request_id="r1", provider="test", model_id=model, mode="r2v",
        prompt="p", content_plan=[{"type": "text", "text": "p"}] + list(items))


# ---------------------------------------------------------------------------
# T1 媒体类型 / 编码
# ---------------------------------------------------------------------------
def test_media_kind_and_mime_classify_video():
    assert media_kind("a.mp4") == "video"
    assert media_kind("a.MOV") == "video"
    assert media_kind("a.webm") == "video"
    assert media_kind("a.png") == "image"
    assert media_mime("a.mp4") == "video/mp4"
    assert media_mime("a.mov") == "video/quicktime"


def test_encode_media_uses_video_mime(tmp_path):
    vid = tmp_path / "prev.mp4"
    vid.write_bytes(b"abc")
    url = encode_media(str(vid))
    assert url.startswith("data:video/mp4;base64,")


def test_capability_bits_for_seedance_2x():
    assert seedance_supports_video_reference("doubao-seedance-2.0")
    assert seedance_supports_local_video_reference("doubao-seedance-2.0")
    assert seedance_supports_video_reference("doubao-seedance-2.5")
    # 2.5 官方只接受公网 URL/素材 ID，本地 Base64 不可用
    assert not seedance_supports_local_video_reference("doubao-seedance-2.5")
    assert not seedance_supports_video_reference("doubao-seedance-1.0-pro")
    assert get_seedance_cap("doubao-seedance-2.5").supports_video_reference
    assert get_video_ref_limit("doubao-seedance-2.5") == 10
    assert get_video_ref_limit("doubao-seedance-2.0") == 3


# ---------------------------------------------------------------------------
# T1 provider：计划 + materialize
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("provider", ["volcengine", "agent_plan"])
def test_provider_plans_and_encodes_video_reference(provider, media):
    img, vid = media
    svc = _service(provider)
    req = VideoGenerationRequest(prompt="p", clip_index=1,
                                 reference_paths=[str(img), str(vid)])
    items, _mods = svc._plan_content(req, "doubao-seedance-2-0")
    assert [i["type"] for i in items] == ["image", "video"]
    assert items[0]["role"] == "reference_image"
    assert items[1]["role"] == "reference_video"

    content = svc._materialize_content(_prepared(items))
    assert [c["type"] for c in content] == ["text", "image_url", "video_url"]
    assert content[2]["role"] == "reference_video"
    assert content[2]["video_url"]["url"].startswith("data:video/mp4;base64,")


@pytest.mark.parametrize("provider", ["volcengine", "agent_plan"])
def test_provider_25_rejects_local_video_base64(provider, media):
    img, vid = media
    svc = _service(provider)
    req = VideoGenerationRequest(prompt="p", clip_index=1,
                                 reference_paths=[str(img), str(vid)])
    with pytest.raises(VideoCapabilityError) as ei:
        svc._plan_content(req, "doubao-seedance-2.5")
    assert ei.value.code == "video_reference_requires_url"


def test_provider_drops_video_when_capability_missing(monkeypatch, media):
    """能力位缺失时视频参考显式丢弃并记账，图片参考继续；不静默当图片发."""
    import shared.model_caps as mc
    from dataclasses import replace
    img, vid = media
    svc = _service("volcengine")
    fake = replace(get_seedance_cap("doubao-seedance-2-0"),
                   supports_video_reference=False,
                   video_reference_local_base64=False)
    monkeypatch.setattr(mc, "get_seedance_cap", lambda *_a, **_k: fake)
    req = VideoGenerationRequest(prompt="p", clip_index=1,
                                 reference_paths=[str(img), str(vid)])
    items, mods = svc._plan_content(req, "fake-model")
    assert [i["type"] for i in items] == ["image"]
    assert any(m.get("field") == "reference_video" for m in mods)


# ---------------------------------------------------------------------------
# T1 prompt：图/视频分开编号 + 指代文案
# ---------------------------------------------------------------------------
def test_r2v_purpose_list_numbers_images_and_videos_separately(media):
    img, vid = media
    shot = {"index": 1, "scene": "屋内", "location": "屋内",
            "ff_desc": "角色站立", "motion_desc": "角色转身"}
    out = pb._build_r2v_purpose_list(
        shot, registry={}, characters=[{"identifier": "角色"}],
        ref_paths=[str(img), str(vid)])
    assert "图1" in out and "视频1" in out
    assert "运镜" in out and "光影" in out
    assert "图2" not in out, "视频不占图片编号"
    assert "视频1" in out and "prev.mp4" in out


def test_frozen_reference_accepts_video_role(media):
    """冻结契约只认规范角色：reference_video 必须归一到 reference，不得被
    预检判成 unknown_role 而在 POST 前拒收."""
    from step02_video_gen.request_plan import ROLE_REFERENCE, _references_from_materials
    img, vid = media
    refs = _references_from_materials([
        MaterialRef(role="reference_image", kind="image",
                    source_path=str(img), sha256="a", ref_id="r1"),
        MaterialRef(role="reference_video", kind="video",
                    source_path=str(vid), sha256="b", ref_id="r2"),
    ])
    assert [r.role for r in refs] == [ROLE_REFERENCE, ROLE_REFERENCE]


def test_frozen_request_with_video_reference_passes_validation(media):
    """完整冻结路径：prepared 的 reference_video 不得在 POST 前被 unknown_role 拒收."""
    import hashlib
    from step02_video_gen import request_plan as rp
    img, vid = media

    def _sha(p):
        return hashlib.sha256(Path(p).read_bytes()).hexdigest()

    req = VideoGenerationRequest(prompt="p", clip_index=1, duration=6,
                                 reference_paths=[str(img), str(vid)])
    prepared = PreparedVideoRequest(
        request_id="r1", provider="volcengine", model_id="doubao-seedance-2-0",
        mode="r2v", media_mode="reference", prompt="p",
        parameters={"duration": 6},
        materials=[
            MaterialRef(role="reference_image", kind="image",
                        source_path=str(img), sha256=_sha(img), ref_id="r1"),
            MaterialRef(role="reference_video", kind="video",
                        source_path=str(vid), sha256=_sha(vid), ref_id="r2"),
        ])
    frozen = rp.build_frozen_request(
        req, shot={"index": 1}, scope={}, script_revision="rev",
        provider="volcengine", model_id="doubao-seedance-2-0", prepared=prepared)
    assert [r.role for r in frozen.references] == ["reference", "reference"]
    assert rp.validate_frozen_request(frozen) == []


def test_continuity_video_reference_is_capability_gated(media):
    img, vid = media
    ok = resolve_video_reference(str(vid), model="doubao-seedance-2.0")
    assert ok["path"] == str(vid) and ok["reason"] == "ok"
    no = resolve_video_reference(str(vid), model="doubao-seedance-2.5")
    assert no["path"] is None and no["reason"] == "video_reference_unsupported"
    missing = resolve_video_reference(str(vid) + ".nope", model="doubao-seedance-2.0")
    assert missing["path"] is None and missing["reason"] == "previous_video_missing"
    not_video = resolve_video_reference(str(img), model="doubao-seedance-2.0")
    assert not_video["path"] is None and not_video["reason"] == "not_a_video"


def _make_request_fixture(monkeypatch, model: str, image_path):
    monkeypatch.setenv("VIDEO_PROVIDER", "volcengine")
    monkeypatch.setenv("VOLCENGINE_I2V_MODEL", model)
    monkeypatch.setattr(pb, "_REFERENCE_MODE", "image", raising=False)
    monkeypatch.setattr(pb, "_USE_TEXT_TO_VIDEO", False, raising=False)
    monkeypatch.setattr(pb, "_MODEL_VOICE", False, raising=False)
    monkeypatch.setattr(pb, "_load_character_registry", lambda: {}, raising=False)
    monkeypatch.setattr(
        pb, "_collect_r2v_references",
        lambda shot, characters=None, registry=None: [str(image_path)],
        raising=False)


def test_make_request_uses_video_reference_when_capable(monkeypatch, media):
    img, vid = media
    _make_request_fixture(monkeypatch, "doubao-seedance-2.0", img)
    shot = {"index": 1, "duration_sec": 5, "ff_desc": "角色站立",
            "lf_desc": "角色转身", "motion_desc": "0-3秒：站立；3-5秒：转身",
            "prompt": "p", "camera_movement": "固定机位"}
    req = pb._make_request(
        None, "p", [str(img)], shot,
        characters=[{"identifier": "角色"}], continuity_ref_video=str(vid))
    assert req.reference_paths == [str(img), str(vid)]
    assert req.image_path is None and req.last_frame_image_path is None
    assert "视频1" in req.prompt and "运镜" in req.prompt
    assert req.media_selection["items"][1]["role"] == "reference_video"


def test_make_request_falls_back_when_video_reference_unsupported(monkeypatch, media):
    img, vid = media
    _make_request_fixture(monkeypatch, "doubao-seedance-1.0-pro", img)
    shot = {"index": 1, "duration_sec": 5, "ff_desc": "角色站立",
            "lf_desc": "角色转身", "motion_desc": "0-3秒：站立；3-5秒：转身",
            "prompt": "p", "camera_movement": "固定机位"}
    req = pb._make_request(
        None, "p", [str(img)], shot,
        characters=[{"identifier": "角色"}], continuity_ref_video=str(vid))
    assert req.reference_paths == []
    assert req.image_path == str(img), "回退到现有首帧 I2V，不破坏既有连续性"
    assert "视频1" not in req.prompt


def test_make_request_ignores_non_video_continuity_ref(monkeypatch, media):
    img, _vid = media
    _make_request_fixture(monkeypatch, "doubao-seedance-2.0", img)
    shot = {"index": 1, "duration_sec": 5, "ff_desc": "角色站立",
            "lf_desc": "角色转身", "motion_desc": "0-3秒：站立；3-5秒：转身",
            "prompt": "p", "camera_movement": "固定机位"}
    req = pb._make_request(
        None, "p", [str(img)], shot,
        characters=[{"identifier": "角色"}], continuity_ref_video=str(img))
    assert req.reference_paths == []
    assert req.image_path == str(img)
    assert "视频1" not in req.prompt


# ---------------------------------------------------------------------------
# T2 时间窗
# ---------------------------------------------------------------------------
def test_time_window_block_for_continuous_long_shot():
    shot = {
        "index": 1, "duration_sec": 9,
        "camera_movement": "一条连续镜头，缓慢推进",
        "motion_desc": "前3秒：甲拔剑；3-6秒：乙后退；6-9秒：甲收剑",
    }
    out = pb._build_time_window_block(shot)
    assert "【时间窗】" in out
    assert "0-3s" in out and "3-6s" in out and "6-9s" in out
    assert "无切剪" in out
    from _video_gen.volcengine_provider import _classify_sentence_weight
    assert _classify_sentence_weight(out) == 2, "时间窗必须是低权重段"


def test_time_window_not_injected_for_normal_shot():
    shot = {"index": 1, "duration_sec": 9,
            "camera_movement": "固定机位",
            "motion_desc": "前3秒：甲拔剑；3-6秒：乙后退；6-9秒：甲收剑"}
    assert pb._build_time_window_block(shot) == ""


def test_concise_prompt_uses_time_window_instead_of_duplicate_segments():
    shot = {"index": 1, "duration_sec": 9,
            "camera_movement": "一条连续镜头，缓慢推进",
            "motion_desc": "前3秒：甲拔剑；3-6秒：乙后退；6-9秒：甲收剑"}
    prompt = pb._build_seedance_concise_prompt(shot)
    assert "【时间窗】" in prompt
    assert "【时间分段】" not in prompt


# ---------------------------------------------------------------------------
# T3 运镜门禁
# ---------------------------------------------------------------------------
def test_camera_stacking_conflicting_still_warns():
    issue = _camera_stacking_issue("固定机位 + 快速环绕")
    assert issue is not None and issue["reason"] == "conflicting"


def test_camera_stacking_time_window_continuous_passes():
    issue = _camera_stacking_issue(
        "一条连续镜头，0-5s 近景环绕，6-10s 中景跟拍，11-20s 缓慢拉远")
    assert issue is None


def test_camera_stacking_unordered_multi_move_warns():
    issue = _camera_stacking_issue("环绕镜，缓慢推近")
    assert issue is not None and issue["reason"] == "unordered_multi_move"


def test_camera_stacking_sequence_marker_passes():
    assert _camera_stacking_issue("先推近，随后环绕") is None


def test_camera_warning_keeps_report_status_pass(monkeypatch, tmp_path):
    from step02_video_gen import experiment_context
    from step02_video_gen.video_prompt_quality import collect_video_prompt_quality

    monkeypatch.setattr(pb, "_build_prompt",
                        lambda shot, script, mode="A": shot["prompt"])
    monkeypatch.setattr("shared.model_caps.active_video_is_seedance", lambda: False)
    monkeypatch.setattr("shared.paths.chapter_dir", lambda: tmp_path)
    # 本用例只关心运镜 warning 是否改变 blocking 语义；上游 Step01 门禁
    # 不在本测试范围（否则报告会被 upstream_blocked 覆盖）。
    monkeypatch.setattr(experiment_context, "build_step02_context",
                        lambda *a, **k: {})
    monkeypatch.setattr(experiment_context, "apply_upstream_override",
                        lambda payload: payload)
    script = {"shots": [
        {"index": 1, "scene": "A", "duration_sec": 5, "prompt": "固定机位 + 快速环绕"},
        {"index": 2, "scene": "A", "duration_sec": 5,
         "prompt": "一条连续镜头，0-5s 近景环绕，6-10s 中景跟拍，11-20s 缓慢拉远"},
    ]}
    result = collect_video_prompt_quality(script)
    stacking = [w for w in result["warnings"]
                if w["category"] == "video_camera_stacking"]
    assert result["status"] == "PASS"
    assert [w["shot"] for w in stacking] == [1], "矛盾镜警告，连续长镜放行"


# ---------------------------------------------------------------------------
# T4 类型化氛围
# ---------------------------------------------------------------------------
def test_atmosphere_enrichment_from_type_hint():
    out = pb._build_atmosphere_enrichment({"color_tone": "冷蓝与暗红"})
    assert "【氛围】" in out and "悬疑" in out
    from _video_gen.volcengine_provider import _classify_sentence_weight
    assert _classify_sentence_weight(out) == 2, "氛围必须是低权重段"


def test_atmosphere_enrichment_empty_without_type_hint():
    assert pb._build_atmosphere_enrichment({"color_tone": "中性"}) == ""
    assert pb._build_atmosphere_enrichment({}) == ""
