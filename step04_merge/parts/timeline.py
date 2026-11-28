# -*- coding: utf-8 -*-
"""S06: `step04_merge.main` 的 timeline 职责（2026-11-28 拆分）.

父模块保留配置/日志/运行状态与编排；本模块只放纯函数，父模块需要的运行值
通过 `_pv`/`_pc` 在调用点读取，导入期不回导父模块。
"""
from pathlib import Path
from step04_merge.stretch import has_audio_stream
from step04_merge.timeline import (
    ClipEntry, MergeTimeline, TimelineAudioTrack, TimelineError,
    stable_shot_id,
    transitions_from_legacy,
)
from shared.media_contract import (
    ENDPOINT_FADE_IN, ENDPOINT_FADE_OUT, LEGAL_BOUNDARY_TRANSITIONS,
    LEGAL_ENDPOINT_EFFECTS,
    MERGE_FPS,
)
import re
_PARENT = "step04_merge.main"


def _pv(name):
    import importlib
    return getattr(importlib.import_module(_PARENT), name)


def _pc(name, *args, **kwargs):
    return _pv(name)(*args, **kwargs)


class _ParentLogger:
    """把日志调用转发到父模块当前 logger（运行中会被 `_ensure_attempt` 替换）."""

    def __getattr__(self, item):
        return getattr(_pv("_logger"), item)


_logger = _ParentLogger()


def _parse_transition(text: str, default_dur: float = 0.5):
    """解析 clip_transition 字符串 → (kind, duration_seconds)【旧输入适配层】.

    kind: 'cut'(硬切) | 'fade_in'(首端点淡入) | 'fade_out'(末端点淡出) | xfade 转场名。

    2026-10-03（S 包，§15「转场合法集合」）：
    - 关键词归一表 ``_TRANSITION_MAP`` 保留为**旧输入适配层**（自由文本照旧）；
    - 显式 kind 写法（纯 ASCII 标识符）必须落在
      ``shared.media_contract`` 的合法集合内，**否则拒收**——不再静默回退 fade；
    - fade_in/fade_out 只有端点含义，位置不对由 timeline 层拒收。
    """
    text = (text or "").strip()
    if not text:
        return "cut", 0.0
    m = re.search(r"(\d+(?:\.\d+)?)\s*[秒s]", text)
    dur = float(m.group(1)) if m else default_dur
    has_transition_word = "转场" in text or m is not None

    kind = None
    # **先认显式合法名**（cut/wipeleft/radial/fade_in/fade_out…）：它们是已经规范化
    # 的 kind，不是"待适配的中文描述"。旧写法把它们一律落到 fade，既改了转场种类
    # 又可能改总时长/丢端点效果（2026-10-03 复核 P2-11）。
    if _EXPLICIT_KIND_RE.match(text) \
            and text in (LEGAL_BOUNDARY_TRANSITIONS | LEGAL_ENDPOINT_EFFECTS):
        kind = text
    if kind is None:
        for kw, xf in _TRANSITION_MAP:
            if kw in text:
                kind = xf
                break
    if kind is None:
        if _EXPLICIT_KIND_RE.match(text) and text not in LEGAL_BOUNDARY_TRANSITIONS \
                and text not in LEGAL_ENDPOINT_EFFECTS:
            raise TimelineError(
                "illegal_transition_kind",
                f"未知转场 kind（拒收，不静默回退 fade）: {text!r}；"
                f"合法集合: {sorted(LEGAL_BOUNDARY_TRANSITIONS | LEGAL_ENDPOINT_EFFECTS)}")
        kind = "fade"
    # 中文描述里的"硬切"仍按 cut；但**显式名**（已经是 kind）不受这层改写影响
    explicit = (kind is not None and kind == text
                and kind in (LEGAL_BOUNDARY_TRANSITIONS | LEGAL_ENDPOINT_EFFECTS))
    if not explicit:
        if "硬切" in text and not has_transition_word:
            kind = "cut"
        elif kind == "cut" and has_transition_word:
            kind = "fade"

    if kind == "cut":
        dur = 0.0
    else:
        # 端点效果（首淡入/末淡出）**有自己的时长**：它不减总长这件事由时间线
        # （`transitions_from_legacy` → `EndpointEffect`）负责，不在这里清成 0
        # ——清成 0 会被时间线按"端点时长必须为正"拒收（2026-10-04 复核 P1-7）。
        dur = max(0.1, min(dur, 1.5))
    return kind, dur

