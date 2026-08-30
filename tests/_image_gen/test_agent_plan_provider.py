# -*- coding: utf-8 -*-
"""2026-08-30 Agent Plan (/api/plan/v3) 供应商接入单测.

覆盖：
  1. 注册：service._auto_register() 把 agent_plan 写入注册表
  2. key 缺失：构造时 ValueError 文案含「AGENT_PLAN_API_KEY」「专属 key」
  3. 图片提交：mock POST /images/generations 验证 payload + 200 解析 + 下载落盘
  4. 图片 401：失败文案含「Agent Plan 鉴权失败」+ 提示申请专属 key
  5. 图片 403：失败文案含「套餐无权限或燃料耗尽」
  6. 图片尾帧/关键帧命名
  7. 视频提交 — 首帧：content 数组含 first_frame role
  8. 视频提交 — 首尾帧：content 数组含 first_frame + last_frame
  9. 视频提交 — r2v：content 数组含 reference_image
  10. 视频轮询 succeeded：下载 video_url 落盘到 vimax_working/shots/<idx>/video.mp4
  11. 视频轮询 failed：返回 error 含「生成失败」
  12. 视频轮询超时：返回 error 含「轮询超时」
  13. 并发护栏：信号量 value 与 AGENT_PLAN_MAX_CONCURRENCY 一致
  14. ref_compose 注册：active_image_ref_limit 在 agent_plan provider 下解析为 3
  15. model_caps：active_video_model/duration_cap/supports_audio/is_seedance 正确

设计原则：mock HTTP、不真调 API；与 volcengine provider 测试同源。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ───────────────────────── 注册测试 ─────────────────────────


def test_image_service_registered():
    """agent_plan 必须在 _image_gen/service._REGISTRY 中."""
    from _image_gen import service
    service._auto_register()
    assert "agent_plan" in service._REGISTRY
    from _image_gen.agent_plan_provider import AgentPlanImageService
    assert service._REGISTRY["agent_plan"] is AgentPlanImageService


def test_video_service_registered():
    """agent_plan 必须在 _video_gen/service._REGISTRY 中."""
    from _video_gen import service
    service._auto_register()
    assert "agent_plan" in service._REGISTRY
    from _video_gen.agent_plan_provider import AgentPlanVideoService
    assert service._REGISTRY["agent_plan"] is AgentPlanVideoService


# ───────────────────────── 配置 + key 缺失 fail-fast ─────────────────────────


def test_image_service_missing_key_raises(monkeypatch, tmp_path):
    """key 缺失时 ValueError 文案必须含「AGENT_PLAN_API_KEY」+「专属 key」提示."""
    from _image_gen import agent_plan_provider as ap_mod
    from shared import config as shared_config
    monkeypatch.setattr(shared_config, "AGENT_PLAN_API_KEY", "")
    monkeypatch.setattr(ap_mod, "AGENT_PLAN_API_KEY", "")
    from _image_gen import config as _gencfg
    _gencfg.OUTPUT_DIR = str(tmp_path)

    with pytest.raises(ValueError) as excinfo:
        ap_mod.AgentPlanImageService()
    msg = str(excinfo.value)
    assert "AGENT_PLAN_API_KEY" in msg
    assert "专属 key" in msg


def test_video_service_missing_key_raises(monkeypatch, tmp_path):
    from _video_gen import agent_plan_provider as ap_mod
    from shared import config as shared_config
    monkeypatch.setattr(shared_config, "AGENT_PLAN_API_KEY", "")
    monkeypatch.setattr(ap_mod, "AGENT_PLAN_API_KEY", "")
    from _video_gen import config as _gencfg
    _gencfg.OUTPUT_DIR = str(tmp_path)

    with pytest.raises(ValueError) as excinfo:
        ap_mod.AgentPlanVideoService()
    msg = str(excinfo.value)
    assert "AGENT_PLAN_API_KEY" in msg
    assert "专属 key" in msg


def test_config_values():
    """shared/config.py 已暴露 AGENT_PLAN_* 配置项（默认值含 base_url / model）."""
    from shared import config
    assert hasattr(config, "AGENT_PLAN_API_KEY")
    assert hasattr(config, "AGENT_PLAN_BASE_URL")
    assert "plan/v3" in config.AGENT_PLAN_BASE_URL
    assert hasattr(config, "AGENT_PLAN_T2I_MODEL")
    assert hasattr(config, "AGENT_PLAN_T2V_MODEL")
    assert hasattr(config, "AGENT_PLAN_VIDEO_POLL_INTERVAL")
    assert hasattr(config, "AGENT_PLAN_VIDEO_MAX_POLL_ATTEMPTS")
    assert hasattr(config, "AGENT_PLAN_MAX_CONCURRENCY")
    assert config.AGENT_PLAN_MAX_CONCURRENCY >= 1


# ───────────────────────── 图片 provider ─────────────────────────


def _make_image_service(monkeypatch, tmp_path):
    """构造一个最小可用的 AgentPlanImageService（注入假 key + tmp 输出目录）.

    注意：shared.config 和 agent_plan_provider 都在 import 时把 AGENT_PLAN_API_KEY
    绑到模块级常量，所以 monkeypatch.setenv 不够，必须 setattr 两边都打补丁。
    """
    from _image_gen import agent_plan_provider as ap_mod
    from shared import config as shared_config
    monkeypatch.setattr(shared_config, "AGENT_PLAN_API_KEY", "test-key")
    monkeypatch.setattr(ap_mod, "AGENT_PLAN_API_KEY", "test-key")
    monkeypatch.setattr(ap_mod, "AGENT_PLAN_BASE_URL", "http://fake")
    from _image_gen import config as _gencfg
    _gencfg.OUTPUT_DIR = str(tmp_path)
    return ap_mod.AgentPlanImageService()


def test_image_submit_basic_payload(monkeypatch, tmp_path):
    """基本提交：验证 POST URL + payload 形态 + 200 解析 + 落盘路径."""
    svc = _make_image_service(monkeypatch, tmp_path)

    # 必须 > 5KB（provider 端有 "下载内容过小" 兜底）
    fake_img_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * (8 * 1024)
    submit_resp = MagicMock(status_code=200, text='{"data":[{"url":"http://x/y.png"}]}')
    submit_resp.json.return_value = {"data": [{"url": "http://x/y.png"}]}
    download_resp = MagicMock(status_code=200, content=fake_img_bytes)

    from _image_gen import models
    req = models.ImageGenerationRequest(
        prompt="test prompt", shot_index=5, frame_desc="首帧", size="1024x1792"
    )

    with patch("requests.post", return_value=submit_resp) as mock_post, \
         patch("requests.get", return_value=download_resp):
        result = svc.generate(req)

    assert result.success is True, f"expected success, got error: {result.error}"
    assert result.save_path is not None
    assert Path(result.save_path).name == "shot_5_ff.png"
    assert Path(result.save_path).exists()

    # 验证 POST URL 走 /images/generations 通道
    call_args = mock_post.call_args
    assert "/images/generations" in call_args[0][0]
    # 测试用 http://fake 简化 mock；正常 default 是 https://ark.cn-beijing.volces.com/api/plan/v3
    # 验请求头含 Bearer token
    assert call_args.kwargs["headers"]["Authorization"] == "Bearer test-key"
    payload = call_args.kwargs.get("json") or call_args[1].get("json")
    assert payload["model"] == "doubao-seedream-5-0-lite"
    assert payload["prompt"] == "test prompt"
    assert payload["size"] == "1024x1792"
    assert payload["response_format"] == "url"
    assert payload["watermark"] is False
    # 无参考图时不带 image 字段
    assert "image" not in payload


def test_image_submit_401_returns_auth_error(monkeypatch, tmp_path):
    """401 → error 文案含「Agent Plan 鉴权失败」+ 提示申请专属 key."""
    svc = _make_image_service(monkeypatch, tmp_path)
    resp = MagicMock(status_code=401, text='{"error":{"code":"Unauthorized"}}')

    from _image_gen import models
    req = models.ImageGenerationRequest(prompt="x", shot_index=1)
    with patch("requests.post", return_value=resp):
        result = svc.generate(req)

    assert result.success is False
    assert "Agent Plan 鉴权失败" in result.error
    assert "专属 key" in result.error


def test_image_submit_403_returns_quota_error(monkeypatch, tmp_path):
    """403 → error 文案含「套餐无权限或燃料耗尽」."""
    svc = _make_image_service(monkeypatch, tmp_path)
    resp = MagicMock(status_code=403, text='{"error":"quota exhausted"}')

    from _image_gen import models
    req = models.ImageGenerationRequest(prompt="x", shot_index=1)
    with patch("requests.post", return_value=resp):
        result = svc.generate(req)

    assert result.success is False
    assert "套餐无权限" in result.error
    assert "燃料耗尽" in result.error


def test_image_lf_frame_naming(monkeypatch, tmp_path):
    """尾帧 → shot_<idx>_lf.png."""
    svc = _make_image_service(monkeypatch, tmp_path)
    fake = b"\x89PNG\r\n\x1a\n" + b"\x00" * (8 * 1024)
    submit = MagicMock(status_code=200)
    submit.json.return_value = {"data": [{"url": "http://x/y.png"}]}
    download = MagicMock(status_code=200, content=fake)

    from _image_gen import models
    req = models.ImageGenerationRequest(prompt="x", shot_index=3, frame_desc="尾帧")
    with patch("requests.post", return_value=submit), \
         patch("requests.get", return_value=download):
        result = svc.generate(req)

    assert result.success is True, f"got error: {result.error}"
    assert Path(result.save_path).name == "shot_3_lf.png"


def test_image_kf_frame_naming(monkeypatch, tmp_path):
    """关键帧2 → shot_<idx>_kf_2.png."""
    svc = _make_image_service(monkeypatch, tmp_path)
    fake = b"\x89PNG\r\n\x1a\n" + b"\x00" * (8 * 1024)
    submit = MagicMock(status_code=200)
    submit.json.return_value = {"data": [{"url": "http://x/y.png"}]}
    download = MagicMock(status_code=200, content=fake)

    from _image_gen import models
    req = models.ImageGenerationRequest(prompt="x", shot_index=7, frame_desc="关键帧2")
    with patch("requests.post", return_value=submit), \
         patch("requests.get", return_value=download):
        result = svc.generate(req)

    assert result.success is True, f"got error: {result.error}"
    assert Path(result.save_path).name == "shot_7_kf_2.png"


def test_image_custom_save_path(monkeypatch, tmp_path):
    """save_path 显式传入时直接落盘该路径（资产图/肖像场景）."""
    svc = _make_image_service(monkeypatch, tmp_path)
    fake = b"\x89PNG\r\n\x1a\n" + b"\x00" * (8 * 1024)
    submit = MagicMock(status_code=200)
    submit.json.return_value = {"data": [{"url": "http://x/y.png"}]}
    download = MagicMock(status_code=200, content=fake)

    custom_path = tmp_path / "asset" / "portrait.png"
    from _image_gen import models
    req = models.ImageGenerationRequest(
        prompt="x", shot_index=99, save_path=str(custom_path)
    )
    with patch("requests.post", return_value=submit), \
         patch("requests.get", return_value=download):
        result = svc.generate(req)

    assert result.success is True, f"got error: {result.error}"
    assert result.save_path == str(custom_path)
    assert custom_path.exists()


# ───────────────────────── 视频 provider ─────────────────────────


def _make_video_service(monkeypatch, tmp_path):
    from _video_gen import agent_plan_provider as ap_mod
    from shared import config as shared_config
    monkeypatch.setattr(shared_config, "AGENT_PLAN_API_KEY", "test-key")
    monkeypatch.setattr(ap_mod, "AGENT_PLAN_API_KEY", "test-key")
    monkeypatch.setattr(ap_mod, "AGENT_PLAN_BASE_URL", "http://fake")
    # POLL_INTERVAL 不能为 0——否则 provider 内 timeout = MAX*INTERVAL = 0，
    # 循环条件 time.time()-start < 0 永真不进，3 次 attempt 都跳过直接 timeout
    # 测试用 1s + patch time.sleep 跳过快进
    monkeypatch.setattr(shared_config, "AGENT_PLAN_VIDEO_POLL_INTERVAL", 1)
    monkeypatch.setattr(ap_mod, "AGENT_PLAN_VIDEO_POLL_INTERVAL", 1)
    monkeypatch.setattr(shared_config, "AGENT_PLAN_VIDEO_MAX_POLL_ATTEMPTS", 3)
    monkeypatch.setattr(ap_mod, "AGENT_PLAN_VIDEO_MAX_POLL_ATTEMPTS", 3)
    monkeypatch.setattr(shared_config, "AGENT_PLAN_MAX_CONCURRENCY", 2)
    monkeypatch.setattr(ap_mod, "AGENT_PLAN_MAX_CONCURRENCY", 2)
    from _video_gen import config as _gencfg
    _gencfg.OUTPUT_DIR = str(tmp_path)
    return ap_mod.AgentPlanVideoService()


def _fake_png(tmp_path, name="ref.png") -> str:
    """生成一张占位 png 文件并返回路径（供 _encode_media 解析）."""
    p = tmp_path / name
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 256)
    return str(p)


def test_video_submit_first_frame_only(monkeypatch, tmp_path):
    """I2V 模式 — 仅 image_path → content 数组含 first_frame role."""
    svc = _make_video_service(monkeypatch, tmp_path)
    ff = _fake_png(tmp_path, "ff.png")

    submit = MagicMock(status_code=200, text='{"id":"task-1"}')
    submit.json.return_value = {"id": "task-1"}

    from _video_gen import models
    req = models.VideoGenerationRequest(
        prompt="test", clip_index=1, image_path=ff, size="720x1280"
    )
    with patch("requests.post", return_value=submit) as mock_post:
        task_id = svc.create_task(req)

    assert task_id == "task-1"
    payload = mock_post.call_args.kwargs["json"]
    assert payload["model"] == "doubao-seedance-2-0"
    # content: text + image(first_frame)
    assert any(
        c.get("role") == "first_frame" for c in payload["content"]
    ), f"content 缺 first_frame: {payload['content']}"
    assert not any(
        c.get("role") in ("last_frame", "reference_image") for c in payload["content"]
    )


def test_video_submit_first_last_frame(monkeypatch, tmp_path):
    """I2V 首尾帧 — image_path + last_frame_image_path → first_frame + last_frame."""
    svc = _make_video_service(monkeypatch, tmp_path)
    ff = _fake_png(tmp_path, "ff.png")
    lf = _fake_png(tmp_path, "lf.png")

    submit = MagicMock(status_code=200)
    submit.json.return_value = {"id": "task-2"}

    from _video_gen import models
    req = models.VideoGenerationRequest(
        prompt="test", clip_index=1, image_path=ff, last_frame_image_path=lf,
        size="720x1280"
    )
    with patch("requests.post", return_value=submit) as mock_post:
        svc.create_task(req)

    payload = mock_post.call_args.kwargs["json"]
    roles = [c.get("role") for c in payload["content"]]
    assert "first_frame" in roles
    assert "last_frame" in roles


def test_video_submit_r2v_reference(monkeypatch, tmp_path):
    """r2v — reference_paths 全部走 reference_image role."""
    svc = _make_video_service(monkeypatch, tmp_path)
    ref1 = _fake_png(tmp_path, "r1.png")
    ref2 = _fake_png(tmp_path, "r2.png")

    submit = MagicMock(status_code=200)
    submit.json.return_value = {"id": "task-3"}

    from _video_gen import models
    req = models.VideoGenerationRequest(
        prompt="test", clip_index=1, reference_paths=[ref1, ref2], size="720x1280"
    )
    with patch("requests.post", return_value=submit) as mock_post:
        svc.create_task(req)

    payload = mock_post.call_args.kwargs["json"]
    roles = [c.get("role") for c in payload["content"]]
    assert roles.count("reference_image") == 2
    # r2v 模式下不混入 first_frame / last_frame
    assert "first_frame" not in roles
    assert "last_frame" not in roles


def test_video_submit_extra_keyframes_to_first_last(monkeypatch, tmp_path):
    """多关键帧在不支持 reference_image 的模型上退化为首尾帧.

    改用 supports_reference_image=False 的 1.5-pro 测试降级路径。
    """
    svc = _make_video_service(monkeypatch, tmp_path)
    # 把 model 改为 1.5-pro（不支持 reference_image）
    svc._model = "doubao-seedance-1-5-pro-251215"
    k1 = _fake_png(tmp_path, "k1.png")
    k2 = _fake_png(tmp_path, "k2.png")
    k3 = _fake_png(tmp_path, "k3.png")

    submit = MagicMock(status_code=200)
    submit.json.return_value = {"id": "task-4"}

    from _video_gen import models
    req = models.VideoGenerationRequest(
        prompt="test", clip_index=1,
        extra_keyframe_paths=[k1, k2, k3], size="720x1280"
    )
    with patch("requests.post", return_value=submit) as mock_post:
        svc.create_task(req)

    payload = mock_post.call_args.kwargs["json"]
    roles = [c.get("role") for c in payload["content"]]
    assert "first_frame" in roles
    assert "last_frame" in roles
    # 退化后不应再有 reference_image
    assert "reference_image" not in roles


def test_video_poll_succeeded_downloads(monkeypatch, tmp_path):
    """succeeded → 下载 video_url 落盘到 vimax_working/shots/<idx>/video.mp4."""
    svc = _make_video_service(monkeypatch, tmp_path)
    fake_video = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * (40 * 1024)
    query = MagicMock(status_code=200)
    query.json.return_value = {
        "status": "succeeded",
        "content": {"video_url": "http://x/v.mp4"},
    }
    download = MagicMock(status_code=200, content=fake_video)

    from _video_gen import models
    req = models.VideoGenerationRequest(
        prompt="test", clip_index=8, size="720x1280", duration=5
    )
    with patch.object(svc, "query_task", return_value=query.json.return_value), \
         patch("requests.get", return_value=download):
        result = svc.wait_for_completion(req, "task-x")

    assert result.success is True, f"got error: {result.error}"
    assert result.save_path is not None
    # 跨平台路径：用 os.sep 检查；Windows 下 vimax_working_dir 返回的反斜杠
    assert "shots" in result.save_path and "8" in result.save_path
    assert result.save_path.endswith("video.mp4")
    assert Path(result.save_path).exists()


def test_video_poll_failed_returns_error(monkeypatch, tmp_path):
    """failed → error 含「生成失败」."""
    svc = _make_video_service(monkeypatch, tmp_path)
    query = MagicMock(status_code=200)
    query.json.return_value = {
        "status": "failed",
        "error": {"code": "InvalidInput", "message": "bad prompt"},
    }

    from _video_gen import models
    req = models.VideoGenerationRequest(prompt="x", clip_index=1)
    with patch.object(svc, "query_task", return_value=query.json.return_value):
        result = svc.wait_for_completion(req, "task-y")

    assert result.success is False
    assert "生成失败" in result.error
    assert "InvalidInput" in result.error


def test_video_poll_timeout(monkeypatch, tmp_path):
    """始终 running → 轮询耗尽 → error 含「轮询超时」."""
    svc = _make_video_service(monkeypatch, tmp_path)
    query = MagicMock(status_code=200)
    query.json.return_value = {"status": "running"}

    from _video_gen import models
    req = models.VideoGenerationRequest(prompt="x", clip_index=1)
    with patch.object(svc, "query_task", return_value=query.json.return_value), \
         patch("time.sleep", return_value=None):
        result = svc.wait_for_completion(req, "task-z")

    assert result.success is False
    assert "轮询超时" in result.error


def test_video_submit_401_raises(monkeypatch, tmp_path):
    """401 不重试直接抛错."""
    svc = _make_video_service(monkeypatch, tmp_path)
    resp = MagicMock(status_code=401, text='{"error":"unauthorized"}')

    from _video_gen import models
    req = models.VideoGenerationRequest(prompt="x", clip_index=1)
    with patch("requests.post", return_value=resp):
        with pytest.raises(RuntimeError) as excinfo:
            svc.create_task(req)
    assert "401" in str(excinfo.value)


def test_video_5xx_retries_then_raises(monkeypatch, tmp_path):
    """5xx 走指数退避，重试用尽后抛错."""
    svc = _make_video_service(monkeypatch, tmp_path)
    resp = MagicMock(status_code=503, text='{"error":"busy"}')

    from _video_gen import models
    req = models.VideoGenerationRequest(prompt="x", clip_index=1)
    with patch("requests.post", return_value=resp), \
         patch("time.sleep", return_value=None):
        with pytest.raises(RuntimeError) as excinfo:
            svc.create_task(req)
    assert "503" in str(excinfo.value)


def test_video_submit_payload_resolution_ratio(monkeypatch, tmp_path):
    """resolution/ratio 推导正确：720x1280 → 720p / 9:16."""
    svc = _make_video_service(monkeypatch, tmp_path)
    submit = MagicMock(status_code=200)
    submit.json.return_value = {"id": "task-r"}

    from _video_gen import models
    req = models.VideoGenerationRequest(
        prompt="x", clip_index=1, size="720x1280", duration=6
    )
    with patch("requests.post", return_value=submit) as mock_post:
        svc.create_task(req)

    payload = mock_post.call_args.kwargs["json"]
    assert payload["resolution"] == "720p"
    assert payload["ratio"] == "9:16"
    assert payload["duration"] == 6
    # Seedance 2-0 支持 generate_audio，按 VIDEO_MODEL_VOICE 默认 1 → 开启
    assert payload.get("generate_audio") is True


# ───────────────────────── 并发护栏 ─────────────────────────


def test_video_semaphore_value_matches_env(monkeypatch, tmp_path):
    """信号量 value = AGENT_PLAN_MAX_CONCURRENCY."""
    from _video_gen import agent_plan_provider as ap_mod
    from shared import config as shared_config
    monkeypatch.setattr(shared_config, "AGENT_PLAN_API_KEY", "test-key")
    monkeypatch.setattr(ap_mod, "AGENT_PLAN_API_KEY", "test-key")
    monkeypatch.setattr(shared_config, "AGENT_PLAN_MAX_CONCURRENCY", 3)
    monkeypatch.setattr(ap_mod, "AGENT_PLAN_MAX_CONCURRENCY", 3)
    from _video_gen import config as _gencfg
    _gencfg.OUTPUT_DIR = str(tmp_path)

    svc = ap_mod.AgentPlanVideoService()
    assert svc._semaphore._value == 3


# ───────────────────────── 能力注册表 ─────────────────────────


def test_ref_compose_agent_plan_limit():
    """Agent Plan 默认 doubao-seedream-5-0-lite 参考图上限 = 3（免费档对齐）."""
    from shared import ref_compose
    limit = ref_compose.get_ref_limit("doubao-seedream-5-0-lite")
    assert limit == 3


def test_active_image_ref_limit_agent_plan(monkeypatch):
    """active_image_ref_limit 在 IMAGE_PROVIDER=agent_plan 时返回 3."""
    from shared import ref_compose
    from shared import config as _shared_config
    monkeypatch.setattr(_shared_config, "IMAGE_PROVIDER", "agent_plan")
    limit = ref_compose.active_image_ref_limit()
    assert limit == 3


def test_seedance_2_0_caps():
    """doubao-seedance-2-0 命中 seedance-2-0 能力（4-15s/支持 ref_image/audio）."""
    from shared.model_caps import get_seedance_cap
    cap = get_seedance_cap("doubao-seedance-2-0")
    assert cap.duration_range == (4, 15)
    assert cap.supports_reference_image is True
    assert cap.supports_generate_audio is True


def test_active_video_model_agent_plan(monkeypatch):
    """active_video_model 在 VIDEO_PROVIDER=agent_plan 时返回 AGENT_PLAN_T2V_MODEL."""
    from shared import model_caps
    monkeypatch.setenv("VIDEO_PROVIDER", "agent_plan")
    monkeypatch.setenv("AGENT_PLAN_T2V_MODEL", "doubao-seedance-2-0")
    model = model_caps.active_video_model()
    assert model == "doubao-seedance-2-0"


def test_active_video_duration_cap_agent_plan(monkeypatch):
    """active_video_duration_cap 在 agent_plan + doubao-seedance-2-0 时 = 15."""
    from shared import model_caps
    monkeypatch.setenv("VIDEO_PROVIDER", "agent_plan")
    monkeypatch.setenv("AGENT_PLAN_T2V_MODEL", "doubao-seedance-2-0")
    assert model_caps.active_video_duration_cap() == 15


def test_active_video_supports_audio_agent_plan(monkeypatch):
    """agent_plan + doubao-seedance-2-0 自带 generate_audio → True."""
    from shared import model_caps
    monkeypatch.setenv("VIDEO_PROVIDER", "agent_plan")
    monkeypatch.setenv("AGENT_PLAN_T2V_MODEL", "doubao-seedance-2-0")
    assert model_caps.active_video_supports_audio() is True


def test_active_video_is_seedance_agent_plan(monkeypatch):
    """active_video_is_seedance 在 agent_plan provider 下也返回 True（同 Seedance 系）."""
    from shared import model_caps
    monkeypatch.setenv("VIDEO_PROVIDER", "agent_plan")
    assert model_caps.active_video_is_seedance() is True
