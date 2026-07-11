"""step01_script_gen.timecode - 时间轴计算模块（v5.0 增强版）

功能：
- _compute_timecodes(): 根据文本长度计算每个镜头的 timecode
- SPEECH_RATE, PAUSE_PER_SHOT, MIN_SHOT_DURATION: 语速配置
- 新增：keyframe_sample_count / movement_speed_multiplier / blank_hold_duration
"""
from __future__ import annotations

import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 语速配置（字符/秒）
SPEECH_RATE = 3.5
PAUSE_PER_SHOT = 1.5
MIN_SHOT_DURATION = 3.0

# variation_type → 运镜速度倍率 / 关键帧数量
_SPEED_MAP = {"small": 1.0, "medium": 0.5, "large": 0.3}
_KEYFRAME_MAP = {"small": 1, "medium": 2, "large": 3}


def _compute_timecodes(script: dict) -> None:
    """根据文本长度计算每个镜头的 timecode（基于 TTS 实际语速）.

    v5.0 新增字段到每个 shot 和 timeline：
      - keyframe_sample_count: 关键帧采样数量
      - movement_speed_multiplier: 运镜速度倍率
      - blank_hold_duration: 留白空镜时长

    公式: duration = len(narration_segment) / SPEECH_RATE + PAUSE_PER_SHOT
    结果四舍五入到 0.333s 步长（音频帧对齐）
    """
    shots = script.get("shots", [])
    if not shots:
        return

    current_time = 0.0
    timeline = []

    for shot in shots:
        text = shot.get("narration_segment", "")
        var_type = shot.get("variation_type", "small")

        # 基础时长计算
        base_duration = len(text) / SPEECH_RATE + PAUSE_PER_SHOT
        duration = round(base_duration * 3) / 3
        duration = max(duration, MIN_SHOT_DURATION)

        shot["duration"] = duration
        shot["timecode"] = {
            "start": round(current_time, 2),
            "end": round(current_time + duration, 2),
        }

        # v5.0: 运镜量化参数
        shot["keyframe_sample_count"] = _KEYFRAME_MAP.get(var_type, 1)
        shot["movement_speed_multiplier"] = _SPEED_MAP.get(var_type, 1.0)
        shot["blank_hold_duration"] = round(PAUSE_PER_SHOT, 1)

        timeline.append({
            "index": shot.get("index", 0),
            "start": round(current_time, 2),
            "end": round(current_time + duration, 2),
            "duration": duration,
            "effective_duration": round(duration, 2),
            "variation_type": var_type,
            "keyframe_sample_count": shot["keyframe_sample_count"],
            "movement_speed_multiplier": shot["movement_speed_multiplier"],
            "blank_hold_duration": shot["blank_hold_duration"],
        })

        current_time += duration

    script["timeline"] = timeline
    total = sum(s.get("duration", 0) for s in shots)
    script["total_duration"] = round(total, 2)
    script["total_effective_duration"] = round(total, 2)
    print(f"[INFO] 时间轴计算完成: {len(shots)} 镜, 总计 {total:.1f}s")
