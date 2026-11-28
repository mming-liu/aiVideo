# -*- coding: utf-8 -*-
"""``tools/verify_merge_timeline.py`` 的入口验收（计划 §S3）.

工具本身是交付物：这里跑它的真实入口（含 CLI），断言独立 golden 通过，
并用**判别性反例**（overlay 越界 / 时长对不上）证明它不是"永远返回 pass"。
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools import verify_merge_timeline as vmt  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="需要 ffmpeg")

W, H = 160, 90


def _spec(**overrides) -> dict:
    spec = {
        "schema_version": vmt.FIXTURE_SCHEMA, "scope": "fixture-scope",
        "script_revision": "rev-1", "fps": 24, "width": W, "height": H,
        "clips": [{"shot_index": 1, "kind": "shot", "duration_sec": 2.0, "tone_hz": 440},
                  {"shot_index": 2, "kind": "shot", "duration_sec": 3.0, "tone_hz": 660}],
        "boundary_transitions": [{"index": 1, "kind": "fade", "duration_sec": 0.5}],
        "endpoint_effects": [{"kind": "fade_in", "duration_sec": 0.5, "clip_index": 0},
                             {"kind": "fade_out", "duration_sec": 0.5, "clip_index": 1}],
        "audio_mode": "native",
        "audio_tracks": [{"kind": "narration", "stable_id": "narration:N1",
                          "duration_sec": 2.0, "tone_hz": 1200,
                          "start_sec": 1.5, "end_sec": 3.5}],
        "overlays": [{"overlay_id": "ov1", "shot_index": 2, "kind": "image",
                      "start_sec": 2.0, "end_sec": 3.0, "layer": 0}],
        "subtitles": {"cues": [], "gap_sec": None},
        "audio_golden": [
            {"freq_hz": 1200, "present": [[1.6, 3.2]], "absent": [[0.2, 1.2]],
             "reference_window": [1.6, 2.6]},
            {"freq_hz": 440, "present": [[0.2, 1.3]]},
        ],
        "overlay_golden": {"at_sec": 2.5, "outside_sec": 4.3, "base_rgb": [0, 0, 125]},
        "crf": "28", "preset": "ultrafast",
    }
    spec.update(overrides)
    return spec


def test_verify_tool_passes_on_a_frozen_fixture(tmp_path):
    report = vmt.verify(_spec(), tmp_path / "run", run_legacy=False)
    assert report["verdict"] == "pass"
    checks = {c["check"]: c for c in report["checks"]}
    assert set(checks) == {"duration", "media", "audio_onsets", "subtitles", "overlay"}
    assert all(c["status"] in ("pass", "n/a") for c in report["checks"])
    # golden：2 + 3 − 0.5 = 4.5（端点不减总长），偏差 ≤ 一帧 + AAC 帧
    assert report["timeline"]["total_duration_sec"] == pytest.approx(4.5)
    assert checks["duration"]["delta_sec"] <= checks["duration"]["tolerance_sec"]
    assert checks["duration"]["tolerance_sec"] < 0.1
    assert (tmp_path / "run" / "report.json").is_file()
    assert Path(report["candidate"]["path"]).is_file()
    assert report["candidate"]["stages"] == ["validate", "compile", "encode", "verify",
                                            "publish"]


def test_verify_tool_cli_entry_and_exit_codes(tmp_path):
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(_spec(), ensure_ascii=False), encoding="utf-8")
    rc = vmt.main(["--fixture", str(fixture), "--out-dir", str(tmp_path / "out"),
                   "--skip-legacy-entry"])
    assert rc == vmt.EXIT_OK

    # 未知 schema ⇒ 输入拒收（2），不是"永远 pass"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": "nope"}), encoding="utf-8")
    assert vmt.main(["--fixture", str(bad), "--out-dir", str(tmp_path / "out2"),
                     "--skip-legacy-entry"]) == vmt.EXIT_INVALID_INPUT


def test_verify_tool_emits_a_sample_fixture(tmp_path):
    target = tmp_path / "sample.json"
    assert vmt.main(["--emit-sample-fixture", str(target)]) == vmt.EXIT_OK
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["schema_version"] == vmt.FIXTURE_SCHEMA


def test_verify_tool_rejects_an_overlay_outside_its_clip_window(tmp_path):
    """判别性：overlay 越界 ⇒ 时间线层拒收（不是继续渲染再假装通过）。"""
    spec = _spec(overlays=[{"overlay_id": "ov1", "shot_index": 2, "kind": "image",
                            "start_sec": 0.2, "end_sec": 0.9, "layer": 0}])
    with pytest.raises(vmt.TimelineError) as ei:
        vmt.verify(spec, tmp_path / "run", run_legacy=False)
    assert ei.value.code == "overlay_out_of_window"


def test_verify_tool_golden_fails_when_the_expectation_is_wrong(tmp_path):
    """判别性：把 golden 的时长期望改错 ⇒ golden 判定必须转红。"""
    spec = _spec()
    monkeypatch_spec = dict(spec)
    # 用一个必然对不上的 overlay 观察点：窗口对但颜色断言放到窗口外
    monkeypatch_spec["overlay_golden"] = {"at_sec": 4.3, "outside_sec": 2.5,
                                          "base_rgb": [0, 0, 125]}
    with pytest.raises(vmt.GoldenFailure):
        vmt.verify(monkeypatch_spec, tmp_path / "run", run_legacy=False)


def test_verify_report_declares_the_legacy_allowed_difference(tmp_path, monkeypatch):
    """旧入口对照：允许差异（末端淡出映射）在报告里逐条声明，且失败留证。"""
    evidence = {"clips": [{"path": str(tmp_path / "c1.mp4"), "kind": "shot",
                           "shot_index": 1, "duration_sec": 0.5}],
                "audio_tracks": [], "overlays": [], "inputs_dir": str(tmp_path)}
    (tmp_path / "c1.mp4").write_bytes(b"x")
    result = vmt.run_legacy_entry(_spec(), evidence, tmp_path / "legacy", timeout=300)
    assert result["status"] in ("ok", "failure_evidence")
    items = {d["item"] for d in result["allowed_differences"]}
    assert {"endpoint_fade_out", "filter_string"} <= items
    assert (tmp_path / "legacy" / "legacy_stdout.log").is_file()
