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
