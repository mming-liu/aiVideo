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
from shared.paths import chapter_dir
from shared.tee_logger import get_logger

# v6.43（08-23 用户规则）：用项目 logger 统一记录，不再 print 散落
_logger = None
def _log():
    global _logger
    if _logger is None:
        _logger = get_logger(chapter_dir() / "step01.log", console_level="INFO")
    return _logger

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


# v6.43（08-25）：超 cap 镜头自动拆镜的切点查找
# 优先级：句号 > 问号/感叹号 > 省略号 > 逗号（中点附近）> 切中字（中点）
_SPLIT_DELIMS_PRIORITY = ("。", "？", "！", "…", "，")


def _find_best_split_point(text: str) -> int:
    """在 text 中找最接近中点的优先级切点.

    Returns:
        切点位置（char index, 切点之前的字符保留在第一段）.
        找不到合适切点返回 -1.
    """
    if not text:
        return -1
    target = len(text) // 2
    best = (-1, float("inf"))  # (index, distance_to_target)
    for delim in _SPLIT_DELIMS_PRIORITY:
        for i, ch in enumerate(text):
            if ch == delim:
                # 切点必须在 [text_len*0.25, text_len*0.75] 区间，避免切出半句话
                if i < len(text) * 0.25 or i > len(text) * 0.75:
                    continue
                d = abs(i - target)
                if d < best[1]:
                    best = (i + 1, d)  # 切点 = delim 之后
        if best[0] >= 0:
            return best[0]
    return -1


