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

from shared.model_caps import active_video_duration_cap, active_video_model

# 语速配置（字符/秒）
SPEECH_RATE = 3.5
PAUSE_PER_SHOT = 1.5
MIN_SHOT_DURATION = 3.0
# 单镜硬上限（08-23 用户规则）：正片单镜时长不长于 8s。
# 钩子时长另算（不受 8s 限制，见 _compute_timecodes hook 段）。
MAX_SHOT_DURATION = 8.0
# 正片分镜总时长硬下限（08-23 用户规则）：不含钩子 ≥ 60s。
# 实际拉伸动作在 constraints/runtime_extend.py（W6，目标 65s 留裕度），
# 此处仅用于日志告警。钩子时长另算，不计入该下限。
MIN_TOTAL_SHOTS_DURATION = 60.0


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
    """检测文本是否包含对话（引号或全角冒号）。判定逻辑统一在 shared/dialogue.py。"""
    from shared.dialogue import has_dialogue
    return has_dialogue(text)


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

    # 匹配 "前X秒"/"后X秒" 或 "X-Y秒" 格式。
    # 支持混合格式（"前2秒：A；2-4秒：B；后1秒：C"），用并集法取最大值，
    # 避免单格式正则漏掉后段区间（code-audit 08-01 #8 的防重复累加由取 max 替代）。
    # 格式1: "前N秒：...；后M秒：..."
    m1 = re.findall(r'前(\d+(?:\.\d+)?)秒', motion_desc)
    m2 = re.findall(r'后(\d+(?:\.\d+)?)秒', motion_desc)
    pre_post = sum(float(x) for x in m1) + sum(float(x) for x in m2)

    # 格式2: "X-Y秒：..." (范围格式，取所有区间终点)
    m3 = re.findall(r'(\d+(?:\.\d+)?)\s*[-–]\s*(\d+(?:\.\d+)?)\s*秒', motion_desc)
    ranges = [float(end) for start, end in m3 if float(end) > float(start)]
    range_max = max(ranges) if ranges else 0.0

    # 格式3: 末尾总时长说明 "共N秒" 或 "时长N秒"
    match = re.search(r'(?:共|时长|总)\s*(\d+(?:\.\d+)?)\s*秒', motion_desc)
    explicit = float(match.group(1)) if match else 0.0

    # 取所有能确定时长的来源的最大值（并集），防止漏段
    candidates = [pre_post, range_max, explicit]
    positives = [c for c in candidates if c > 0]
    return max(positives) if positives else None