def _compute_clip_windows(durations, transitions):
    """Return per-clip final (start,end) and total duration.【旧签名适配层】

    xfade overlaps make cumulative-duration subtraction ambiguous.  The windows
    below are the single source shared by the ffmpeg filter and subtitles.
    2026-10-03：时间算术迁入 ``step04_merge.timeline``（同一套判据），本函数只做
    旧参数（旧 transitions 列表）到新表示的转换——旧 ``transitions[0]==fade_in`` /
    末尾 ``fade_out`` 迁成**端点效果**，其余位置原样作为边界过渡。

    **新旧差异（有意，§15「转场表示」的 golden 为准）**：旧实现把
    ``transitions[n-1]==("fade_out", d)`` **同时**当边界消费——kind 不在旧
    ``_VALID_XFADE`` 里 ⇒ 静默回退成 ``xfade=fade``（重叠 d 秒），随后再叠一次末端点；
    例：[2.0, 3.0] + 末淡出 0.5 在旧实现里总长 4.5。新契约把末端点与边界分开
    （端点不减总长），同一输入总长 5.0，差异 = 原 tdur（少一次 xfade）。
    这条差异在 ``tools/verify_merge_timeline.py`` 的旧入口对照里显式声明为允许差异。
    """
    from step04_merge.timeline import windows_from_legacy
    return windows_from_legacy(durations, transitions)

def _build_timeline(clips, durations, transitions, *, scope, script_revision,
                    clip_kinds=None, shot_indexes=None, audio_mode="native",
                    tts_paths=None, narration_tracks=None, subtitle_plan=None,
                    overlays=(), source=None, fps=MERGE_FPS, clip_has_audio=None,
                    shot_digests=None, tts_identities=None,
                    narration_identities=None, clip_pads=None,
                    tts_atempo=None) -> MergeTimeline:
    """旧参数（路径表 + 旧 transitions 列表）→ :class:`MergeTimeline`（唯一装配点）."""
    clip_list = [Path(c) for c in clips]
    n = len(clip_list)
    kinds = list(clip_kinds or ["shot"] * n)
    indexes = list(shot_indexes if shot_indexes is not None else range(n))
    boundaries, endpoints, notes = transitions_from_legacy(transitions, n_clips=n, fps=fps)
    for note in notes:
        _logger.warn(f"[WARN] {note}")

    entries: list[ClipEntry] = []
    for i in range(n):
        kind = str(kinds[i])
        index = int(indexes[i])
        digest = None
        if shot_digests:
            digest = shot_digests.get(index, shot_digests.get(str(index)))
        entries.append(ClipEntry(
            shot_index=index, kind=kind,
            # 身份接已有单源：scope + 整稿 revision + 逐镜语义摘要（per_shot_revisions）
            stable_id=stable_shot_id(scope, script_revision, kind, index, digest),
            path=clip_list[i], duration_sec=0.0,
            raw_duration_sec=float(durations[i]), frames=0,
            pad_sec=float((clip_pads or {}).get(i, 0.0) or 0.0),
            has_audio=bool(clip_has_audio[i]) if clip_has_audio is not None
            else has_audio_stream(clip_list[i])))

    tracks: list[TimelineAudioTrack] = []
    if audio_mode == "tts_mix":
        for i, path in enumerate(tts_paths or []):
            if not path:
                continue
            tracks.append(TimelineAudioTrack(
                kind="shot_tts", stable_id=f"{entries[i].stable_id}:tts",
                path=Path(path), clip_index=i, gain=_pv("_TTS_BASE_GAIN"),
                atempo=float((tts_atempo or {}).get(i, 1.0) or 1.0),
                identity=dict((tts_identities or {}).get(str(indexes[i])) or {}) or None))
    for track in (narration_tracks or []):
        tracks.append(TimelineAudioTrack(
            kind="narration", stable_id=f"narration:N{track['index']}",
            path=Path(track["path"]), start_sec=float(track["start"]),
            end_sec=float(track["end"]), gain=_pv("_NARRATION_GAIN"),
            identity=dict((narration_identities or {}).get(str(track["index"])) or {}) or None))

    return MergeTimeline(
        scope=scope, script_revision=script_revision, width=_pv("OUT_W"), height=_pv("OUT_H"), fps=fps,
        clips=tuple(entries), boundary_transitions=boundaries, endpoint_effects=endpoints,
        audio_mode=audio_mode, audio_tracks=tuple(tracks), subtitle_plan=subtitle_plan,
        overlays=tuple(overlays), source=dict(source or {}))

