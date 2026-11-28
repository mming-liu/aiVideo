# -*- coding: utf-8 -*-
"""S06: `step04_merge.main` 的 declarations 职责（2026-11-28 拆分）.

父模块保留配置/日志/运行状态与编排；本模块只放纯函数，父模块需要的运行值
通过 `_pv`/`_pc` 在调用点读取，导入期不回导父模块。
"""
from pathlib import Path
from step04_merge.timeline import (
    TimelineError,
)
from shared.media_contract import (
    ENDPOINT_FADE_IN, MERGE_FPS,
)
import json
from step04_merge.parts.track_collector import _resolve_dialogue_path  # noqa: F401

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


def _source_declarations(timeline, script, shots) -> dict:
    """来源**声明**的时间区间与内部切点（B 逐字复制；**不**由检测倒推）.

    - ``allowed_intervals``：本产物自己声明的合法非常规区间——端点淡入/淡出的黑场段
      （``kind="black"``，来自 timeline 的端点效果）与边界过渡重叠段
      （``kind="transition"``，来自边界表示）；每条带 ``source_locator``（RFC6901）。
    - ``declared_internal_cuts``：step01 的时间语义单源（``shot["temporal_plan"]["events"]``）
      声明的镜内切点；零长区间（start==end）表示一个切点。
    - ``prohibited_kinds``：来源**没有**声明禁用类目时为空表——不猜、不倒推。
    """
    windows = timeline.windows
    allowed: list = []
    for i, effect in enumerate(timeline.endpoint_effects):
        window = windows[int(effect.clip_index)]
        if effect.kind == ENDPOINT_FADE_IN:
            start, end = window.start_sec, window.start_sec + effect.duration_sec
        else:
            start, end = window.end_sec - effect.duration_sec, window.end_sec
        allowed.append({"kind": "black", "start_sec": round(start, 6),
                        "end_sec": round(end, 6),
                        "source_locator": f"/endpoint_effects/{i}"})
    for i, boundary in enumerate(timeline.boundary_transitions):
        if float(boundary.duration_sec) <= 0:
            continue
        window = windows[int(boundary.index)]
        allowed.append({"kind": "transition", "start_sec": round(window.start_sec, 6),
                        "end_sec": round(window.start_sec + float(boundary.duration_sec), 6),
                        "source_locator": f"/boundary_transitions/{i}"})
    cuts: list = []
    for k, shot in enumerate(shots or []):
        if not isinstance(shot, dict):
            continue
        plan = shot.get("temporal_plan")
        events = plan.get("events") if isinstance(plan, dict) else None
        if not events:
            continue
        index = int(shot.get("index", k))
        hit = timeline.clip_for_shot(index)
        if hit is None:
            continue
        window = windows[timeline.clips.index(hit)]
        for j, event in enumerate(events):
            if not isinstance(event, dict):
                continue
            try:
                at = float(event.get("start_sec"))
            except (TypeError, ValueError):
                continue
            absolute = round(window.start_sec + at, 6)
            cuts.append({"kind": "cut", "shot_index": index, "start_sec": absolute,
                         "end_sec": absolute,
                         "source_locator": f"/shots/{k}/temporal_plan/events/{j}"})
    return {"allowed_intervals": allowed, "declared_internal_cuts": cuts,
            "prohibited_kinds": []}

def _detector_parameters(timeline) -> dict:
    """给 B 的检测器参数（**来源声明**的一部分，B 原样使用）.

    ffmpeg blackdetect 的默认 ``min_duration`` 是 2.0s——端点淡入/淡出（常见 0.5s）
    与边界过渡在默认值下**检不出**（B 不伪造事件，但也看不到）。这里按本产物自己
    声明的最短黑场区间取一半（不低于一帧），让声明的区间在 B 侧可判读。
    """
    fps = int(timeline.fps or MERGE_FPS)
    floor = 1.0 / fps
    declared = [float(e.duration_sec) for e in timeline.endpoint_effects
                if float(e.duration_sec) > 0]
    if declared:
        value = max(floor, round(min(declared) / 2.0, 3))
    else:
        value = max(floor, 0.2)
    return {"black": {"min_duration_sec": value}}

def _load_validated_timings(options, audio_mode):
  """--subtitle-timings（C 包校验后的对齐结果）：只在 tts_mix 独立对白轨下受支持."""
  if options is None or options.subtitle_timings is None:
    return None
  if audio_mode != "tts_mix":
    _logger.warn("[WARN] --subtitle-timings 只在 tts_mix 独立对白轨下受支持"
                 "（native 对白/图外旁白不改字幕产品语义）；本次不消费该候选")
    return None
  path = Path(options.subtitle_timings)
  if not path.is_file():
    raise TimelineError("subtitle_timings_missing", f"对齐候选不存在: {path}", path=str(path))
  try:
    return json.loads(path.read_text(encoding="utf-8"))
  except Exception as exc:
    raise TimelineError("subtitle_timings_unparsable",
                        f"对齐候选解析失败: {path}: {exc}", path=str(path)) from exc

def _shot_audio_hashes(valid_shots, resolve) -> dict:
  """逐镜对白轨的实际摘要（对齐候选的音频身份核对用；只在有候选时计算）."""
  from shared import enhancement_io as eio
  hashes = {}
  for shot in valid_shots:
    index = shot.get("index", 0)
    path = _resolve_dialogue_path(index, resolve)
    if path is not None and Path(path).is_file():
      hashes[str(index)] = eio.sha256_file(Path(path))
  return hashes

def _assert_voice_gate(chapter_dir, *, script_revision: str, voice_required: bool,
                       dry_run: bool) -> dict:
  """Step E 声音准入的唯一接线点（Step 04 正式合成入口）.

  正式合成（``dry_run=False``）要求 ``voice_state.json`` 为 ``confirmed`` 且
  ``script_revision`` 与本次正式稿一致；``prepared`` / 缺失 / 版本漂移一律
  fail-closed 拒收（``VoiceStateError``）。``--dry-run`` 只推演时间线，不消费
  音频字节，允许在 ``prepared`` 下继续，但仍把差异打进日志。
  """
  from shared import voice_state
  try:
    report = voice_state.assert_voice_state(
        Path(chapter_dir) / voice_state.FILENAME, script_revision=script_revision,
        voice_required=voice_required, dry_run=dry_run)
  except voice_state.VoiceStateError as exc:
    _logger.error("[VOICE-STATE] 拒收（%s）：%s" % (exc.code, exc.message))
    raise
  _logger.info("[VOICE-STATE] status=%s gate=%s units=%d/%d revision=%s reasons=%s"
               % (report.get("status"), report.get("gate"),
                  report.get("units_confirmed"), report.get("units_required"),
                  str(report.get("script_revision") or "")[:12],
                  report.get("reasons") or []))
  return report
