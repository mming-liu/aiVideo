"""step04_merge.srt_gen - SRT 字幕生成（cue 唯一源在 shared.subtitle_contract）.

2026-10-03（S 包）：本模块不再自己切句/算时窗——原分句与逐镜时窗逻辑迁进
``shared.subtitle_contract``，这里只做两件事：

1. :func:`build_srt_plan` 把（旧口径或合法对齐的）字幕转成 :class:`SubtitlePlan`；
2. :func:`generate_srt` 把同一个计划的 SRT 文本落盘。

FFmpeg ``subtitles`` 滤镜侧由调用方用**同一份** plan 构造
（``shared.subtitle_contract.ffmpeg_subtitle_filter`` 会按字节核对盘上文件）。
旧签名（位置参数与前 5 个参数）与默认行为保持不变，新增**可选** ``validated_timings``。
"""
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from typing import List, Optional

from shared.subtitle_contract import (  # noqa: F401  (向后兼容的重导出)
    MAX_CHARS_PER_LINE,
    SRT_ENCODING,
    SubtitleCue,
    SubtitlePlan,
    build_subtitle_plan,
    seconds_to_srt_time,
    split_subtitle_by_punctuation,
    write_srt,
)


def build_srt_plan(shots: List[dict], durations: List[float],
                   clip_windows: Optional[List[tuple]] = None,
                   speech_durations: Optional[List[Optional[float]]] = None,
                   validated_timings: Optional[dict] = None,
                   *, script_revision: Optional[str] = None,
                   shot_audio_sha256: Optional[dict] = None,
                   warn=None) -> SubtitlePlan:
    """字幕事实源：旧口径或（逐镜降级的）对齐候选 → :class:`SubtitlePlan`。"""
    return build_subtitle_plan(
        shots, durations, clip_windows=clip_windows, speech_durations=speech_durations,
        validated_timings=validated_timings, script_revision=script_revision,
        shot_audio_sha256=shot_audio_sha256, warn=warn or print)


def generate_srt(shots: List[dict], durations: List[float], output: Path,
                 clip_windows: Optional[List[tuple]] = None,
                 speech_durations: Optional[List[Optional[float]]] = None,
                 validated_timings: Optional[dict] = None) -> None:
    """Generate SRT subtitle file based on per-shot target durations.

    v5.2：长字幕按标点智能拆分（逻辑已迁至 shared.subtitle_contract）。
    S 包：``validated_timings`` 为 C 包校验后的合法对齐结果（可选，位置参数不变）。
    """
    plan = build_srt_plan(shots, durations, clip_windows=clip_windows,
                          speech_durations=speech_durations,
                          validated_timings=validated_timings)
    write_srt(plan, output)
    total = plan.cues[-1].end_sec if plan.cues else 0.0
    print(f"[OK] SRT generated: {output} ({len(plan.cues)} subtitles, total {total:.1f}s)")