def _do_pre_split_oversized_shots(shots: list, shot_cap: float) -> int:
    """v6.43（08-25）修复 8s 静默 clamp：估时 > shot_cap 的 shot 拆为 2 镜.

    旧实现：timecode.py for 循环里 `if duration > shot_cap: duration = shot_cap`
    静默压缩时长 → 长对话镜头后半段被截断 / TTS 音画不同步。

    新实现：pre-pass 在 _compute_timecodes 主循环之前把 shots 里超 cap 的按
    narration_segment 句号切为 2 镜，原地修改 shots 列表。

    切镜策略（防递归爆炸）：
    1. 估时 > shot_cap 才拆（防 false positive）
    2. 二分找切点：必须满足"两段估时都 ≤ shot_cap"——保证拆 1 次就够
    3. 找切点：优先级 (。 > ？ > ！ > … > ，) 距中点最近且满足 ②
    4. 找不到合适切点 → 不拆（保留原版，下游约束再处理）
    5. shot_b 继承 shot_a 的 prompt/ff/lf/char_layout/motion_desc（连贯性）
    6. shot_b.narration_segment = 切后段；subtitle 同步
    7. shot_b.index = shot_a.index + 0.5（半整数；timeline 顺序按 list 序）

    Returns:
        拆出镜数（int；0 = 全部不需拆）
    """
    import copy
    split_count = 0
    i = 0
    while i < len(shots):
        shot = shots[i]
        # v6.51：每镜最多拆 1 次。_compute_timecodes 每次落盘都会重跑本
        # pre-pass（main.py 5 处调用），已拆出的子镜若估时仍 > cap 会被
        # 再次拆分，index +0.5 链式递增（7.5→8.0）与真实 shot 8 数值碰撞
        # （16:34 run 实测：两次 [SPLIT] 日志、shots 同时含 8.0 与 8）。
        if "_split_from" in shot or "_split_to" in shot:
            i += 1
            continue
        text = shot.get("narration_segment", "")
        if not text:
            i += 1
            continue
        estimated = _estimate_duration(text)
        if estimated <= shot_cap:
            i += 1
            continue
        # 找切点：必须满足切后两段估时都 ≤ shot_cap（防递归）
        split_at = _find_split_point_within_cap(text, shot_cap)
        if split_at <= 0 or split_at >= len(text) - 1:
            i += 1
            continue
        # 构造 shot_b
        shot_b = copy.deepcopy(shot)
        # narration_segment 切分
        shot_b["narration_segment"] = text[split_at:].strip()
        if shot_b.get("subtitle"):
            shot_b["subtitle"] = shot_b["narration_segment"]
        # ff_desc 继承 shot_a 的 lf_desc（接续）
        lf_a = shot.get("lf_desc", "")
        if lf_a:
            shot_b["ff_desc"] = lf_a
        # keyframes 清空（keyframe 由 motion_desc 派生，shot_b 不再有时间分段）
        shot_b["keyframes"] = []
        if not shot_b.get("motion_desc"):
            # 没 motion_desc 时给个空的避免 validator 误报
            shot_b["motion_desc"] = ""
        # 标记拆镜关系（调试用，不进入 prompt）
        shot_b["_split_from"] = shot.get("index", i)
        # 索引防碰撞：+0.5 撞现有 index（int/float 数值相等即撞）时逐级细化，
        # 防止出现 8.0 与真实 shot 8 并存的数值重复（排序/查重即不稳定）
        _orig_idx = shot.get("index", i) or i
        _existing = {s.get("index") for s in shots if isinstance(s, dict)}
        new_idx = round(_orig_idx + 0.5, 4)
        for _frac in (0.25, 0.125, 0.0625):
            if new_idx not in _existing:
                break
            new_idx = round(_orig_idx + _frac, 4)
        shot_b["index"] = new_idx
        # v6.50（08-29）拆镜视觉差异化：此拆分器 shot_b.ff=shot_a.lf（接续）
        # 但 shot_b.lf 是同一文本深拷贝 → ff==lf 必现；prompt 深拷贝 → 相邻
        # 相似度 1.00（14:22 run shot11.5/12.5 实测）。按"后半镜"（part_i=1）
        # 差异化：景别 +1、过肩、情绪递进、焦点、lf 缓推兜底。
        try:
            from step01_script_gen.split_visuals import differentiate_split_visuals
            differentiate_split_visuals(shot, shot_b, part_i=1)
        except Exception:
            pass
        # v6.50（08-29）后半镜致命伤锚延续：拆分发生在约束层 injury_continuity
        # 之后，新 halves 没人再补锚（15:12 run shot12.5/14.5/15.5 ff/lf 无伤锚
        # 实测——刺杀后画面会画出无伤的杨旭）。原镜带真伤锚 + 非威胁角色在
        # 后半镜画面 → ff/lf 补锚。
        try:
            from step01_script_gen.prompt_semantic.registry import (
                INJURY_ANCHOR_TAIL as _IAT,
                STRONG_INJURY_ANCHORS as _SIA,
                BLOOD_STATE_WORDS as _BSW,
                COLLECTIVE_NAMES as _COLL,
            )
            _orig_txt = (shot.get("prompt") or "") + (shot.get("lf_desc") or "") + (shot.get("ff_desc") or "")
            if any(w in _orig_txt for w in _SIA):
                _victim = ""
                for _it in (shot_b.get("char_layout") or []):
                    _nm = str((_it or {}).get("name", "")).strip()
                    if (_nm and not any(t in _nm for t in ("刺客", "神秘人", "黑衣人", "杀手"))
                            and not any(w in _nm for w in _COLL)):
                        _victim = _nm
                        break
                _b_txt = (shot_b.get("ff_desc") or "") + (shot_b.get("lf_desc") or "")
                if _victim and _victim in _b_txt:
                    for _fld in ("ff_desc", "lf_desc"):
                        _t = shot_b.get(_fld) or ""
                        # v6.51：已锚判定用 BLOOD_STATE_WORDS（含 暗红/洇开/血迹 等
                        # 可见血状态词）。原用 STRONG（洇痕/血痕…）——变体锚
                        # 「暗红痕迹洇开，清晰可见」漏判 → 重复追加完整 _IAT →
                        # 下游复读去重把重复片段抠掉，残留「。暗红血迹洇开，血痕」
                        # 截断碎片（16:34 run shot8.0 实测）。
                        if _t and not any(w in _t for w in _BSW):
                            shot_b[_fld] = _t.rstrip("。") + f"。{_IAT}"
        except Exception:
            pass
        # shot_a 留前半段
        shots[i]["narration_segment"] = text[:split_at].strip()
        if shots[i].get("subtitle"):
            shots[i]["subtitle"] = shots[i]["narration_segment"]
        shots[i]["_split_to"] = shot_b["index"]
        # 插入到 i+1
        shots.insert(i + 1, shot_b)
        split_count += 1
        # v6.43 08-25：限制单 shot 最多拆 1 次（i 跳过 shot_b 自身），防
        # 23s 长对话拆成 6 镜导致镜数爆炸。shot_b 估时仍可能 > cap，由
        # _compute_timecodes 主循环的 clamp 兜底（虽然会被截断，但至少
        # shot_a 是完整的）。
        i += 2
    if split_count:
        try:
            _log().info(
                f"[SPLIT] {split_count} 个镜头估时 > {shot_cap:.1f}s，已按 narration_segment 切为 2 镜"
            )
        except Exception:
            pass
    return split_count


