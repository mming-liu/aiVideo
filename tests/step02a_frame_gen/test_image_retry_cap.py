# -*- coding: utf-8 -*-
"""图片 provider 的**请求数上限**（S14 运行单的零调用前置）.

历史实现里 429/5xx 是 `continue` 且不计数，理论上可无限重发；按量试跑必须能把
单次调用钉死在 N 次 POST，失败即停、不自动重抽。
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _service(monkeypatch):
    import _image_gen.volcengine_provider as vp

    monkeypatch.setattr(vp, "VOLCENGINE_API_KEY", "test-key")
    monkeypatch.setattr(vp.config, "validate", lambda: None)
    return vp


def test_attempt_cap_bounds_posts_and_stops(monkeypatch):
    vp = _service(monkeypatch)
    from _image_gen.models import ImageGenerationRequest

    posts: list = []

    class _Resp:
        status_code = 500
        text = "server error"

    def _post(*a, **k):
        posts.append(1)
        return _Resp()

    monkeypatch.setattr(vp.requests, "post", _post)
    monkeypatch.setattr(vp.time, "sleep", lambda _s: None)
    monkeypatch.setenv("VOLCENGINE_IMAGE_MAX_ATTEMPTS", "2")

    svc = vp.VolcengineImageService()
    res = svc.generate(ImageGenerationRequest(prompt="p", shot_index=1, size="1024x1024"))

    assert res.success is False
    assert len(posts) == 2, "超过上限仍在重发：%d 次" % len(posts)
    assert "尝试上限" in (res.error or "")


def test_no_cap_keeps_legacy_behaviour(monkeypatch):
    """未设上限时保持历史行为：5xx 会继续重试（这里用一次成功后停止来收口）."""
    vp = _service(monkeypatch)
    from _image_gen.models import ImageGenerationRequest

    posts: list = []

    class _Ok:
        status_code = 200
        text = "{}"

        def json(self):
            return {"data": [{"url": "http://x/y.png"}]}

    def _post(*a, **k):
        posts.append(1)
        return _Ok()

    monkeypatch.setattr(vp.requests, "post", _post)
    monkeypatch.delenv("VOLCENGINE_IMAGE_MAX_ATTEMPTS", raising=False)
    monkeypatch.setattr(vp.VolcengineImageService, "_download",
                        lambda self, req, url, idx: vp.ImageGenerationResult(
                            success=True, save_path=str(req.save_path or ""),
                            image_url=url))

    svc = vp.VolcengineImageService()
    res = svc.generate(ImageGenerationRequest(prompt="p", shot_index=1, size="1024x1024"))
    assert res.success is True
    assert len(posts) == 1

def test_sensitive_retry_respects_the_attempt_cap(monkeypatch):
    """上限 1 + 敏感响应：改写提示后的第二次 POST 必须被**发送前**拦下."""
    vp = _service(monkeypatch)
    from _image_gen.models import ImageGenerationRequest

    posts: list = []

    class _Sensitive:
        status_code = 400
        text = '{"error":{"code":"InputTextSensitiveContentDetected"}}'

    def _post(*a, **k):
        posts.append(1)
        return _Sensitive()

    monkeypatch.setattr(vp.requests, "post", _post)
    monkeypatch.setattr("shared.content_safety.input_sensitive_retry_prompt",
                        lambda _p: "sanitized prompt")
    monkeypatch.setenv("VOLCENGINE_IMAGE_MAX_ATTEMPTS", "1")

    svc = vp.VolcengineImageService()
    res = svc.generate(ImageGenerationRequest(prompt="p", shot_index=1, size="1024x1024"))

    assert res.success is False
    assert len(posts) == 1, "敏感重试绕过了上限：%d 次 POST" % len(posts)
    assert "尝试上限" in (res.error or "")


def test_process_post_budget_blocks_later_calls(monkeypatch):
    """整批预算：第 2 次 generate 在发送前被拦，总 POST 不超过预算."""
    vp = _service(monkeypatch)
    from _image_gen.models import ImageGenerationRequest

    posts: list = []

    class _Resp:
        status_code = 500
        text = "server error"

    def _post(*a, **k):
        posts.append(1)
        return _Resp()

    monkeypatch.setattr(vp.requests, "post", _post)
    monkeypatch.setattr(vp.time, "sleep", lambda _s: None)
    monkeypatch.setattr(vp, "_POSTS_USED", [0])
    monkeypatch.setenv("VOLCENGINE_IMAGE_MAX_POSTS", "1")
    monkeypatch.setenv("VOLCENGINE_IMAGE_MAX_ATTEMPTS", "1")

    svc = vp.VolcengineImageService()
    first = svc.generate(ImageGenerationRequest(prompt="p", shot_index=1, size="1024x1024"))
    second = svc.generate(ImageGenerationRequest(prompt="p", shot_index=2, size="1024x1024"))

    assert first.success is False and second.success is False
    assert len(posts) == 1, "批次预算被绕过：%d 次 POST" % len(posts)
    assert "批次 POST 上限" in (second.error or "")

def test_batch_abort_blocks_followup_generate(monkeypatch):
    """FAIL_FAST：首次失败后，后续 generate 在发送前被拒（0 次追加 POST）."""
    vp = _service(monkeypatch)
    from _image_gen.models import ImageGenerationRequest

    posts: list = []

    class _Resp:
        status_code = 500
        text = "server error"

    def _post(*a, **k):
        posts.append(1)
        return _Resp()

    monkeypatch.setattr(vp.requests, "post", _post)
    monkeypatch.setattr(vp.time, "sleep", lambda _s: None)
    monkeypatch.setattr(vp, "_POSTS_USED", [0])
    monkeypatch.setattr(vp, "_ABORT", [None])
    monkeypatch.setenv("VOLCENGINE_IMAGE_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("VOLCENGINE_IMAGE_FAIL_FAST", "1")

    svc = vp.VolcengineImageService()
    first = svc.generate(ImageGenerationRequest(prompt="p", shot_index=1, size="1024x1024"))
    second = svc.generate(ImageGenerationRequest(prompt="p", shot_index=2, size="1024x1024"))

    assert first.success is False
    assert second.success is False
    assert len(posts) == 1, "失败后仍继续发送：%d 次 POST" % len(posts)
    assert "本批已中止" in (second.error or "")


def test_portrait_batch_fail_fast_stops_after_first_failure(tmp_path, monkeypatch):
    """实际批次入口：第一个视角失败即中止，不再请求后续视角."""
    import asyncio

    from shared import vimax_bridge as vb

    calls: list = []

    class _Adapter:
        async def generate_single_image(self, **kwargs):
            calls.append(kwargs)
            raise RuntimeError("stub failure")

    class _Bank:
        def has_character(self, _ident):
            return False

        def get_portrait_dir(self, ident):
            return tmp_path / ident

        def save(self):
            pass

    monkeypatch.setenv("STEP02A_FAIL_FAST", "1")
    monkeypatch.setattr("shared.vimax_adapters.ImageGeneratorAiVideoAdapter",
                        lambda **_k: _Adapter())

    try:
        asyncio.run(vb._generate_missing_portraits(
            [{"identifier": "甲"}], _Bank(), object(), 1))
    except RuntimeError as exc:
        assert "stub failure" in str(exc)
    else:
        raise AssertionError("fail-fast 没有中止批次")

    assert len(calls) == 1, "首个视角失败后仍请求了后续视角：%d 次" % len(calls)

def test_output_sensitive_soft_skip_also_aborts(monkeypatch):
    """输出敏感软跳过（soft_skip）同样算失败：后续 generate 0 次追加 POST."""
    vp = _service(monkeypatch)
    from _image_gen.models import ImageGenerationRequest

    posts: list = []

    class _Sensitive:
        status_code = 400
        text = '{"error":{"code":"OutputImageSensitiveContentDetected"}}'

    def _post(*a, **k):
        posts.append(1)
        return _Sensitive()

    monkeypatch.setattr(vp.requests, "post", _post)
    monkeypatch.setattr(vp, "_POSTS_USED", [0])
    monkeypatch.setattr(vp, "_ABORT", [None])
    monkeypatch.setenv("VOLCENGINE_IMAGE_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("VOLCENGINE_IMAGE_FAIL_FAST", "1")

    svc = vp.VolcengineImageService()
    first = svc.generate(ImageGenerationRequest(prompt="p", shot_index=1, size="1024x1024"))
    second = svc.generate(ImageGenerationRequest(prompt="p", shot_index=2, size="1024x1024"))

    assert first.success is False and first.soft_skip is True
    assert len(posts) == 1, "软跳过后仍继续发送：%d 次" % len(posts)
    assert "本批已中止" in (second.error or "")


def test_download_failure_also_aborts(monkeypatch):
    """HTTP 200 但下载失败：同样设置中止标记，后续 generate 0 次追加 POST."""
    vp = _service(monkeypatch)
    from _image_gen.models import ImageGenerationRequest

    posts: list = []

    class _Ok:
        status_code = 200
        text = "{}"

        def json(self):
            return {"data": [{"url": "http://x/y.png"}]}

    def _post(*a, **k):
        posts.append(1)
        return _Ok()

    monkeypatch.setattr(vp.requests, "post", _post)
    monkeypatch.setattr(vp, "_POSTS_USED", [0])
    monkeypatch.setattr(vp, "_ABORT", [None])
    monkeypatch.setenv("VOLCENGINE_IMAGE_FAIL_FAST", "1")

    svc = vp.VolcengineImageService()
    monkeypatch.setattr(svc, "_download", lambda req, url, idx: vp.ImageGenerationResult(
        success=False, error="download failed"))

    first = svc.generate(ImageGenerationRequest(prompt="p", shot_index=1, size="1024x1024"))
    second = svc.generate(ImageGenerationRequest(prompt="p", shot_index=2, size="1024x1024"))

    assert first.success is False
    assert len(posts) == 1, "下载失败后仍继续发送：%d 次" % len(posts)
    assert "本批已中止" in (second.error or "")


def test_soft_skip_aborts_the_portrait_batch(tmp_path, monkeypatch):
    """实际批次入口：首视角软跳过（适配器抛 SOFT-SKIP）即中止，不再请求后续视角."""
    import asyncio

    from shared import vimax_bridge as vb

    calls: list = []

    class _Adapter:
        async def generate_single_image(self, **kwargs):
            calls.append(kwargs)
            raise RuntimeError("[SOFT-SKIP] 图像因内容安全策略跳过")

    class _Bank:
        def has_character(self, _ident):
            return False

        def get_portrait_dir(self, ident):
            return tmp_path / ident

        def save(self):
            pass

    monkeypatch.setenv("STEP02A_FAIL_FAST", "1")
    monkeypatch.setattr("shared.vimax_adapters.ImageGeneratorAiVideoAdapter",
                        lambda **_k: _Adapter())

    try:
        asyncio.run(vb._generate_missing_portraits(
            [{"identifier": "甲"}], _Bank(), object(), 1))
    except RuntimeError as exc:
        assert "SOFT-SKIP" in str(exc)
    else:
        raise AssertionError("软跳过没有中止批次")

    assert len(calls) == 1, "软跳过后仍请求了后续视角：%d 次" % len(calls)

def test_no_auto_repair_short_circuits(monkeypatch):
    """STEP02A_NO_REPAIR=1：QC critical 也不发修图请求（发送前禁止）."""
    import asyncio

    from shared import vimax_bridge as vb

    class _Adapter:
        async def generate_single_image(self, **kwargs):
            raise AssertionError("禁用修图后仍发起了 i2i 请求")

    monkeypatch.setenv("STEP02A_NO_REPAIR", "1")
    out = asyncio.run(vb._try_qc_repair(_Adapter(), {}, "frame.png", "first_frame",
                                       [], False, 1))
    assert out is False
