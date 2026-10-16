"""shared.media_contract - 媒体合成契约的中立单源（2026-10-03 计划 §1、§15）.

这里只定义**一次**、被多个阶段共同消费的常量与纯函数；不新增任何 env 配置：

1. ``MERGE_FPS`` —— 合并与动效共用的帧率（S 与 A 都从这里取，不各自复制 24）；
2. 合法转场集合 —— 从 ``step04_merge.main._VALID_XFADE`` 迁入的唯一集合。
   旧稿的文本关键词归一（原 ``_TRANSITION_MAP``）只是**旧输入适配层**：
   归一后不在集合内的 kind 一律**拒收**，不再静默回退 fade；
   首镜淡入 / 末镜淡出属于**端点效果**（endpoint），不参与边界 min 判据；
3. 帧数量化 —— ``floor(duration_sec * fps + 0.5)`` 的唯一实现。
   A 包的验证与渲染、S 包的时长断言共用这一个函数，避免 Python ``round``
   与 JS ``Math.round`` 的-half 行为差异（Python banker's rounding）。
"""
from __future__ import annotations

#: 合并/动效统一帧率。产品上**不提供**第二个帧率开关；脚本级 fps/RENDER_FPS
#: 只影响 step01 的时长量化（见 §15「转场表示」与 §1）。
MERGE_FPS = 24

#: 边界可用的 xfade 转场名（原 step04_merge.main._VALID_XFADE，19 项）。
LEGAL_XFADE_TRANSITIONS = frozenset({
    "fade", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown",
    "circlecrop", "rectcrop", "distance", "fadeblack", "fadewhite",
    "radial", "smoothleft", "smoothright", "smoothup", "smoothdown",
})

#: 硬切：无转场重叠的边界。
BOUNDARY_CUT = "cut"

#: 边界（clip i-1 → clip i，i=1..n-1）允许的全部 kind。
LEGAL_BOUNDARY_TRANSITIONS = LEGAL_XFADE_TRANSITIONS | {BOUNDARY_CUT}

#: 端点效果：只作用于单个 clip 自身（首镜淡入 / 末镜淡出），**不减少总时长**。
ENDPOINT_FADE_IN = "fade_in"
ENDPOINT_FADE_OUT = "fade_out"
LEGAL_ENDPOINT_EFFECTS = frozenset({ENDPOINT_FADE_IN, ENDPOINT_FADE_OUT})


class MediaContractError(ValueError):
    """契约违例（非法 kind / 非法时长 / 非法量化输入）——调用方按拒收处理。"""


def is_legal_boundary_transition(kind: str) -> bool:
    return kind in LEGAL_BOUNDARY_TRANSITIONS


def is_legal_endpoint_effect(kind: str) -> bool:
    return kind in LEGAL_ENDPOINT_EFFECTS


def require_boundary_transition(kind: str) -> str:
    """边界 kind 校验：不在合法集合内即拒收（不静默回退 fade）。"""
    if not is_legal_boundary_transition(kind):
        raise MediaContractError(f"非法转场 kind: {kind!r}（合法集合: {sorted(LEGAL_BOUNDARY_TRANSITIONS)}）")
    return kind


def quantize_frame_count(duration_sec: float, fps: int = MERGE_FPS) -> int:
    """时长 → 帧数：``floor(duration_sec * fps + 0.5)``，不足 1 帧非法。

    这是**唯一**的帧数量化实现（A 包验证/渲染、S 包断言、帧起点计算共用）。
    """
    if not (isinstance(duration_sec, (int, float)) and duration_sec == duration_sec):  # NaN 拒绝
        raise MediaContractError(f"非法时长: {duration_sec!r}")
    if duration_sec < 0:
        raise MediaContractError(f"负时长: {duration_sec!r}")
    if not isinstance(fps, int) or fps <= 0:
        raise MediaContractError(f"非法帧率: {fps!r}")
    import math
    frames = math.floor(duration_sec * fps + 0.5)
    if frames < 1:
        raise MediaContractError(f"时长 {duration_sec!r}s 在 {fps}fps 下不足 1 帧")
    return frames


def quantized_duration_sec(duration_sec: float, fps: int = MERGE_FPS) -> float:
    """量化后的实际时长（帧数 / fps）。"""
    return quantize_frame_count(duration_sec, fps) / float(fps)


def frame_start_sec(frame_index: int, fps: int = MERGE_FPS) -> float:
    """第 ``frame_index`` 帧（0 起）的时间戳。seek 终点只采 ``(frame_count-1)/fps``。"""
    if not isinstance(frame_index, int) or frame_index < 0:
        raise MediaContractError(f"非法帧号: {frame_index!r}")
    return frame_index / float(fps)


def validate_transition_timing(tdur: float, d_prev: float, d_next: float) -> float:
    """边界过渡时长判据（§S2）：``tdur`` 有限非负且 ``tdur <= min(d_prev, d_next)``。

    该判据下 ``total = Σd - Σtdur`` 恒成立（旧实现的 ``max(0, ...)`` clamp 分支
    在新契约里改为显式拒收——见计划 §15「转场表示」与 S-3 的归纳证明）。
    """
    if tdur is None or tdur != tdur or tdur < 0:
        raise MediaContractError(f"非法过渡时长: {tdur!r}")
    if tdur == 0:
        return 0.0
    limit = min(float(d_prev), float(d_next))
    if tdur > limit:
        raise MediaContractError(
            f"过渡时长 {tdur}s 超过相邻可用时长 min({d_prev}, {d_next})={limit}s"
        )
    return float(tdur)
