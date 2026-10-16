# -*- coding: utf-8 -*-
"""step04_merge.timeline - ``MergeTimeline v1``：合并的显式时间/媒体输入协议（计划 §S2）.

设计约束（逐条对应计划）：

- **唯一一套时间算术**：总时长 = 规范化后各 clip 实际时长之和 − 全部合法边界过渡
  重叠之和；``start_i = total_{i-1} - tdur_i``、``end_i = start_i + d_i``，不做
  ``max(0, …)`` 修正——合法性由 ``shared.media_contract.validate_transition_timing``
  在编译前保证（非法即拒收）。
- **边界与端点分开**：``boundary_transitions``（i=1..n-1，参与 min 判据与递推）与
  ``endpoint_effects``（首淡入/末淡出，只作用于本镜、**不减总长**）是两组字段；
  首项没有前镜，只能是 cut（或旧入口的端点写法）。
- **编号身份**：``stable_shot_id`` = scope + script_revision + (hook | shot:<index>)；
  ``shot_index`` 只是旧定位映射，hook（index=0）是**独立身份**，不能当第一正文镜头。
- **后端只消费已验证的绝对时窗**：不猜角色、不猜正文、不按数组位置猜身份。

本模块不做任何媒体编码（那是 ``step04_merge.backends`` 的职责），也不写文件。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path

from shared.media_contract import (
    BOUNDARY_CUT,
    ENDPOINT_FADE_IN,
    ENDPOINT_FADE_OUT,
    LEGAL_BOUNDARY_TRANSITIONS,
    LEGAL_ENDPOINT_EFFECTS,
    MERGE_FPS,
    MediaContractError,
    quantize_frame_count,
    quantized_duration_sec,
    require_boundary_transition,
    validate_transition_timing,
)
from shared.subtitle_contract import SubtitlePlan

#: 时间线 schema 版本（写入 manifest / 候选冻结输入）
SCHEMA_MERGE_TIMELINE = "merge_timeline.v1"

#: 候选素材来自 V/T 的**enhancement attempt manifest**（schema 与包名由
#: ``shared.enhancement_io`` 定义），路径/摘要/身份由 F 的
#: ``shared.candidate_inputs.resolve_candidate_inputs`` 解析——本包不另立清单格式。
#: 声音身份字段口径**单源**在 ``shared.audio_identity``（REQUIRED_IDENTITY_FIELDS /
#: NULLABLE_IDENTITY_FIELDS / NONEMPTY_IDENTITY_FIELDS），本包不复制第二份清单。

CANDIDATE_UNIT_ROLES = ("hook", "shot", "dialogue", "narration")

_FRAME_EPS = 1e-9


class TimelineError(Exception):
    """时间线拒收（结构化 code/stage，供退出码与报告使用）。"""

    def __init__(self, code: str, message: str, *, stage: str = "timeline", exit_code: int = 2,
                 **fields):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.message = message
        self.exit_code = int(exit_code)
        self.fields = fields

    def payload(self) -> dict:
        return {"code": self.code, "stage": self.stage, "message": self.message, **self.fields}


# ------------------------------------------------------------------ 身份

def stable_shot_id(scope: str, script_revision: str, kind: str, shot_index: int,
                   shot_digest: str | None = None) -> str:
    """稳定身份：``<scope>:<script_revision>:hook`` 或 ``…:shot:<index>[:<摘要12>]``.

    hook（index=0）与正文镜头是**不同身份**，不会因为编号相同而互相顶替。

    ``shot_digest`` 接**已有身份单源**（``shared.quality_gate.per_shot_revisions`` /
    ``shot_semantic_digest``）；给了它时身份里带逐镜语义摘要，改一镜不影响其他镜的
    绑定——不新造第二套身份（2026-10-03 复核）。
    """
    revision = str(script_revision or "")
    base = f"{scope}:{revision}" if revision else str(scope)
    if kind == "hook":
        ident = f"{base}:hook"
    elif kind == "shot":
        ident = f"{base}:shot:{int(shot_index)}"
    else:
        raise TimelineError("unknown_clip_kind", f"非法 clip kind: {kind!r}", kind=kind)
    if shot_digest:
        ident += f":{str(shot_digest)[:12]}"
    return ident


# ------------------------------------------------------------------ 数据模型

@dataclass(frozen=True)
class ClipEntry:
    """一个视频输入（hook 或正文镜头）。``duration_sec`` 是**规范化后**的实际时长。"""

    shot_index: int
    kind: str                      # "hook" | "shot"
    stable_id: str
    path: Path
    duration_sec: float            # 帧量化后的实际时长
    raw_duration_sec: float        # ffprobe 实测
    frames: int
    sha256: str | None = None
    has_audio: bool = False
    source: str = "legacy"         # "legacy" | "video_candidates"


@dataclass(frozen=True)
class BoundaryTransition:
    """边界 i（连接 clip i-1 与 i，i=1..n-1）。cut 的 ``duration_sec`` 为 0。"""

    index: int
    kind: str
    duration_sec: float


@dataclass(frozen=True)
class EndpointEffect:
    """端点效果（首淡入 / 末淡出）：只作用于本镜，不减总长。"""

    kind: str                      # "fade_in" | "fade_out"
    duration_sec: float
    clip_index: int


@dataclass(frozen=True)
class ClipWindow:
    """clip 在最终时间轴上的绝对窗口。"""

    index: int
    start_sec: float
    end_sec: float

    @property
    def duration_sec(self) -> float:
        return self.end_sec - self.start_sec


@dataclass(frozen=True)
class TimelineAudioTrack:
    """一条额外音轨：逐镜 TTS 对白（kind=shot_tts）或图外旁白（kind=narration）。"""

    kind: str
    stable_id: str
    path: Path
    clip_index: int | None = None
    start_sec: float | None = None
    end_sec: float | None = None
    gain: float = 1.0
    sha256: str | None = None
    identity: dict | None = None
    source: str = "legacy"


@dataclass(frozen=True)
class OverlayEntry:
    """动效叠加（A 包产物）：按 scope+revision+稳定 shot_id 绑定到镜头。"""

    overlay_id: str
    stable_shot_id: str
    shot_index: int
    kind: str                      # "image" | "png_sequence"
    path: Path
    start_sec: float
    end_sec: float
    layer: int = 0
    spec_id: str | None = None
    fps: int = MERGE_FPS
    sha256: str | None = None


@dataclass(frozen=True)
class MergeTimeline:
    """合并的完整输入事实。渲染前必须通过 :func:`validate_timeline`。"""

    scope: str
    script_revision: str
    width: int
    height: int
    fps: int
    clips: tuple[ClipEntry, ...]
    boundary_transitions: tuple[BoundaryTransition, ...]
    endpoint_effects: tuple[EndpointEffect, ...] = ()
    audio_mode: str = "native"                       # "native" | "tts_mix"
    audio_tracks: tuple[TimelineAudioTrack, ...] = ()
    subtitle_plan: SubtitlePlan | None = None
    overlays: tuple[OverlayEntry, ...] = ()
    source: dict = field(default_factory=dict)

    # -- 计算属性（与编译共用同一套函数，不各自实现） --
    @property
    def windows(self) -> tuple[ClipWindow, ...]:
        return compute_windows(self.clips, self.boundary_transitions)

    @property
    def total_duration_sec(self) -> float:
        return compute_total_duration(self.clips, self.boundary_transitions)

    def clip_for_shot(self, shot_index: int) -> ClipEntry | None:
        for clip in self.clips:
            if int(clip.shot_index) == int(shot_index):
                return clip
        return None

    def window_for_clip(self, clip_index: int) -> ClipWindow:
        return self.windows[clip_index]

    def to_dict(self) -> dict:
        """冻结/留档用的规范化描述（也是 input_key 的事实来源）。"""
        return {
            "schema_version": SCHEMA_MERGE_TIMELINE,
            "scope": self.scope,
            "script_revision": self.script_revision,
            "width": self.width, "height": self.height, "fps": self.fps,
            "audio_mode": self.audio_mode,
            "clips": [{"shot_index": c.shot_index, "kind": c.kind, "stable_id": c.stable_id,
                       "path": str(c.path), "sha256": c.sha256,
                       "duration_sec": round(c.duration_sec, 9), "frames": c.frames,
                       "has_audio": c.has_audio, "source": c.source} for c in self.clips],
            "boundary_transitions": [{"index": b.index, "kind": b.kind,
                                      "duration_sec": round(b.duration_sec, 9)}
                                     for b in self.boundary_transitions],
            "endpoint_effects": [{"kind": e.kind, "duration_sec": round(e.duration_sec, 9),
                                  "clip_index": e.clip_index} for e in self.endpoint_effects],
            "audio_tracks": [{"kind": t.kind, "stable_id": t.stable_id, "path": str(t.path),
                              "clip_index": t.clip_index, "start_sec": t.start_sec,
                              "end_sec": t.end_sec, "gain": t.gain, "sha256": t.sha256,
                              "source": t.source} for t in self.audio_tracks],
            "overlays": [{"overlay_id": o.overlay_id, "stable_shot_id": o.stable_shot_id,
                          "shot_index": o.shot_index, "kind": o.kind, "path": str(o.path),
                          "start_sec": o.start_sec, "end_sec": o.end_sec, "layer": o.layer,
                          "spec_id": o.spec_id, "fps": o.fps} for o in self.overlays],
            "subtitle": None if self.subtitle_plan is None else {
                "source": self.subtitle_plan.source,
                "sha256": self.subtitle_plan.sha256,
                "cues": [{"cue_id": c.cue_id, "text": c.text,
                          "start_sec": c.start_sec, "end_sec": c.end_sec,
                          "shot_index": c.shot_index, "source": c.source}
                         for c in self.subtitle_plan.cues],
                "fallbacks": [dict(f) for f in self.subtitle_plan.fallbacks],
            },
            "source": dict(self.source),
        }


# ------------------------------------------------------------------ 时长与窗口

def normalize_duration(duration_sec: float, fps: int = MERGE_FPS) -> tuple[int, float]:
    """实测时长 → （帧数, 规范化时长）。量化函数单源在 shared.media_contract。"""
    try:
        frames = quantize_frame_count(float(duration_sec), fps)
    except MediaContractError as exc:
        raise TimelineError("invalid_clip_duration", str(exc), duration=duration_sec) from exc
    return frames, frames / float(fps)


def compute_total_duration(clips, boundary_transitions, fps: int = MERGE_FPS) -> float:
    """总长 = Σ d_i − Σ tdur_i（端点效果不减总长）。"""
    if not clips:
        return 0.0
    total = float(clips[0].duration_sec)
    by_index = {int(b.index): b for b in boundary_transitions}
    for i in range(1, len(clips)):
        boundary = by_index.get(i)
        tdur = float(boundary.duration_sec) if boundary is not None else 0.0
        total = (total - tdur) + float(clips[i].duration_sec)
    return round(total, 9)


def compute_windows(clips, boundary_transitions) -> tuple[ClipWindow, ...]:
    """每镜的绝对 (start, end)：``start_i = total_{i-1} - tdur_i``，无 max(0) 修正。"""
    if not clips:
        return ()
    by_index = {int(b.index): b for b in boundary_transitions}
    windows = [ClipWindow(0, 0.0, round(float(clips[0].duration_sec), 9))]
    total = float(clips[0].duration_sec)
    for i in range(1, len(clips)):
        boundary = by_index.get(i)
        tdur = float(boundary.duration_sec) if boundary is not None else 0.0
        start = round(total - tdur, 9)
        end = round(start + float(clips[i].duration_sec), 9)
        windows.append(ClipWindow(i, start, end))
        total = end
    return tuple(windows)


# ------------------------------------------------------------------ 旧入口适配层

def windows_from_legacy(durations, transitions, fps: int = MERGE_FPS):
    """旧入口口径的窗口计算：``([(start,end)...], total)``.

    量化/判据与 :func:`validate_timeline` 共用同一套函数（先量化再比 min：量化函数
    单调不增，合法性方向不变）；旧 ``transitions`` 列表先经
    :func:`transitions_from_legacy` 迁成边界 + 端点。
    """
    durs = [float(d) for d in (durations or [])]
    if not durs:
        return [], 0.0
    clips: list[ClipEntry] = []
    for i, raw in enumerate(durs):
        frames, normalized = normalize_duration(raw, fps)
        clips.append(ClipEntry(shot_index=i, kind="shot", stable_id=f"legacy:shot:{i}",
                               path=Path("."), duration_sec=round(normalized, 9),
                               raw_duration_sec=raw, frames=frames))
    boundaries, _endpoints, _notes = transitions_from_legacy(transitions, n_clips=len(clips),
                                                             fps=fps)
    boundaries = quantize_transitions(boundaries, clips, fps)
    windows = compute_windows(clips, boundaries)
    return [(w.start_sec, w.end_sec) for w in windows], compute_total_duration(clips, boundaries)


def transitions_from_legacy(items, *, n_clips: int, fps: int = MERGE_FPS):
    """旧 ``transitions`` 列表（每镜一项）→ (boundary_transitions, endpoint_effects, notes).

    §15「转场表示」逐条落实：

    - ``transitions[0] == fade_in`` → **首端点**；多镜时首项其余取值仅按 cut 处理；
    - ``transitions[-1] == fade_out`` → **末端点**，该边界为 cut（保持旧入口含义）；
    - 其他位置的 ``fade_in``/``fade_out`` **拒收**；
    - 单镜时单项不猜"同时首淡入末淡出"：``fade_in`` 只做首端点，``fade_out`` 只做末端点。
    """
    entries = list(items or [])
    if n_clips <= 0:
        return (), (), []
    if len(entries) < n_clips:
        entries = entries + [("cut", 0.0)] * (n_clips - len(entries))
    notes: list[str] = []
    endpoints: list[EndpointEffect] = []

    def _kind_dur(item):
        kind, dur = (item or ("cut", 0.0))[0], (item or ("cut", 0.0))[1]
        return str(kind or BOUNDARY_CUT), float(dur or 0.0)

    if n_clips == 1:
        kind, dur = _kind_dur(entries[0])
        if kind == ENDPOINT_FADE_IN:
            endpoints.append(EndpointEffect(ENDPOINT_FADE_IN, dur, 0))
        elif kind == ENDPOINT_FADE_OUT:
            endpoints.append(EndpointEffect(ENDPOINT_FADE_OUT, dur, 0))
        elif kind not in LEGAL_BOUNDARY_TRANSITIONS:
            raise TimelineError("illegal_transition_kind",
                                f"单镜时间线的转场 kind 非法: {kind!r}")
        return (), tuple(endpoints), notes

    # 首项：只有 fade_in 有端点含义，其余仅 cut
    first_kind, first_dur = _kind_dur(entries[0])
    if first_kind == ENDPOINT_FADE_IN:
        endpoints.append(EndpointEffect(ENDPOINT_FADE_IN, first_dur, 0))
    elif first_kind in LEGAL_ENDPOINT_EFFECTS:
        raise TimelineError(
            "endpoint_effect_misplaced",
            f"fade_out 出现在首镜（非末镜），拒收: {first_kind!r}（不猜同时首淡入/末淡出）",
            position=0)
    elif first_kind not in LEGAL_BOUNDARY_TRANSITIONS:
        raise TimelineError("illegal_transition_kind",
                            f"首项转场 kind 非法: {first_kind!r}", position=0)
    elif first_kind != BOUNDARY_CUT:
        notes.append(f"首镜的 {first_kind!r} 没有前镜可过渡，按 cut 处理（旧入口含义）")

    boundaries: list[BoundaryTransition] = []
    for i in range(1, n_clips):
        kind, dur = _kind_dur(entries[i])
        if kind in LEGAL_ENDPOINT_EFFECTS:
            if i == n_clips - 1 and kind == ENDPOINT_FADE_OUT:
                endpoints.append(EndpointEffect(ENDPOINT_FADE_OUT, dur, n_clips - 1))
                boundaries.append(BoundaryTransition(i, BOUNDARY_CUT, 0.0))
                continue
            raise TimelineError(
                "endpoint_effect_misplaced",
                f"fade_in/fade_out 只能作端点（首项淡入/末项淡出），位置 {i} 非法: {kind!r}",
                position=i)
        if kind not in LEGAL_BOUNDARY_TRANSITIONS:
            raise TimelineError("illegal_transition_kind",
                                f"边界 {i} 的转场 kind 非法: {kind!r}", position=i)
        boundaries.append(BoundaryTransition(i, kind, dur if kind != BOUNDARY_CUT else 0.0))
    return tuple(boundaries), tuple(endpoints), notes


def quantize_transitions(boundaries, clips, fps: int = MERGE_FPS):
    """过渡时长与相邻 clip 时长**同一套量化**后再做 min 判据（违规拒收）。

    边界编号必须唯一且落在 ``1..n-1``；**缺项按 cut 处理**（与旧入口的
    ``transitions[i] if i < len(...) else ("cut", 0.0)`` 同义，文档化默认）。
    """
    durs = [float(c.duration_sec) for c in clips]
    out = []
    valid_indexes = set(range(1, len(clips)))
    seen: set[int] = set()
    for boundary in boundaries:
        index = int(boundary.index)
        if index not in valid_indexes:
            raise TimelineError(
                "boundary_index_out_of_range",
                f"边界编号必须在 1..{len(clips) - 1} 内，实际 {index}"
                f"（每个边界 i 连接 i-1 与 i）", index=index)
        if index in seen:
            raise TimelineError("duplicate_boundary_index", f"边界编号重复: {index}", index=index)
        seen.add(index)
    by_index = {int(b.index): b for b in boundaries}
    for i in sorted(valid_indexes):
        boundary = by_index.get(i, BoundaryTransition(i, BOUNDARY_CUT, 0.0))
        try:
            require_boundary_transition(boundary.kind)
        except MediaContractError as exc:
            raise TimelineError("illegal_transition_kind", str(exc), position=i) from exc
        tdur = 0.0 if boundary.kind == BOUNDARY_CUT else float(boundary.duration_sec)
        if boundary.kind == BOUNDARY_CUT:
            if tdur != 0:
                raise TimelineError("cut_with_duration",
                                    f"cut 边界的过渡时长必须为 0，实际 {tdur}", position=i)
            try:
                validate_transition_timing(0.0, durs[i - 1], durs[i])
            except MediaContractError as exc:
                raise TimelineError("invalid_transition_timing", str(exc), position=i) from exc
        elif tdur == 0:
            # tdur=0 是合法非负数（§S2）；编译器按无重叠处理（等价 cut，不静默 clamp）
            try:
                validate_transition_timing(0.0, durs[i - 1], durs[i])
            except MediaContractError as exc:
                raise TimelineError("invalid_transition_timing", str(exc), position=i) from exc
            tdur = 0.0
        else:
            try:
                tdur = quantized_duration_sec(tdur, fps)
            except MediaContractError as exc:
                raise TimelineError("invalid_transition_timing", str(exc), position=i) from exc
            try:
                validate_transition_timing(tdur, durs[i - 1], durs[i])
            except MediaContractError as exc:
                raise TimelineError("invalid_transition_timing", str(exc), position=i) from exc
        out.append(BoundaryTransition(i, boundary.kind, round(float(tdur), 9)))
    return tuple(out)


# ------------------------------------------------------------------ 校验

def validate_timeline(timeline: MergeTimeline) -> MergeTimeline:
    """编译前的完整校验；返回规范化（量化后）的时间线副本。违规即拒收。"""
    if not timeline.clips:
        raise TimelineError("no_clips", "时间线没有任何 clip（空片段）")
    fps = int(timeline.fps)
    if fps <= 0:
        raise TimelineError("invalid_fps", f"非法帧率: {timeline.fps!r}")
    if timeline.audio_mode not in ("native", "tts_mix"):
        raise TimelineError("invalid_audio_mode", f"非法音频模式: {timeline.audio_mode!r}")

    seen_ids: set[str] = set()
    seen_index: set[tuple[str, int]] = set()
    clips: list[ClipEntry] = []
    for clip in timeline.clips:
        if clip.kind not in ("hook", "shot"):
            raise TimelineError("unknown_clip_kind", f"非法 clip kind: {clip.kind!r}")
        key = (clip.kind, int(clip.shot_index))
        if key in seen_index:
            raise TimelineError("duplicate_clip_identity",
                                f"重复的 clip 身份: {clip.kind} index={clip.shot_index}")
        seen_index.add(key)
        if clip.stable_id in seen_ids:
            raise TimelineError("duplicate_clip_identity", f"重复的稳定身份: {clip.stable_id}")
        seen_ids.add(clip.stable_id)
        if not Path(clip.path).is_file():
            raise TimelineError("clip_missing", f"clip 文件不存在: {clip.path}",
                                path=str(clip.path))
        if not (isinstance(clip.raw_duration_sec, (int, float)) and clip.raw_duration_sec > 0
                and clip.raw_duration_sec == clip.raw_duration_sec):
            raise TimelineError("invalid_clip_duration",
                                f"clip 时长非法（探测失败/为 0）: {clip.path} "
                                f"raw={clip.raw_duration_sec!r}", path=str(clip.path))
        frames, normalized = normalize_duration(float(clip.raw_duration_sec), fps)
        clips.append(replace(clip, frames=frames, duration_sec=round(normalized, 9)))

    boundaries = quantize_transitions(timeline.boundary_transitions, clips, fps)

    n = len(clips)
    endpoints: list[EndpointEffect] = []
    seen_endpoints: set[int] = set()
    for effect in timeline.endpoint_effects:
        if effect.kind not in LEGAL_ENDPOINT_EFFECTS:
            raise TimelineError("illegal_endpoint_effect", f"非法端点效果: {effect.kind!r}")
        if not (0 <= int(effect.clip_index) < n):
            raise TimelineError("endpoint_out_of_range",
                                f"端点效果指向不存在的 clip: {effect.clip_index}")
        if effect.kind == ENDPOINT_FADE_IN and int(effect.clip_index) != 0:
            raise TimelineError("endpoint_effect_misplaced", "fade_in 只能作用首镜")
        if effect.kind == ENDPOINT_FADE_OUT and int(effect.clip_index) != n - 1:
            raise TimelineError("endpoint_effect_misplaced", "fade_out 只能作用末镜")
        if int(effect.clip_index) in seen_endpoints:
            raise TimelineError("duplicate_endpoint_effect",
                                f"同一镜只能有一个端点效果: clip {effect.clip_index}")
        seen_endpoints.add(int(effect.clip_index))
        if not (isinstance(effect.duration_sec, (int, float)) and effect.duration_sec > 0
                and effect.duration_sec == effect.duration_sec):
            raise TimelineError("invalid_endpoint_duration",
                                f"端点时长非法: {effect.duration_sec!r}")
        try:
            duration = quantized_duration_sec(float(effect.duration_sec), fps)
        except MediaContractError as exc:
            raise TimelineError("invalid_endpoint_duration", str(exc)) from exc
        clip_dur = float(clips[int(effect.clip_index)].duration_sec)
        if duration > clip_dur + _FRAME_EPS:
            raise TimelineError(
                "endpoint_longer_than_clip",
                f"端点时长 {duration}s 超过本镜时长 {clip_dur}s（端点不减总长，不得越界）")
        endpoints.append(EndpointEffect(effect.kind, round(duration, 9), int(effect.clip_index)))

    windows = compute_windows(clips, boundaries)
    total = compute_total_duration(clips, boundaries)
    audio_tracks = _validate_audio_tracks(timeline, clips, windows, total)
    overlays = validate_overlays(timeline, clips, windows)

    normalized_timeline = replace(
        timeline, clips=tuple(clips), boundary_transitions=boundaries,
        endpoint_effects=tuple(endpoints), audio_tracks=audio_tracks,
        overlays=overlays)
    if normalized_timeline.subtitle_plan is not None:
        from shared.subtitle_contract import validate_cues
        validate_cues(normalized_timeline.subtitle_plan.cues)
    return normalized_timeline


def _validate_audio_tracks(timeline: MergeTimeline, clips, windows, total: float):
    tracks: list[TimelineAudioTrack] = []
    seen: set[str] = set()
    for track in timeline.audio_tracks:
        if track.kind not in ("shot_tts", "narration"):
            raise TimelineError("unknown_audio_track_kind", f"非法音轨 kind: {track.kind!r}")
        if track.stable_id in seen:
            raise TimelineError("duplicate_audio_identity", f"重复的音轨身份: {track.stable_id}")
        seen.add(track.stable_id)
        if not Path(track.path).is_file():
            raise TimelineError("audio_track_missing", f"音轨文件不存在: {track.path}",
                                path=str(track.path))
        if track.kind == "shot_tts":
            if track.clip_index is None or not (0 <= int(track.clip_index) < len(clips)):
                raise TimelineError("audio_track_without_clip",
                                    f"逐镜 TTS 音轨未绑定合法 clip: {track.stable_id}")
        else:
            start, end = track.start_sec, track.end_sec
            if not (isinstance(start, (int, float)) and isinstance(end, (int, float))
                    and start == start and end == end and 0 <= float(start) < float(end)):
                raise TimelineError("invalid_audio_window",
                                    f"旁白音轨时窗非法: {track.stable_id} {start}..{end}")
            if float(end) > total + 1.0 / max(int(timeline.fps), 1):
                raise TimelineError(
                    "audio_window_out_of_timeline",
                    f"旁白音轨落点 {float(end):.3f}s 超出成片总长 {total:.3f}s")
        tracks.append(track)
    return tuple(tracks)


# ------------------------------------------------------------------ overlay 绑定

def validate_overlays(timeline: MergeTimeline, clips, windows) -> tuple[OverlayEntry, ...]:
    """overlay 身份/时窗校验：scope+revision 稳定 id、越界/重复/跨镜重叠一律拒收。"""
    by_shot = {(c.kind, int(c.shot_index)): (i, c) for i, c in enumerate(clips)}
    seen_ids: set[str] = set()
    entries: list[OverlayEntry] = []
    for overlay in timeline.overlays:
        if overlay.overlay_id in seen_ids:
            raise TimelineError("duplicate_overlay_id", f"重复的 overlay id: {overlay.overlay_id}")
        seen_ids.add(overlay.overlay_id)
        if overlay.kind not in ("image", "png_sequence"):
            raise TimelineError("unknown_overlay_kind", f"非法 overlay kind: {overlay.kind!r}")
        index = int(overlay.shot_index)
        # hook（index=0）是独立身份：按 kind 定位，不能落到第一正文镜头上
        key = ("hook", 0) if index == 0 and ("hook", 0) in by_shot else ("shot", index)
        hit = by_shot.get(key)
        if hit is None:
            raise TimelineError(
                "overlay_shot_not_found",
                f"overlay {overlay.overlay_id} 指向不存在的镜头 shot_index={index}"
                f"（hook 与正文镜头是不同身份）", shot_index=index)
        clip_index, clip = hit
        expected_id = clip.stable_id      # 身份单源：条目自己的稳定身份（含逐镜摘要）
        if overlay.stable_shot_id != expected_id:
            raise TimelineError(
                "overlay_identity_mismatch",
                f"overlay {overlay.overlay_id} 的稳定身份 {overlay.stable_shot_id!r} "
                f"与本次 scope/revision 不符（期望 {expected_id!r}，跨 revision 拒收）",
                shot_index=index)
        window = windows[clip_index]
        if not (overlay.start_sec == overlay.start_sec and overlay.end_sec == overlay.end_sec
                and float(overlay.start_sec) < float(overlay.end_sec)):
            raise TimelineError("invalid_overlay_window",
                                f"overlay {overlay.overlay_id} 时窗非法")
        eps = 1.0 / max(int(timeline.fps), 1)
        if float(overlay.start_sec) < window.start_sec - eps \
                or float(overlay.end_sec) > window.end_sec + eps:
            raise TimelineError(
                "overlay_out_of_window",
                f"overlay {overlay.overlay_id} 超出镜头窗口 "
                f"({overlay.start_sec:.3f}..{overlay.end_sec:.3f} vs "
                f"{window.start_sec:.3f}..{window.end_sec:.3f})")
        if overlay.kind == "image":
            if not Path(overlay.path).is_file():
                raise TimelineError("overlay_missing", f"overlay 素材不存在: {overlay.path}")
        else:
            pattern = Path(overlay.path)
            if "%" in pattern.name:
                if not list(pattern.parent.glob(pattern.name.replace("%05d", "*").replace("%d", "*"))):
                    raise TimelineError("overlay_missing",
                                        f"overlay PNG 序列没有帧: {overlay.path}")
            elif not pattern.is_file():
                raise TimelineError("overlay_missing", f"overlay 序列入口不存在: {overlay.path}")
        entries.append(overlay)
    # 跨镜 overlay 最终区间重叠 ⇒ 拒收（同镜多层按 layer 排序、允许相接）
    by_clip: dict[int, list[OverlayEntry]] = {}
    for overlay in entries:
        index = int(overlay.shot_index)
        key = ("hook", 0) if index == 0 and ("hook", 0) in by_shot else ("shot", index)
        by_clip.setdefault(by_shot[key][0], []).append(overlay)
    for clip_index, items in by_clip.items():
        ordered = sorted(items, key=lambda o: (float(o.start_sec), int(o.layer)))
        for prev, cur in zip(ordered, ordered[1:]):
            if float(cur.start_sec) < float(prev.end_sec) - _FRAME_EPS:
                raise TimelineError(
                    "overlay_overlap",
                    f"同一镜头的 overlay 最终区间重叠: {prev.overlay_id} 与 {cur.overlay_id}")
    return tuple(sorted(entries, key=lambda o: (int(o.shot_index), int(o.layer),
                                                float(o.start_sec))))


# ------------------------------------------------------------------ 动效计划（A 包）适配

MOTION_OVERLAY_PLAN_SCHEMA = "motion_overlay_plan.v1"


def overlays_from_motion_plan(data: dict, *, scope: str, script_revision: str,
                              clips, windows) -> tuple[OverlayEntry, ...]:
    """A 包渲染产物的 Step04 侧适配器（只有 title/letter 可以进成片）。

    接受的顶层 schema 为 ``motion_overlay_plan.v1``：A 的 render 步骤按
    MotionBundle v1 的 overlays 生成，并显式声明 scope/script_revision/fps 与
    已渲染 PNG 序列。未知 schema、越界/重复/跨 revision 一律拒收（不猜路径）。
    """
    data = data or {}
    schema = data.get("schema_version")
    if schema != MOTION_OVERLAY_PLAN_SCHEMA:
        raise TimelineError(
            "unsupported_motion_plan_schema",
            f"动效计划 schema 不认识: {schema!r}（期望 {MOTION_OVERLAY_PLAN_SCHEMA}）",
            schema_version=schema, stage="motion_plan")
    plan_scope = str(data.get("scope") or "")
    if not plan_scope:
        raise TimelineError("motion_plan_scope_missing",
                            "动效计划缺少 scope（无法核同源）", stage="motion_plan")
    if str(scope) and plan_scope != str(scope):
        raise TimelineError("motion_plan_scope_mismatch",
                            f"动效计划 scope 不符: {plan_scope!r} != {scope!r}",
                            stage="motion_plan")
    revision = str(data.get("script_revision") or "")
    if not revision or revision != str(script_revision):
        raise TimelineError("motion_plan_revision_mismatch",
                            f"动效计划 script_revision 不符: {revision!r} != {script_revision!r}",
                            stage="motion_plan")
    by_shot = {(c.kind, int(c.shot_index)): (i, c) for i, c in enumerate(clips)}
    overlays: list[OverlayEntry] = []
    for entry in (data.get("overlays") or []):
        if not isinstance(entry, dict):
            raise TimelineError("invalid_overlay_entry", "动效计划的 overlay 条目不是对象",
                                stage="motion_plan")
        index = int(entry.get("shot_index", -1))
        key = ("hook", 0) if index == 0 and ("hook", 0) in by_shot else ("shot", index)
        hit = by_shot.get(key)
        if hit is None:
            raise TimelineError("overlay_shot_not_found",
                                f"动效 overlay 指向不存在的镜头: shot_index={index}",
                                stage="motion_plan")
        clip_index, clip = hit
        window = windows[clip_index]
        local_start = float(entry.get("start_sec") or 0.0)
        local_end = float(entry.get("end_sec") or 0.0)
        if not (0 <= local_start < local_end):
            raise TimelineError("invalid_overlay_window",
                                f"动效 overlay 局部时窗非法: {local_start}..{local_end}",
                                stage="motion_plan")
        overlays.append(OverlayEntry(
            overlay_id=str(entry.get("overlay_id") or ""),
            stable_shot_id=clip.stable_id,   # 与 clip 条目同一身份单源
            shot_index=int(clip.shot_index),
            kind=str(entry.get("kind") or "png_sequence"),
            path=Path(str(entry.get("path") or "")),
            start_sec=window.start_sec + local_start,
            end_sec=window.start_sec + local_end,
            layer=int(entry.get("layer") or 0),
            spec_id=entry.get("spec_id"),
            fps=int(entry.get("fps") or (data.get("fps") or MERGE_FPS)),
            sha256=entry.get("sha256"),
        ))
    for overlay in overlays:
        if not overlay.overlay_id:
            raise TimelineError("invalid_overlay_entry", "动效 overlay 缺少 overlay_id",
                                stage="motion_plan")
    return tuple(overlays)


# ------------------------------------------------------------------ 候选清单（V/T）

def _read_json(path: Path) -> dict:
    p = Path(path)
    if not p.is_file():
        raise TimelineError("candidates_missing", f"候选清单不存在: {p}", path=str(p))
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        raise TimelineError("candidates_unparsable", f"候选清单解析失败: {p}: {exc}",
                            path=str(p)) from exc
    if not isinstance(data, dict):
        raise TimelineError("candidates_unparsable", f"候选清单不是 JSON 对象: {p}", path=str(p))
    return data


def identity_reason(identity: dict, audio_path=None) -> str:
    """声音富身份的**单源**判据（``shared.audio_identity``）；返回 "" = 合法.

    - 结构：``identity_problems``（可空字段 ``model_revision/instruction/rate/volume/
      speaker`` 为 null/空**不算缺**；``text_hash``/``text_sha256`` 一致性由它核）；
    - 关系：``verify_identity`` 核**侧车 ↔ 盘上媒体**（``artifact_hash``）以及与本次
      期望身份的逐字段一致——媒体被换过、侧车缺席都拒收（不拿文本 signature 顶替）。
    """
    from shared.audio_identity import (IDENTITY_INCOMPLETE, identity_missing_fields,
                                       verify_identity)
    if not identity:
        return (f"{IDENTITY_INCOMPLETE}：没有 audio_identity.v2 富身份"
                "（文本 signature 不能自行恢复富身份）")
    # 判缺**单源**在 T：只有 NONEMPTY_IDENTITY_FIELDS 缺了才算不完整；
    # rate/volume/instruction/model_revision/speaker 为 null/空是**合法事实**
    # （参数未生效 / 声线由 model_id 决定），本包不抄第二份清单。
    missing = identity_missing_fields(identity)
    if missing:
        return f"{IDENTITY_INCOMPLETE}：缺少必需身份字段 {missing}"
    if audio_path is None:
        return ""
    # 关系侧：侧车 ↔ 盘上媒体（artifact_hash）↔ 本次期望身份逐字段一致
    return verify_identity(Path(audio_path), identity)


@dataclass(frozen=True)
class CandidateInputs:
    """跨阶段候选输入（V 的视频 / T 的声音），由 F 的 resolver 解析后的 Step04 视图.

    - ``video_map``：``("hook", 0)`` 或 ``("shot", N)`` → 视频路径（按稳定 unit_id 取，不按数组位置）；
    - ``dialogue`` / ``narration``：镜头号 / 旁白编号 → 音轨路径；
    - ``identities``：unit_id → 声音富身份（``audio_identity.v2`` 字段集）；
    - ``resolved``：F resolver 的原始返回（含 manifest 路径与摘要，供留档）。
    """

    scope: dict
    script_revision: str
    source: str
    video_map: dict
    dialogue: dict           # shot_index → 音轨路径
    narration: dict          # 旁白编号 → 音轨路径
    identities: dict         # unit_id → 富身份
    dialogue_identity: dict  # shot_index → 富身份（消费侧按镜查）
    narration_identity: dict # 旁白编号 → 富身份
    resolved: dict


def resolve_merge_candidates(video_manifest, audio_manifest=None, *, script_revision: str,
                             expect_scope: dict | None = None,
                             context=None, experiment_root=None,
                             required_checks: tuple = ()) -> CandidateInputs:
    """**唯一**候选解析入口：委托 ``shared.candidate_inputs.resolve_candidate_inputs``.

    不按镜号猜路径、不接受任意文件绕过准入；本函数只做两件事：
    ① 把 F 的通用 unit 记录映射成 Step04 的（hook/正文镜头、对白/旁白）视图；
    ② 校声音富身份（``audio_identity.v2`` 字段集）——文本 signature 不能自行恢复富身份。

    音频编号来自生产者 manifest 的 ``narration_index``（旁白）或 ``shot_index``（对白）；
    缺编号即拒收（不按数组位置猜）。
    """
    from shared.candidate_inputs import resolve_candidate_inputs

    if audio_manifest is not None and video_manifest is None:
        raise TimelineError(
            "candidate_anchor_missing",
            "给声音候选时必须同时给视频候选 manifest"
            "（F 的 resolver 以视频 manifest 为身份锚：两侧 scope/revision 必须一致）",
            field_path="video_manifest")
    resolved = resolve_candidate_inputs(
        video_manifest, audio_manifest, context=context, experiment_root=experiment_root,
        expect_scope=expect_scope, expect_script_revision=script_revision,
        required_checks=tuple(required_checks))

    # 生产者的编号映射（resolver 只保证 unit_id/路径/摘要，编号仍来自 manifest 声明）
    raw_units: list = []
    for path in (video_manifest, audio_manifest):
        if path is None:
            continue
        data = _read_json(path)
        raw_units.extend(u for u in (data.get("units") or data.get("artifacts") or [])
                         if isinstance(u, dict))
    index_by_uid = {}
    for unit in raw_units:
        uid = unit.get("unit_id")
        if uid:
            index_by_uid[str(uid)] = unit

    video_map: dict = {}
    dialogue: dict = {}
    narration: dict = {}
    identities: dict = {}
    dialogue_identity: dict = {}
    narration_identity: dict = {}
    for uid, unit in (resolved.get("units") or {}).items():
        raw = index_by_uid.get(str(uid), {})
        if unit.get("video_path"):
            index = unit.get("shot_index")
            if index is None:
                index = raw.get("shot_index")
            if index is None:
                raise TimelineError(
                    "candidate_index_missing",
                    f"视频候选 {uid} 没有 shot_index（不按数组位置猜身份）", unit_id=uid)
            kind = "hook" if int(index) == 0 else "shot"
            video_map[(kind, int(index))] = Path(unit["video_path"])
            # 不 continue：同一条目标 manifest 里 video 与 audio 的 unit_id 可以相同
            # （F 的 resolver 会把声音记录并进同一条 unit）
        if not unit.get("audio_path"):
            continue
        kind = str(unit.get("kind") or raw.get("kind") or "")
        identity = dict(unit.get("voice_identity") or raw.get("voice_identity") or {})
        reason = identity_reason(identity, unit.get("audio_path") or unit.get("path"))
        if reason:
            raise TimelineError("identity_incomplete",
                                f"声音候选 {uid}: {reason}", unit_id=uid)
        if str(identity.get("script_revision")) not in ("", str(script_revision)):
            raise TimelineError("candidates_revision_mismatch",
                                f"声音候选 {uid} 的 revision 与本次不符", unit_id=uid)
        if kind == "narration":
            index = raw.get("narration_index", raw.get("index"))
            if index is None:
                raise TimelineError(
                    "candidate_index_missing",
                    f"旁白候选 {uid} 缺少 narration_index/index（不按数组位置猜编号）",
                    unit_id=uid)
            narration[int(index)] = Path(unit["audio_path"])
            narration_identity[int(index)] = identity
        elif kind == "dialogue":
            index = unit.get("shot_index", raw.get("shot_index"))
            if index is None:
                raise TimelineError("candidate_index_missing",
                                    f"对白候选 {uid} 缺少 shot_index", unit_id=uid)
            dialogue[int(index)] = Path(unit["audio_path"])
            dialogue_identity[int(index)] = identity
        else:
            raise TimelineError("invalid_candidate_unit",
                                f"声音候选 kind 非法: {kind!r}", unit_id=uid)
        identities[str(uid)] = identity

    if not video_map and audio_manifest is None:
        raise TimelineError("empty_candidates", "候选清单没有任何可用单元",
                            path=str(video_manifest))
    return CandidateInputs(scope=dict(resolved.get("scope") or {}),
                           script_revision=str(resolved.get("script_revision") or ""),
                           source=str(resolved.get("source") or ""), video_map=video_map,
                           dialogue=dialogue, narration=narration, identities=identities,
                           dialogue_identity=dialogue_identity,
                           narration_identity=narration_identity, resolved=resolved)


__all__ = [
    "BoundaryTransition", "ClipEntry", "ClipWindow", "EndpointEffect", "MergeTimeline",
    "MOTION_OVERLAY_PLAN_SCHEMA", "OverlayEntry", "SCHEMA_MERGE_TIMELINE", "TimelineAudioTrack",
    "TimelineError", "compute_total_duration", "identity_reason",
    "compute_windows", "normalize_duration",
    "CandidateInputs", "overlays_from_motion_plan", "quantize_transitions",
    "resolve_merge_candidates", "stable_shot_id",
    "transitions_from_legacy", "validate_overlays", "validate_timeline", "windows_from_legacy",
]
