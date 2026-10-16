# -*- coding: utf-8 -*-
"""step04_merge.backends - 合并后端（单一 FFmpeg 内核，计划 §S1/§S2）.

不实施双后端；MoviePy 只作为选型比对记录（§S1）。这里只做三件事：

1. 从 :class:`step04_merge.timeline.MergeTimeline` 编译 filter 图与输入列表
   （**同一份列表**派生 ``-i`` 参数与 ``[i:v]`` 索引，不两处各写一遍）；
2. 以本地 subprocess 跑 FFmpeg，失败/中断时结束进程并保留旧 accepted；
3. 解出来的产物实测媒体与时长判据，然后原子发布到候选路径。
"""
from __future__ import annotations

from .ffmpeg import (  # noqa: F401
    BACKEND_NAME,
    EncodeSettings,
    InputSpec,
    MediaInfo,
    RenderError,
    RenderResult,
    build_inputs,
    compile_graph,
    ffmpeg_version,
    get_backend,
    probe_media,
    render,
)

__all__ = [
    "BACKEND_NAME", "EncodeSettings", "InputSpec", "MediaInfo", "RenderError", "RenderResult",
    "build_inputs", "compile_graph", "ffmpeg_version", "get_backend", "probe_media", "render",
]
