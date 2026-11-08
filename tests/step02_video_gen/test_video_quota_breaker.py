# -*- coding: utf-8 -*-
"""视频 403 额度耗尽：写入熔断标记并阻止后续 POST（2026-11-08）."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def test_quota_403_breakers_future_posts(monkeypatch, tmp_path):
    import _video_gen.volcengine_provider as vp
    from shared import post_budget

    monkeypatch.setenv("STEP02B_VIDEO_BUDGET_FILE", str(tmp_path / "video_budget.json"))
    monkeypatch.setenv("STEP02B_VIDEO_BUDGET_TOTAL", "10")
    post_budget.reset("video")
    monkeypatch.setattr(vp, "VOLCENGINE_API_KEY", "test-key")
    monkeypatch.setattr(vp.config, "validate", lambda: None)

    class _Resp:
        status_code = 403
        text = '{"error":{"code":"SetLimitExceeded","message":"quota"}}'
        headers = {}

        def json(self):
            return {}

    posts: list = []

    def _post(*a, **k):
        posts.append(1)
        return _Resp()

    monkeypatch.setattr(vp.requests, "post", _post)
    svc = vp.VolcengineVideoService()
    monkeypatch.setattr(svc, "_materialize_content", lambda *a, **k: [])
    from _video_gen.models import PreparedVideoRequest

    prepared = PreparedVideoRequest(
        request_id="r1", provider="volcengine", model_id="m", mode="i2v",
        media_mode="image", prompt="p", parameters={"clip_index": 1})

    with pytest.raises(Exception):
        svc.create_prepared_task(prepared)
    assert len(posts) == 1, "首次 403 应恰好 1 个 POST：%d" % len(posts)
    assert post_budget.quota_exhausted("video"), "403 额度错误必须写入熔断标记"

    with pytest.raises(Exception):
        svc.create_prepared_task(prepared)
    assert len(posts) == 1, "熔断后仍发了新 POST：%d" % len(posts)
    post_budget.reset("video")
