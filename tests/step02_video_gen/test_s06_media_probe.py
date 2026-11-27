# -*- coding: utf-8 -*-
"""S06/V3：0 元 FFmpeg 硬探针（黑场/静帧/残缺/首帧偏离）与视觉 QC 前置拦截.

全部用本地合成 MP4，**不调用任何视觉模型**。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from step02_video_gen import submit_poll
from step02_video_gen.video_qc import probe_video_media

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(FFMPEG is None or FFPROBE is None,
                                reason="需要本机 ffmpeg/ffprobe")


def _clip(path: Path, *, seconds: float = 2.0, source: str = "testsrc2",
          color: str = "black", audio: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    args = [FFMPEG, "-y", "-v", "error"]
    if source == "color":
        args += ["-f", "lavfi", "-i", f"color=c={color}:s=320x240:r=24:d={seconds}"]
    else:
        args += ["-f", "lavfi", "-i", f"{source}=size=320x240:rate=24:duration={seconds}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast"]
    if audio:
        args += ["-c:a", "aac", "-shortest"]
    args += [str(path)]
    subprocess.run(args, check=True)
    return path


def _png(path: Path, color=(0, 0, 0)) -> Path:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 240), color).save(path)
    return path


def test_normal_clip_passes_probe(tmp_path):
    clip = _clip(tmp_path / "ok.mp4")
    probe = probe_video_media(clip, expected_duration=2.0)
    assert probe.measured is True
    assert probe.critical == []
    assert probe.metrics["duration_sec"] == pytest.approx(2.0, abs=0.3)
    assert probe.metrics["width"] == 320 and probe.metrics["height"] == 240


def test_black_clip_is_critical(tmp_path):
    clip = _clip(tmp_path / "black.mp4", source="color", color="black")
    probe = probe_video_media(clip)
    codes = {i["code"] for i in probe.critical}
    assert "media_black_whole" in codes
    assert probe.status == "critical"


def test_static_clip_is_reported_as_freeze(tmp_path):
    clip = _clip(tmp_path / "static.mp4", source="color", color="navy")
    probe = probe_video_media(clip)
    codes = {i["code"] for i in probe.critical}
    assert "media_freeze_whole" in codes


def test_duration_mismatch_is_critical(tmp_path):
    clip = _clip(tmp_path / "short.mp4", seconds=2.0)
    probe = probe_video_media(clip, expected_duration=5.0)
    codes = {i["code"] for i in probe.critical}
    assert "media_duration_mismatch" in codes


def _checkerboard(path: Path) -> Path:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("L", (320, 240), 0)
    px = img.load()
    for y in range(240):
        for x in range(320):
            if ((x // 20) + (y // 20)) % 2 == 0:
                px[x, y] = 240
    img.save(path)
    return path


def test_first_frame_drift_detected_and_matching_frame_not_flagged(tmp_path):
    clip = _clip(tmp_path / "moving.mp4", source="testsrc2")
    other = _checkerboard(tmp_path / "checker.png")
    probe = probe_video_media(clip, first_frame_path=other)
    drift = [i for i in probe.issues if i["code"] == "media_first_frame_drift"]
    assert drift, probe.to_dict()
    assert probe.metrics["first_frame_similarity"]["comparable"] is True
    assert probe.metrics["first_frame_similarity"]["corr"] < 0.25

    # 用同一段视频自己的首帧做输入帧 ⇒ 相关=1，不报偏离
    frame = tmp_path / "same.png"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(clip), "-frames:v", "1",
                    str(frame)], check=True)
    same = probe_video_media(clip, first_frame_path=frame)
    assert not any(i["code"] == "media_first_frame_drift" for i in same.issues)


def test_flat_input_frame_is_not_comparable(tmp_path):
    """输入帧本身无结构（纯黑）⇒ 不宣称"偏离"，只记 comparable=False."""
    clip = _clip(tmp_path / "moving2.mp4", source="testsrc2")
    black = _png(tmp_path / "flat.png", (0, 0, 0))
    probe = probe_video_media(clip, first_frame_path=black)
    assert probe.metrics["first_frame_similarity"]["comparable"] is False
    assert not any(i["code"] == "media_first_frame_drift" for i in probe.issues)


def test_zero_cost_probe_blocks_before_any_vision_qc(tmp_path, monkeypatch):
    """探针判 critical ⇒ QC 直接失败，且**没有**进入视觉 QC 分支（0 模型调用）."""
    monkeypatch.setenv("VIDEO_QC_ENABLED", "1")
    called = {"visual": 0}

    def _boom(*args, **kwargs):  # 视觉 QC 一旦被调用就失败
        called["visual"] += 1
        raise AssertionError("视觉 QC 不应在 0 元探针已判坏时被调用")

    monkeypatch.setattr("step02_video_gen.video_qc.qc_video_file_result", _boom)
    clip = _clip(tmp_path / "black.mp4", source="color", color="black")
    qc = submit_poll._qc_video_product_result(clip, 1, 2.0)
    assert qc.status == "failed"
    assert qc.media_issues and qc.media_probe["status"] == "critical"
    assert called["visual"] == 0
    assert all(c.get("name") != "visual" for c in qc.checks)
    coverage, _sem, _status = submit_poll._qc_coverage(qc, "fail")
    assert "media_probe" in coverage and "black" in coverage


def test_probe_issues_flow_into_reroll_report(tmp_path):
    """探针问题按 kind 进入 `reroll_decision` 的 media 分类（不在这里决定重抽）."""
    from step02_video_gen import reroll_decision as rd

    issue = {"code": "media_black_whole", "kind": "black", "severity": "critical",
             "message": "整段黑屏", "evidence": {"segments": [[0, 2]]},
             "time_range": [0.0, 2.0]}
    report = rd.VideoCheckReport(
        media_status=rd.STATUS_FAIL,
        issues=[rd.VideoIssue(issue_id="shot1-media-0", kind="black",
                              observed=issue["message"],
                              evidence_paths=["a.mp4"], time_range=(0.0, 2.0))])
    decision = rd.decide(report, {"request_key": "k"})
    assert decision.kind == rd.DECISION_REVISE_INPUTS
    assert decision.issue_ids == ["shot1-media-0"]


def test_empty_scene_uses_single_authority(tmp_path, monkeypatch):
    """V3：空镜判据复用 `is_empty_scene`（帧布局有人物 ⇒ 不是空镜），不再看字面量."""
    from shared.image_qc import QcResult
    captured = {}

    def _fake_qc(frame, shot, frame_kind, characters=None, empty_scene=False,
                 prompt=None, kf_index=None):
        captured["empty_scene"] = empty_scene
        return QcResult(ok=True, has_hallucination=False, issues=[])

    monkeypatch.setattr("shared.image_qc.maybe_qc_sync", _fake_qc)
    clip = _clip(tmp_path / "moving.mp4", source="testsrc2")
    shot = {"index": 1, "main_subject": "空镜",
            "frame_layouts": {"ff": [{"name": "甲", "on_screen": True}],
                              "lf": [{"name": "甲", "on_screen": True}]}}
    monkeypatch.setenv("OUTPUT_BASE", str(tmp_path / "out"))
    monkeypatch.setenv("NOVEL_NAME", "匿名样本作品")
    monkeypatch.setenv("BOOK_INDEX", "1")
    monkeypatch.setenv("BOOK_NAME", "匿名测试卷")
    monkeypatch.setenv("NOVEL_CHAPTER", "1")
    working = (tmp_path / "out" / "匿名样本作品" / "part_01_匿名测试卷"
               / "chapter_001" / "vimax_working")
    working.mkdir(parents=True, exist_ok=True)
    from step02_video_gen.video_qc import qc_video_file_result
    result = qc_video_file_result(clip, 1, shot=shot, characters=[])
    assert result.status in ("clean", "unverified"), result.to_dict()
    assert captured.get("empty_scene") is False
