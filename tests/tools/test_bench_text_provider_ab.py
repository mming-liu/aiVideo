from __future__ import annotations

import sys
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


pytest.importorskip("tools.bench_text_provider_ab")


def test_doubao_hardfix_register_is_explicit(monkeypatch):
    """A/B 工具不应依赖 TEXT_PROVIDER 才能挂载 doubao hard_fix。"""
    monkeypatch.setenv("TEXT_PROVIDER", "deepseek")

    from step01_script_gen.constraints import engine
    from tools.bench_text_provider_ab import _apply_doubao_hardfixes

    script = {
        "shots": [{
            "index": 1,
            "scene": "庭院",
            "location": "庭院",
            "main_subject": "杨旭说",
            "prompt": "中景，固定机位。",
            "ff_desc": "杨旭抬眼。",
            "lf_desc": "杨旭仍在庭院。",
            "motion_desc": "前2秒:杨旭抬眼；2-4秒:杨旭说完。",
            "core_keywords": "杨旭,庭院",
            "narration_segment": "",
            "subtitle": "",
            "duration_sec": 4,
            "keyframes": [],
        }],
        "characters": [],
    }

    script, report = _apply_doubao_hardfixes(script)

    names = {c.name for c in engine.ALL_CONSTRAINTS}
    assert "doubao_dialogue_narration_empty" in names
    assert "杨旭说" in script["shots"][0]["main_subject"]
    assert isinstance(report, int)


def test_camera_metric_uses_camera_movement_first():
    """camera_movement 是有效运镜输出时，不应被默认 cam_idx 判 0 分。"""
    from tools.bench_reasoning import score_one

    script = {
        "shots": [
            {"camera_movement": "固定机位", "cam_idx": 1},
            {"camera_movement": "慢推", "cam_idx": 1},
            {"camera_movement": "拉远", "cam_idx": 1},
        ]
    }

    metrics = score_one(script, "", True, True, 0, {}, "agent_plan")

    assert metrics["camera"] == 1.0


def test_camera_metric_falls_back_to_cam_idx():
    """旧 DeepSeek 输出没有 camera_movement 时仍按 cam_idx 评分。"""
    from tools.bench_reasoning import score_one

    script = {
        "shots": [
            {"cam_idx": 1},
            {"cam_idx": 2},
            {"cam_idx": 3},
        ]
    }

    metrics = score_one(script, "", True, True, 0, {}, "deepseek")

    assert metrics["camera"] == 1.0


def test_camera_metric_uses_fallback_when_movement_is_uniform():
    """修复层统一填固定机位时，不应抹掉 cam_idx 的多样性别。"""
    from tools.bench_reasoning import score_one

    script = {
        "shots": [
            {"camera_movement": "固定机位", "cam_idx": 1},
            {"camera_movement": "固定机位", "cam_idx": 2},
            {"camera_movement": "固定机位", "cam_idx": 3},
        ]
    }

    metrics = score_one(script, "", True, True, 0, {}, "deepseek")

    assert metrics["camera"] == 1.0


def test_process_trial_counts_final_hard_unresolved_and_gap(monkeypatch):
    """最终 hard_fix 后的 HARD unresolved 与 gap 失败必须反映到 warns。"""
    import json

    from tools import bench_text_provider_ab as bench

    monkeypatch.setattr(
        bench,
        "_apply_final_hardfixes",
        lambda provider, script: (script, 2),
    )

    def fake_validate(script, novel_text):
        return {"overall_pass": False, "distribution": {}}

    monkeypatch.setattr(
        "step01_script_gen.gap_analysis.validate_script_gap",
        fake_validate,
    )
    monkeypatch.setattr(
        "step01_script_gen.validation.parse_response",
        lambda raw, novel_text: json.loads(raw),
    )

    raw = {
        "shots": [
            {"narration_segment": "", "subtitle": "", "prompt": ""}
            for _ in range(4)
        ]
    }
    raw_res = {
        "success": True,
        "text": json.dumps(raw, ensure_ascii=False),
        "error": None,
        "usage": {},
        "elapsed": 0.0,
    }

    result = bench.process_trial(
        "agent_plan", raw_res, "novel", trial=7, dry_run=True
    )

    assert result["hard_unresolved_count"] == 2
    assert result["final_gap_pass"] is False


def test_process_trial_does_not_dump_parse_failure_in_dry_run(monkeypatch):
    """dry-run 解析失败时也不得覆盖任何真实 raw dump。"""
    import json

    from tools import bench_text_provider_ab as bench

    dumped = []
    monkeypatch.setattr(bench, "_dump_raw", lambda *args, **kwargs: dumped.append(args))

    def fail_parse(raw, novel_text):
        raise ValueError("parse failed")

    monkeypatch.setattr(
        "step01_script_gen.validation.parse_response",
        fail_parse,
    )

    raw_res = {
        "success": True,
        "text": json.dumps({"shots": []}),
        "error": None,
        "usage": {},
        "elapsed": 0.0,
    }

    result = bench.process_trial(
        "deepseek", raw_res, "novel", trial=3, dry_run=True
    )

    assert dumped == []
    assert result["parse_err"] == "parse failed"
