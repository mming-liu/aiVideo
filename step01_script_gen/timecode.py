"""step01_script_gen.timecode - 时间轴计算模块

功能：
- _compute_timecodes(): 根据文本长度计算每个镜头的 timecode
- SPEECH_RAE, PAUSE_PER_SHOT, MIN_SHOT_DURATION: 语速配置
"""
import sys
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 语速配置（字符/秒）
SPEECH_RAE = 3.5
PAUSE_PER_SHOT = 1.5
MIN_SHOT_DURATION = 3.0


def _compute_timecodes(script: dict) -> None:
    """根据文本长度计算每个镜头的 timecode（基于 TTS 实际语速）.

    公式: duration = len(narration_segment) / SPEECH_RAE + PAUSE_PER_SHOT
    结果四舍五入到 0.333s 步长（音频帧对齐）
    """
    shots = script.get("shots", [])
    if not shots:
        return

    current_time = 0.0
    for shot in shots:
        text = shot.get("narration_segment", "")
        # 计算基础时长
        base_duration = len(text) / SPEECH_RAE + PAUSE_PER_SHOT
        # 四舍五入到 0.333s 步长
        duration = round(base_duration * 3) / 3
        duration = max(duration, MIN_SHOT_DURATION)

        shot["duration"] = duration
        shot["timecode"] = {
            "start": round(current_time, 2),
            "end": round(current_time + duration, 2),
        }
        current_time += duration

    # 计算总时长
    total = sum(s.get("duration", 0) for s in shots)
    script["total_duration"] = round(total, 2)
    script["total_effective_duration"] = round(total, 2)
    print(f"[INFO] 时间轴计算完成: {len(shots)} 镜, 总计 {total:.1f}s")