def _align_frame_duration(raw: float, fps: float) -> float:
    """时长对齐整数帧 + 输出 ≤3 位小数（08-23 用户要求 duration 保留 2-3 位小数）.

    帧对齐铁律（v6.32）：duration × fps 必须是整数帧，否则视频生成多/少一帧卡顿。
    但 fps=24 时 1/24 = 0.04166… 是循环小数，直接 round(raw*fps)/fps 得到
    5.583333333333333 这类长小数；若粗暴 round(x, 2) 又破坏整数帧对齐
    （5.58×24=133.92 非整帧 → style_prefix.validate_fps_consistency 每次误报）。

    关键：fps=24 时 duration = 帧数/24 只有在帧数是 3 的倍数时才非循环
    （3k/24 = k/8，最多 3 位小数：0.125/0.375/0.5…）。因此把帧数归一化到
    最近 3 的倍数——同时满足「整数帧对齐」+「输出 ≤3 位小数」。

    通用化：仅当帧数×1000/fps 非整数（时长在 3 位内不可精确表示，如 fps=24/30）
    才归一化到 3 帧步长；fps=25 等 1/fps 可整除 1000 的帧率天然 2 位小数，跳过。
    最大偏差 1.5 帧 = 0.0625s（fps=24），远小于 QC 时长容差 1.5s。
    """
    if fps <= 0:
        return round(float(raw), 2)
    frames = round(float(raw) * fps)
    # 时长在 3 位小数内能否精确表示？帧数 × 1000 / fps 应为整数
    if abs(frames * 1000 / fps - round(frames * 1000 / fps)) > 1e-6:
        frames = round(frames / 3) * 3
    return frames / fps


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

    # 视频帧率对齐（v6.32，08-12）：时长 × fps 必须是整数帧，否则视频生成
    # 多/少一帧导致卡顿（step01.log 反复报「duration 与 fps=24 不对齐」）。
    # 旧实现按音频帧 1/30s 对齐，与视频 fps=24 冲突；改按 fps 对齐整数帧。
    fps = float(script.get("fps", 24) or 24)
    if fps <= 0:
        fps = 24.0

    # 视频模型单镜时长上限（0=不夹取）。防止长对白/长 motion 分段生成的镜头
    # 超过模型上限（如 Seedance 1.5 pro=12s、Hailuo v1 720P=6s）被 provider
    # 截断——provider 现在会按能力表夹取，但那是「截断内容」而非「规划时长」。
    dur_cap = active_video_duration_cap()
    # 08-23 用户规则：正片单镜 ≤ 8s（MAX_SHOT_DURATION）；模型 cap 更严则随模型。
    # 钩子不受 8s 限制（钩子时长另算，下方 hook 段仍只按模型 cap 夹取）。
    shot_cap = MAX_SHOT_DURATION if not dur_cap else min(dur_cap, MAX_SHOT_DURATION)
    clamped_count = 0

    for shot in shots:
        text = shot.get("narration_segment", "")
        motion_desc = shot.get("motion_desc", "")
        var_type = shot.get("variation_type", "small")

        # v6.6: 优先从 motion_desc 解析时间分段
        motion_duration = _extract_motion_duration(motion_desc) if motion_desc else None

        # 对话镜头时长必须以「能念完台词」为下限：取 motion 分段与字数估时的较大值，
        # 避免视频太短、TTS 长台词被截断（音画同步关键）。
        speech_duration = _estimate_duration(text) if _has_dialogue(text) else 0.0

        if motion_duration and motion_duration > 0:
            base_duration = max(motion_duration, speech_duration) if speech_duration > 0 else motion_duration
        elif _has_dialogue(text):
            # 有对话但无 motion 分段：按对话字数估算时长
            base_duration = speech_duration
        else:
            # 纯旁白：固定短时长
            base_duration = NARRATION_FIXED_DURATION

        # #29 修复 2026-08-14：先兜底 MIN_SHOT_DURATION、再按 fps 对齐整数帧。
        # 旧顺序「先对齐后 max」在分数 fps（如 23.976）下会被 max 兜出
        # 3.0s 非整帧时长，破坏对齐保证（step01.log 反复报 duration 与 fps 不对齐）。
        raw = max(base_duration, MIN_SHOT_DURATION)
        # 08-23：_align_frame_duration 兼顾「整数帧对齐」+「输出 ≤3 位小数」
        # （fps=24 时长不再出现 5.583333333333333 长小数）
        duration = _align_frame_duration(raw, fps)
        # 按单镜上限夹取（模型 cap / 8s 硬限取严；先夹后对齐，保证整帧）。
        # 超过模型上限会被 provider 截断内容；8s 是 08-23 用户规则（钩子另算）。
        if duration > shot_cap:
            duration = _align_frame_duration(shot_cap, fps)
            clamped_count += 1

        shot["duration"] = duration
        # duration_sec：视频时长的权威来源（剥离 TTS 后由它驱动），
        # 与 duration 同值；LLM 的 duration 仅作参考，此处覆盖。
        shot["duration_sec"] = duration
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

    # 计算正片分镜总时长（08-23 用户规则：≥60s 下限只算正片分镜，钩子另算）
    shots_total = sum(s.get("duration", 0) for s in shots)
    total = shots_total

    # 加上钩子分镜时长（如果有）
    # v6.14: hook 时长下限 = max(motion_desc 时间分段, LLM duration, 对话字数估时)。
    # 此前 LLM 常给 hook duration=3.0，而 motion_desc 描述的是 6 秒动作（前2/2-4/4-6），
    # 视频生成后段动作被硬截断（hook 视频只有 3 秒，动作没播完）。
    hook = script.get("hook")
    if hook and isinstance(hook, dict):
        # 1) motion_desc 时间分段（如"前2秒…2-4秒…4-6秒"→6s）优先，动作必须播完
        motion_duration = _extract_motion_duration(hook.get("motion_desc", "")) if hook.get("motion_desc") else None
        # 2) LLM 给出的 duration（仅作参考）
        llm_duration = None
        raw_duration = hook.get("duration")
        if raw_duration is not None:
            try:
                llm_duration = float(raw_duration)
            except (TypeError, ValueError):
                llm_duration = None
        # 3) 对话字数估算（钩子通常是尖叫/对话，留足口型时间）
        hook_text = hook.get("narration_segment", "")
        speech_duration = _estimate_duration(hook_text) if hook_text else 0.0

        candidates = [d for d in (motion_duration, llm_duration, speech_duration) if d and d > 0]
        if candidates:
            hook_duration = max(candidates)
        else:
            hook_duration = MIN_SHOT_DURATION
        # 下限保护：钩子是开场悬念，至少给足动作时间，禁止 < MIN_SHOT_DURATION
        hook_duration = max(hook_duration, MIN_SHOT_DURATION)
        # v6.32：钩子时长同样按 fps 对齐整数帧（与 shots 一致）；
        # 08-23：改用 _align_frame_duration，兼顾整数帧对齐 + 输出 ≤3 位小数
        hook_duration = _align_frame_duration(hook_duration, fps)
        # 钩子时长同样受视频模型上限约束
        if dur_cap and hook_duration > dur_cap:
            hook_duration = _align_frame_duration(dur_cap, fps)
        hook["duration"] = round(hook_duration, 2)
        hook["duration_sec"] = round(hook_duration, 2)
        total += hook_duration

    script["total_duration"] = round(total, 2)
    script["total_effective_duration"] = round(total, 2)
    # 08-23 用户规则日志：正片分镜时长（不含钩子）与钩子时长分开报
    hook_dur = 0.0
    if hook and isinstance(hook, dict):
        try:
            hook_dur = float(hook.get("duration_sec") or hook.get("duration") or 0)
        except (TypeError, ValueError):
            hook_dur = 0.0
    print(f"[INFO] 时间轴计算完成: {len(shots)} 镜 {shots_total:.1f}s（不含钩子）"
          + (f" + 1 钩子 {hook_dur:.1f}s（另算）" if hook else "")
          + f", 总计 {total:.1f}s")
    if shots_total < MIN_TOTAL_SHOTS_DURATION:
        print(f"[WARN] 正片分镜总时长 {shots_total:.1f}s < {MIN_TOTAL_SHOTS_DURATION:.0f}s 硬下限"
              f"（不含钩子），W6 时长拉伸将补足（constraints/runtime_extend）")
    if clamped_count:
        cap_src = f"，视频模型上限 {dur_cap}s" if dur_cap and dur_cap < MAX_SHOT_DURATION else ""
        print(f"[INFO] {clamped_count} 个镜头时长超过单镜上限 {shot_cap}s{cap_src}，已夹取到 {shot_cap}s"
              f"（active model: {active_video_model()}）")
