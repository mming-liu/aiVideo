# -*- coding: utf-8 -*-
"""配置单源回归（2026-11-27 配置解耦）.

覆盖：画幅比例、分辨率兜底、时长下限、RENDER_FPS→成片帧率、参考图上限。
这些断言只证明配置被正确消费；真实生成效果仍需按阶段入口另行验收。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _run(code: str, **env_overrides: str) -> str:
    env = dict(os.environ)
    env.pop("RENDER_FPS", None)
    for key in ("VIDEO_SIZE", "RENDER_RESOLUTION", "DURATION_MIN_SEC", "IMAGE_MAX_REFS"):
        env.pop(key, None)
    env.update(env_overrides)
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), env=env,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=120)
    assert proc.returncode == 0, proc.stderr[-800:]
    return proc.stdout.strip()


def test_frame_canvas_ratio_follows_video_size(monkeypatch):
    from shared.config import DEFAULT_VIDEO_SIZE
    from shared.style_prefix import frame_canvas_ratio

    monkeypatch.setenv("VIDEO_SIZE", "1280*720")
    assert frame_canvas_ratio() == "16:9"
    monkeypatch.setenv("VIDEO_SIZE", "1440*2560")
    assert frame_canvas_ratio() == "9:16"
    monkeypatch.delenv("VIDEO_SIZE", raising=False)
    assert frame_canvas_ratio() == "9:16"
    w, h = (int(x) for x in DEFAULT_VIDEO_SIZE.replace("*", "x").split("x"))
    assert frame_canvas_ratio() == ("16:9" if w > h else "9:16")


def test_global_render_config_resolution_from_config(monkeypatch):
    from shared.config import DEFAULT_VIDEO_SIZE
    from shared.style_prefix import get_global_render_config

    monkeypatch.delenv("RENDER_RESOLUTION", raising=False)
    monkeypatch.setenv("VIDEO_SIZE", "1280*720")
    assert get_global_render_config()["resolution"] == "1280*720"
    monkeypatch.delenv("VIDEO_SIZE", raising=False)
    assert get_global_render_config()["resolution"] == DEFAULT_VIDEO_SIZE


def test_prompt_paths_use_frame_ratio_not_hardcoded():
    """两条视频路径都必须经 frame_canvas_ratio()，不得再写死 ratio=\"9:16\"."""
    files = [REPO / "step02_video_gen" / "prompt_builder.py",
             REPO / "step02_video_gen" / "submit_poll.py"]
    for path in files:
        text = path.read_text(encoding="utf-8")
        assert 'style_profile(ratio="9:16")' not in text, path.name
        assert "frame_canvas_ratio()" in text, path.name


def test_duration_floor_follows_config():
    out = _run("from shared.config import DURATION_MIN_SEC;"
               "from step01_script_gen.timecode import MIN_TOTAL_SHOTS_DURATION;"
               "print(DURATION_MIN_SEC, MIN_TOTAL_SHOTS_DURATION)",
               DURATION_MIN_SEC="30")
    assert out.split() == ["30.0", "30.0"]


def test_merge_fps_follows_render_fps():
    assert _run("from shared.config import RENDER_FPS;"
                "from shared.media_contract import MERGE_FPS;"
                "print(RENDER_FPS, MERGE_FPS)", RENDER_FPS="30").split() == ["30", "30"]
    assert _run("from shared.media_contract import MERGE_FPS;print(MERGE_FPS)").strip() == "24"


def test_ref_limit_honours_image_max_refs(monkeypatch):
    from shared.ref_compose import get_ref_limit

    monkeypatch.setenv("IMAGE_MAX_REFS", "3")
    assert get_ref_limit("seedream-5-0-pro") == 3       # 注册模型能力 10，被配置压到 3
    assert get_ref_limit("unknown-image-model") == 3
    monkeypatch.delenv("IMAGE_MAX_REFS", raising=False)
    assert get_ref_limit("seedream-5-0-pro") == 10      # 未显式配置时保留模型能力
