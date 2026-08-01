"""step01_script_gen.timecode - 时间轴计算模块（v6.1 增强版）

功能：
- _compute_timecodes(): 根据文本长度计算每个镜头的 timecode
- _estimate_duration(): 优化的时长估算（考虑标点符号、句子长短、动态语速）
- 新增：keyframe_sample_count / movement_speed_multiplier / blank_hold_duration
"""
from __future__ import annotations

import sys
import re
from pathlib import Path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 语速配置（字符/秒）
SPEECH_RATE = 3.5
PAUSE_PER_SHOT = 1.5
MIN_SHOT_DURATION = 3.0


def _estimate_duration(text: str) -> float:
    """优化后的时长估算函数（v6.1 新增）

    策略：
    1. 剔除标点符号，只计算有效汉字/单词
    2. 动态语速：短句慢读，长句快读（模拟人类呼吸）
    3. 增加停顿缓冲（根据标点符号数量）
    """
    if not text:
        return MIN_SHOT_DURATION

    # 1. 剔除标点符号，只计算有效汉字
    clean_text = re.sub(r'[^\w一-鿿]', '', text)
    char_count = len(clean_text)

    if char_count == 0:
        return MIN_SHOT_DURATION

    # 2. 动态语速：短句慢读，长句快读
    base_rate = 4.0  # 基础语速
    if char_count < 10:
        base_rate = 3.0  # 短句慢读
    elif char_count > 50:
        base_rate = 4.5  # 长句快读

    duration = char_count / base_rate

    # 3. 增加停顿缓冲（根据标点符号）
    pauses = text.count('，') * 0.3 + text.count('。') * 0.6 + text.count('！') * 0.5 + text.count('？') * 0.5
    duration = duration + pauses

    # 4. 确保最小时长
    return max(duration, MIN_SHOT_DURATION)

# variation_type → 运镜速度倍率 / 关键帧数量
_SPEED_MAP = {"small": 1.0, "medium": 0.5, "large": 0.3}
_KEYFRAME_MAP = {"small": 1, "medium": 2, "large": 3}


def _has_dialogue(text: str) -> bool:
    """检测文本是否包含对话（带冒号、引号）。"""
    dialogue_markers = [":", "：", '"', "“", "‘", "——"]
    return any(marker in text for marker in dialogue_markers)


def _extract_motion_duration(motion_desc: str) -> float | None:
    """从 motion_desc 中提取时间分段，计算实际时长。

    解析格式如：
    - "前3秒：推镜过程；后3秒：镜头稳定" -> 6秒
    - "前2秒：开场；中间3秒：动作；后2秒：收尾" -> 7秒
    - "0-2秒：A动作；2-4秒：B动作" -> 4秒

    Returns:
        计算出的总时长（秒），解析失败返回 None
    """
    if not motion_desc:
        return None

    # 匹配 "前X秒" 或 "后X秒" 或 "X-Y秒" 格式
    total = 0.0
    found = False

    # 格式1: "前N秒：...；后M秒：..."
    match = re.findall(r'前(\d+(?:\.\d+)?)秒', motion_desc)
    for m in match:
        total += float(m)
        found = True
    match = re.findall(r'后(\d+(?:\.\d+)?)秒', motion_desc)
    for m in match:
        total += float(m)
        found = True

    # 格式2: "X-Y秒：..." (范围格式)
    match = re.findall(r'(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*秒', motion_desc)
    for start, end in match:
        diff = float(end) - float(start)
        if diff > 0:
            total += diff
            found = True

    # 格式3: 末尾总时长说明 "共N秒" 或 "时长N秒"
    match = re.search(r'(?:共|时长|总)\s*(\d+(?:\.\d+)?)\s*秒', motion_desc)
    if match:
        return float(match.group(1))

    return total if found else None


def _compute_timecodes(script: dict) -> None:
    """根据 motion_desc 时间分段或对话字数计算每个镜头的 timecode。

    v6.6 修复：
    - 优先从 motion_desc 中解析时间分段（前N秒，后M秒）
    - 只有对话（带冒号/引号）才按字数估算时长
    - 纯旁白用固定短时长（4秒）
    """
    shots = script.get("shots", [])
    if not shots:
        return

    current_time = 0.0
    timeline = []

    # 纯旁白固定时长（不读旁白，只展示画面）
    NARRATION_FIXED_DURATION = 4.0

    for shot in shots:
        text = shot.get("narration_segment", "")
        motion_desc = shot.get("motion_desc", "")
        var_type = shot.get("variation_type", "small")

        # v6.6: 优先从 motion_desc 解析时间分段
        motion_duration = _extract_motion_duration(motion_desc) if motion_desc else None

        if motion_duration and motion_duration > 0:
            # 优先使用 motion_desc 中的时间分段
            base_duration = motion_duration
        elif _has_dialogue(text):
            # 有对话：按对话字数估算时长
            base_duration = _estimate_duration(text)
        else:
            # 纯旁白：固定短时长
            base_duration = NARRATION_FIXED_DURATION

        duration = round(base_duration * 30) / 30  # 对齐音频帧 (0.033s)
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

    # 计算总分镜时长
    total = sum(s.get("duration", 0) for s in shots)

    # 加上钩子分镜时长（如果有）
    hook = script.get("hook")
    if hook and isinstance(hook, dict):
        hook_duration = hook.get("duration")
        if hook_duration is not None:
            hook_duration = float(hook_duration)
        else:
            # 如果钩子没有时长，用 LLM 返回的 narration_segment 估算
            hook_text = hook.get("narration_segment", "")
            hook_duration = _estimate_duration(hook_text)
            hook["duration"] = round(hook_duration, 2)
        total += hook_duration

    script["total_duration"] = round(total, 2)
    script["total_effective_duration"] = round(total, 2)
    print(f"[INFO] 时间轴计算完成: {len(shots)} 镜 + {1 if hook else 0} 钩子, 总计 {total:.1f}s")
