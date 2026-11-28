from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def test_video_quality_reports_camera_stacking_and_transition(monkeypatch, tmp_path):
    import step02_video_gen.prompt_builder as pb
    from step02_video_gen.video_prompt_quality import collect_video_prompt_quality

    monkeypatch.setattr(pb, "_build_prompt", lambda shot, script, mode="A": shot["prompt"])
    monkeypatch.setattr("shared.model_caps.active_video_is_seedance", lambda: False)
    from shared.paths import chapter_dir
    monkeypatch.setattr("shared.paths.chapter_dir", lambda: tmp_path)
    script = {
        "shots": [
            {"index": 1, "scene": "A", "duration_sec": 5, "prompt": "环绕镜，缓慢推近"},
            {"index": 2, "scene": "B", "duration_sec": 5, "prompt": "固定机位"},
        ]
    }
    result = collect_video_prompt_quality(script)
    cats = {w["category"] for w in result["warnings"]}
    assert "video_camera_stacking" in cats
    assert "video_transition_missing" in cats


def test_video_quality_clean_prompt(monkeypatch, tmp_path):
    import step02_video_gen.prompt_builder as pb
    from step02_video_gen.video_prompt_quality import collect_video_prompt_quality

    monkeypatch.setattr(pb, "_build_prompt", lambda shot, script, mode="A": shot["prompt"])
    monkeypatch.setattr("shared.model_caps.active_video_is_seedance", lambda: False)
    monkeypatch.setattr("shared.paths.chapter_dir", lambda: tmp_path)
    script = {"shots": [{"index": 1, "scene": "A", "duration_sec": 5,
                         "prompt": "固定机位，人物缓慢转头"}]}
    result = collect_video_prompt_quality(script)
    assert result["warning_count"] == 0


def test_video_product_acceptance_reports_status(monkeypatch, tmp_path):
    import step02_video_gen.video_prompt_quality as q
    import shared.audio_policy as ap
    import step04_merge.stretch as stretch
    work = tmp_path / "working"
    shot_dir = work / "shots" / "1"
    shot_dir.mkdir(parents=True)
    (shot_dir / "video.mp4").write_bytes(b"x" * (25 * 1024))
    class _Policy:
        effective_merge_mode = "tts_mix"
    monkeypatch.setattr(q, "chapter_dir", lambda: tmp_path)
    monkeypatch.setattr(ap, "resolve_audio_policy", lambda actual_audio_present=None: _Policy())
    monkeypatch.setattr(stretch, "get_media_duration", lambda p: 5.0)
    monkeypatch.setattr(stretch, "has_audio_stream", lambda p: False)
    r = q.write_video_product_quality({"shots": [{"index": 1}]}, work)
    assert r["status"] == "PASS"
    assert r["outputs"]["1"]["duration_sec"] == 5.0
