# -*- coding: utf-8 -*-
from __future__ import annotations
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def test_ask_uses_go_endpoint_and_session_header(monkeypatch):
    import tools.vision_check as vc

    seen = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}],
                    "usage": {"opencode": {"billing_source": "go"}}}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, headers=headers, payload=json)
        return _Resp()

    monkeypatch.setattr("requests.post", fake_post)
    monkeypatch.setattr(vc, "_api_key", lambda: "oc_sk_test")
    monkeypatch.setattr(vc, "encode_image", lambda *a, **k: "AAAA")

    out = vc.ask("q", [("x.png", "face")])

    assert out["status"] == 200
    assert seen["url"] == "https://opencode.ai/zen/go/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer oc_sk_test"
    assert seen["headers"]["x-opencode-session"]
    assert seen["headers"]["User-Agent"].startswith("aiVideo-vision/")
    assert seen["payload"]["model"] == "glm-5.3-flash"
    assert seen["payload"]["messages"][0]["content"][0]["text"] == "q"
