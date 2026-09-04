"""motion_desc 时间分段解析（v6.58 Phase C-2，2026-09-03）.

把 step01 LLM 输出的 motion_desc（中文时段语法）解析为结构化时间段，
供 prompt_builder 在视频 prompt 中显式标注「0-2s: X / 2-4s: Y / ...」，
让 Seedance 2.0+ / H3 等支持时间分段的视频模型按段执行动作。

复用 `step01_script_gen.validation` 已有的两个函数：
  - `_parse_motion_time_segments(motion, dur)`：解析时段列表 [(start, end), ...]
  - `_split_motion_to_seg_texts(motion, segs, dur)`：切分每段文本

为什么不直接放 validation.py：validation.py 是 step01 内部的校验模块，
被 step02 依赖会形成反向耦合（step02→step01）。
所以这里做一层薄封装，让 step02 的 prompt_builder 只 import shared.

用法：
  from shared.motion_segments import build_motion_segments
  segs = build_motion_segments(shot)
  # → [{"start": 0.0, "end": 2.0, "text": "拔剑出鞘"}, {"start": 2.0, "end": 4.0, "text": "挥剑斩下"}, ...]
  # → [] (motion_desc 为空/无时段标记/duration 缺失)
"""
from __future__ import annotations

from typing import TypedDict


class MotionSegment(TypedDict):
    """单段时段结构."""
    start: float      # 起始秒数
    end: float        # 结束秒数
    text: str         # 该段动作描述（已剥离「前N秒：」标签）


def _get_duration(shot: dict) -> float:
    """从 shot 提取 duration_sec（兼容 timecode 字段）."""
    dur = shot.get("duration_sec")
    try:
        if dur and float(dur) > 0:
            return float(dur)
    except (TypeError, ValueError):
        pass
    tc = shot.get("timecode") or {}
    try:
        start = float(tc.get("start", 0) or 0)
        end = float(tc.get("end", 0) or 0)
        if end > start:
            return end - start
    except (TypeError, ValueError):
        pass
    return 0.0


def build_motion_segments(shot: dict) -> list[MotionSegment]:
    """从 shot.motion_desc 解析时间分段.

    返回:
      - []：motion_desc 为空 / 无时段标记 / duration 缺失
      - 1 段：仅 1 个显式时段（如「前2秒：拔剑」→ (0, 2)）
      - 2+ 段：显式分段时间表，自动补 implicit 末尾段

    幂等：纯函数，无副作用。
    """
    motion = (shot.get("motion_desc") or "").strip()
    if not motion:
        return []
    dur = _get_duration(shot)
    if dur <= 0:
        return []

    # Lazy import: validation.py 是 step01 内部模块，import 它会把 step01 链路带进来
    from step01_script_gen.validation import (
        _parse_motion_time_segments,
        _split_motion_to_seg_texts,
    )

    segs = _parse_motion_time_segments(motion, dur)
    if not segs:
        return []

    texts = _split_motion_to_seg_texts(motion, segs, dur)
    out: list[MotionSegment] = []
    for (a, b), text in zip(segs, texts):
        out.append({"start": a, "end": b, "text": (text or "").strip()})
    return out


def format_motion_segments(segments: list[MotionSegment], style: str = "zh") -> str:
    """把分段列表格式化为可拼到 prompt 的字符串.

    Args:
        segments: build_motion_segments 的输出
        style:
          - "zh"：中文时段语法「0-2秒：拔剑；2-4秒：挥剑」 (默认，兼容 step01 原生格式)
          - "en_s"：Seedance 2.0 官方推荐「0-2s: 拔剑; 2-4s: 挥剑」 (含 's')

    Returns:
        多段拼接字符串（无前导标签），调用方决定加什么前缀。
        空段（text 为空）自动跳过。
    """
    if not segments:
        return ""
    parts: list[str] = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        a, b = seg["start"], seg["end"]
        if style == "en_s":
            parts.append(f"{a:g}-{b:g}s: {text}")
        else:  # zh / 默认
            parts.append(f"{a:g}-{b:g}秒：{text}")
    return "；".join(parts)


def count_effective_segments(segments: list[MotionSegment]) -> int:
    """统计有效段数（text 非空的段）.

    比 len(segments) 更准：implicit 补的段 text 为空，应被排除。
    """
    return sum(1 for s in segments if (s.get("text") or "").strip())


__all__ = [
    "MotionSegment",
    "build_motion_segments",
    "format_motion_segments",
    "count_effective_segments",
]
