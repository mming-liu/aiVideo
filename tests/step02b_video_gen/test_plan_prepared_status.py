# -*- coding: utf-8 -*-
from __future__ import annotations
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def test_unsupported_prepared_is_pending_not_ready(tmp_path, monkeypatch):
    from PIL import Image
    from _video_gen.models import PreparedVideoRequest
    from step02b_video_gen import main

    shot_dir = tmp_path / "shots" / "1"
    shot_dir.mkdir(parents=True)
    Image.new("RGB", (8, 8), "white").save(shot_dir / "first_frame.png")
    Image.new("RGB", (8, 8), "white").save(shot_dir / "last_frame.png")

    class _Svc:
        def supports_prepared(self):
            return True

        def prepare_request(self, req, context=None):
            return PreparedVideoRequest.unsupported("stub", "not adapted")

    monkeypatch.setattr("_video_gen.create_service", lambda provider=None: _Svc())
    script = {"shots": [{"index": 1, "ff_desc": "甲", "lf_desc": "乙",
                         "camera_movement": "固定机位", "motion_desc": "镜头掠过"}]}
    plan = main.build_plan(script, tmp_path / "shots", only_indices={1},
                           chapter=1, provider="stub")
    entry = plan["shots"][0]
    assert entry["verdict"] == "pending"
    assert entry["generations"] == 0
    assert plan["ready"] == 0