def _find_split_point_within_cap(text: str, cap: float) -> int:
    """在 text 中找切点，满足切后两段估时都 ≤ cap（防递归拆镜）.

    策略：枚举优先级切点（。 > ？ > ！ > … > ，），找最接近中点且两段都 ≤ cap。
    退化：若两段都 ≤ cap 找不到，按"只强制左段 ≤ cap、右段任意"再找 1 次。
    Returns: 切点 index（0 或 len(text)-1 = 不可拆）.
    """
    if not text:
        return -1
    target = len(text) // 2
    best_strict = (-1, float("inf"))
    best_loose = (-1, float("inf"))  # 退化：只要求左段 ≤ cap
    for delim in _SPLIT_DELIMS_PRIORITY:
        for i, ch in enumerate(text):
            if ch != delim:
                continue
            # 切点必须在 [text_len*0.2, text_len*0.8] 区间（避免切出半句话）
            if i < len(text) * 0.2 or i > len(text) * 0.8:
                continue
            left = text[:i + 1]
            right = text[i + 1:]
            d = abs(i - target)
            # 严格：两段都 ≤ cap
            if _estimate_duration(left) <= cap and _estimate_duration(right) <= cap:
                if d < best_strict[1]:
                    best_strict = (i + 1, d)
            # 退化：只要求左段 ≤ cap（右段可能仍超 cap，由 shot_b 继续尝试拆）
            elif _estimate_duration(left) <= cap:
                if d < best_loose[1]:
                    best_loose = (i + 1, d)
        if best_strict[0] >= 0:
            return best_strict[0]
    return best_loose[0]  # 严格没找到就退化（仍优于不拆）


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

    v6.43（08-23 修复）钩子时间轴：钩子按播放顺序在头部（开场钩子→淡出黑场
    →正片），所以 timecode 0..hook_duration 归 hook，正片 shots 从
    hook_duration..hook_duration+shots_total。但 shots_total（正片分镜总时长）
    用于"60-90s"硬约束检查时**仍只算 shots（不含钩子）**——钩子另算。
    """
    shots = script.get("shots", [])
    if not shots:
        return

    # v6.43：先算钩子时长（开放在前），正片 shots 起始时间 = hook_duration
    hook = script.get("hook")
    hook_duration = 0.0
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

    # v6.43（08-25）：拆镜 pre-pass——估时 > shot_cap 的 shot 按 narration_segment
    # 句号切为 2 镜，避免 timecode 静默 clamp 截断长对话镜头（违反 v6.43 用户规则）。
    # 放在 fps/dur_cap 推导之后，让 _estimate_duration / 拆分都走同一 shot_cap。
    _do_pre_split_oversized_shots(shots, shot_cap)

    # 钩子按模型 cap + fps 整数帧对齐（与 shots 一致；钩子本身不受 8s cap）
    if hook_duration > 0:
        hook_duration = _align_frame_duration(hook_duration, fps)
        if dur_cap and hook_duration > dur_cap:
            hook_duration = _align_frame_duration(dur_cap, fps)
        hook["duration"] = round(hook_duration, 2)
        hook["duration_sec"] = round(hook_duration, 2)
        # 钩子 timecode 放在播放顺序头部（0..hook_duration）
        hook["timecode"] = {
            "start": 0.0,
            "end": round(hook_duration, 2),
        }
    else:
        hook["duration"] = 0.0
        hook["duration_sec"] = 0.0
        hook["timecode"] = {"start": 0.0, "end": 0.0}

    # v6.43：正片 shots 起始时间 = hook_duration（钩子在播放顺序头部）
    current_time = hook_duration
    timeline = []
    NARRATION_FIXED_DURATION = 4.0  # 纯旁白固定时长

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

    # v6.43：timeline 列表前置钩子条目（播放顺序：hook → shots）
    if hook and isinstance(hook, dict) and hook_duration > 0:
        timeline.insert(0, {
            "index": 0,
            "is_hook": True,
            "start": 0.0,
            "end": round(hook_duration, 2),
            "duration": hook_duration,
            "effective_duration": round(hook_duration, 2),
            "variation_type": hook.get("variation_type", "large"),
            "keyframe_sample_count": _KEYFRAME_MAP.get(hook.get("variation_type", "large"), 3),
            "movement_speed_multiplier": _SPEED_MAP.get(hook.get("variation_type", "large"), 0.3),
            "blank_hold_duration": round(PAUSE_PER_SHOT, 1),
        })

    script["timeline"] = timeline

    # 计算正片分镜总时长（08-23 用户规则：≥60s 下限只算正片分镜，钩子另算）
    shots_total = sum(s.get("duration", 0) for s in shots)
    # v6.43：total = hook_duration + shots_total（钩子在播放顺序头部）
    total = hook_duration + shots_total

    script["total_duration"] = round(total, 2)
    script["total_effective_duration"] = round(total, 2)
    # 08-23 用户规则日志：正片分镜时长（不含钩子）与钩子时长分开报
    _log().info(f"时间轴计算完成: {len(shots)} 镜 {shots_total:.1f}s（不含钩子）"
          + (f" + 1 钩子 {hook_duration:.1f}s（另算，开场在头部）" if hook and hook_duration > 0 else "")
          + f", 总计 {total:.1f}s")
    if shots_total < MIN_TOTAL_SHOTS_DURATION:
        _log().warn(f"正片分镜总时长 {shots_total:.1f}s < {MIN_TOTAL_SHOTS_DURATION:.0f}s 硬下限"
              f"（不含钩子），W6 时长拉伸将补足（constraints/runtime_extend）")
    if clamped_count:
        cap_src = f"，视频模型上限 {dur_cap}s" if dur_cap and dur_cap < MAX_SHOT_DURATION else ""
        # v6.43（08-23 用户规则）：clamp 升级为 WARN——LLM 应拆镜而非靠静默
        # clamp 压缩时长（会丢失动作链）。让跑批时一眼看到 prompt 哪里没拆。
        _log().warn(f"{clamped_count} 个镜头时长超过单镜上限 {shot_cap}s{cap_src}，"
              f"已夹取到 {shot_cap}s（active model: {active_video_model()}）。"
              f"v6.43 用户规则：单镜 > 8s 应在 prompt 层拆成多镜，不靠此处静默 clamp。")
