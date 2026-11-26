# -*- coding: utf-8 -*-
"""S49 migrated tests: bench_reasoning camera metric & script_quality_eval extract_metrics."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from tools.bench_reasoning import score_one  # noqa: E402
from script_quality_eval import extract_metrics  # noqa: E402


def test_camera_metric_uses_camera_movement_first():
    """camera_movement 是有效运镜输出时，不应被默认 cam_idx 判 0 分."""
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
    """无 camera_movement 字段时按 cam_idx 多样性评分."""
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
    """修复层统一填固定机位时，不应抹掉 cam_idx 的多样性."""
    script = {
        "shots": [
            {"camera_movement": "固定机位", "cam_idx": 1},
            {"camera_movement": "固定机位", "cam_idx": 2},
            {"camera_movement": "固定机位", "cam_idx": 3},
        ]
    }
    metrics = score_one(script, "", True, True, 0, {}, "deepseek")
    assert metrics["camera"] == 1.0


def test_extract_metrics_minimal_script():
    """纯函数最小分镜指标提取（离线）."""
    script = {
        "total_duration": 60.0,
        "shots": [
            {"index": 1, "narration_segment": "旁白一", "duration_sec": 4},
            {"index": 2, "narration_segment": "", "duration_sec": 4},
        ],
    }
    m = extract_metrics(script, "")
    assert m["shot_count"] == 2
    assert m["duration_sec"] == 60.0
    assert m["duration_ok"] is True
    assert m["dialogue_shots"] == 1