def _declared_black_windows(timeline) -> list:
    """时间线**声明**的黑场窗口（fadeblack/fadewhite 转场 + 首尾端点淡变）.

    终检只把"没声明过的黑场"判成缺陷；这是"黑场闪白"这类有意转场不被误杀的唯一判据。
    """
    windows = list(getattr(timeline, "windows", ()) or ())
    out: list = []
    for boundary in getattr(timeline, "boundary_transitions", ()) or ():
        if str(boundary.kind) not in ("fadeblack", "fadewhite"):
            continue
        idx = int(boundary.index)
        if 0 <= idx < len(windows):
            start = float(windows[idx].start_sec)
            out.append([start, start + float(boundary.duration_sec)])
    for effect in getattr(timeline, "endpoint_effects", ()) or ():
        duration = float(effect.duration_sec)
        if effect.kind == ENDPOINT_FADE_IN:
            out.append([0.0, duration])
        elif effect.kind == ENDPOINT_FADE_OUT:
            total = float(getattr(timeline, "total_duration_sec", 0.0) or 0.0)
            out.append([max(0.0, total - duration), total])
    return out

def _color_filter() -> str:
    """既定调色（配置化；与 merge_core 的环境变量单源）."""
    from step04_merge.merge_core import CONTRAST, SATURATION, VIGNETTE_ANGLE
    color_filter = f"eq=contrast={CONTRAST}:saturation={SATURATION}"
    if VIGNETTE_ANGLE:
        color_filter += f",vignette=angle={VIGNETTE_ANGLE}"
    return color_filter

def _assert_step02b_scope(report: dict, only_indices, script: dict) -> None:
  """step02b 成品报告的范围核对（与 step02b 的 _assert_step02a_scope 对称）.

  S19-3 防穿透：`step02b --shots 1` 会写出只覆盖 shot 1 的 PASS 报告；若全章
  step04 合并直接采信，盘上残留的旧片段会被当成整章通过。无 scope 的旧报告
  按全章处理（保持旧行为）。
  """
  from shared.quality_gate import QualityGateError

  scope = (report or {}).get("scope") or {}
  scoped = {int(i) for i in (scope.get("shots") or [])}
  if not scoped:
    return
  if only_indices is None:
    wanted = {int(s.get("index", 0)) for s in (script.get("shots") or [])
              if isinstance(s, dict)}
    if script.get("hook"):
      wanted.add(0)
  else:
    wanted = {int(i) for i in only_indices}
  missing = sorted(wanted - scoped)
  if missing:
    raise QualityGateError(
        "step02b 验收范围不含本次请求的镜头: %s（报告覆盖 %s）——请重跑 step02b"
        % (missing, sorted(scoped)))

_EXPLICIT_KIND_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

_TRANSITION_MAP = (
    # 黑场闪白 / 冲击转场
    ("黑场闪白", "fadeblack"),
    ("画面冲击", "fadeblack"),
    ("冲击转场", "fadeblack"),
    ("光线闪白", "fadewhite"),
    ("闪白", "fadewhite"),
    # 推移 / 推镜 / 拉远
    ("推移转场", "slideleft"),
    ("推近匹配", "smoothleft"),
    ("拉远揭示", "smoothright"),
    ("推移", "slideleft"),
    # 擦拭 / 扫光
    ("擦拭", "wipeleft"),
    ("扫光", "wipeleft"),
    ("物体擦拭", "wipeup"),
    # 平滑 / 柔化 / 虚化
    ("焦点虚化", "smoothup"),
    ("模糊叠化", "smoothdown"),
    ("平滑", "smoothleft"),
    ("柔化", "smoothdown"),
    # 旋转 / 径向
    ("旋转", "radial"),
    ("光圈", "radial"),
    ("径向", "radial"),
    # 叠化 / 溶解
    ("叠化", "fade"),
    ("dissolve", "fade"),
    ("淡入淡出", "fade"),
    ("交叉溶解", "fade"),
    # 匹配 / 空镜
    ("空镜匹配", "fade"),
    # 端点（首淡入 / 末淡出）：**排在"淡入淡出"之后**，否则长词会被短词先截走。
    # 与显式名 fade_in/fade_out 同一语义（各自带自身时长，不减总长）。
    ("淡入", ENDPOINT_FADE_IN),
    ("淡出", ENDPOINT_FADE_OUT),
    ("匹配剪辑", "fade"),
    ("动作匹配", "fade"),
    # 淡入 / 淡出
    ("淡入", "fade_in"),
    ("淡出", "fade_out"),
    # 硬切 / 跳跃
    ("硬切", "cut"),
    ("jump", "cut"),
    ("跳跃剪辑", "cut"),
)
