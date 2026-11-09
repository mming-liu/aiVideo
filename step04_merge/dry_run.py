# -*- coding: utf-8 -*-
"""Step04 秒级离线预检报告（M4，2026-11-09）.

`--dry-run` 复用**生产同一条**时间线构建/校验链（clips → 转场 → 音轨 → overlay
→ 字幕），在调用 FFmpeg 编码前停下，输出结构化报告：

- 时间线总长、每镜窗口、边界转场、端点效果；
- 音轨（逐镜对白 / 图外旁白）身份与时间窗；
- 字幕 cue 数与来源、overlay 数；
- `network_calls=0`、`ffmpeg_encode_calls=0`，并明确 `would_write` 路径。

报告不含内容正文（只含摘要/计数），可安全落候选目录或打印。它**不**证明成片
可播放、音频混音正确或字幕字体渲染正确——那些仍需真实编码验收。
"""
from __future__ import annotations

import hashlib
import json
import time

SCHEMA = "step04_dry_run.v1"


def timeline_digest(timeline) -> str:
    blob = json.dumps(timeline.to_dict(), ensure_ascii=False, sort_keys=True,
                      default=str, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def build_report(timeline, *, output, srt_path, settings=None,
                 elapsed_ms: float | None = None,
                 media_prechecks=None) -> dict:
    """从已校验的 MergeTimeline 生成预检报告（纯读，不触碰文件）."""
    subtitle = timeline.subtitle_plan
    report = {
        "schema": SCHEMA,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "network_calls": 0,
        "ffmpeg_encode_calls": 0,
        "encoded": False,
        "timeline_validated": True,
        "timeline_digest": timeline_digest(timeline),
        "scope": timeline.scope,
        "script_revision": timeline.script_revision,
        "audio_mode": timeline.audio_mode,
        "width": timeline.width,
        "height": timeline.height,
        "fps": timeline.fps,
        "total_duration_sec": round(float(timeline.total_duration_sec), 6),
        "clip_count": len(timeline.clips),
        "clips": [{
            "shot_index": c.shot_index,
            "kind": c.kind,
            "stable_id": c.stable_id,
            "duration_sec": round(float(c.duration_sec), 6),
            "frames": int(c.frames),
            "has_audio": bool(c.has_audio),
            "source": c.source,
        } for c in timeline.clips],
        "windows": [{
            "clip_index": w.index,
            "start_sec": round(w.start_sec, 6),
            "end_sec": round(w.end_sec, 6),
        } for w in timeline.windows],
        "boundary_transitions": [{
            "index": b.index,
            "kind": b.kind,
            "duration_sec": round(float(b.duration_sec), 6),
        } for b in timeline.boundary_transitions],
        "endpoint_effects": [{
            "kind": e.kind,
            "duration_sec": round(float(e.duration_sec), 6),
            "clip_index": e.clip_index,
        } for e in timeline.endpoint_effects],
        "audio_tracks": [{
            "kind": t.kind,
            "stable_id": t.stable_id,
            "clip_index": t.clip_index,
            "start_sec": None if t.start_sec is None else round(float(t.start_sec), 6),
            "end_sec": None if t.end_sec is None else round(float(t.end_sec), 6),
            "has_sha256": bool(t.sha256),
        } for t in timeline.audio_tracks],
        "subtitle": {
            "source": None if subtitle is None else subtitle.source,
            "cue_count": 0 if subtitle is None else len(subtitle.cues),
            "fallback_count": 0 if subtitle is None else len(subtitle.fallbacks),
            "sha256": None if subtitle is None else subtitle.sha256,
        },
        "overlay_count": len(timeline.overlays),
        "would_write": {"final": str(output), "srt": str(srt_path)},
        "wrote_formal_outputs": False,
        "elapsed_ms": None if elapsed_ms is None else round(float(elapsed_ms), 1),
        "not_verified": [
            "真实编码/封装/时长/音画同步未验证（dry-run 不调用 FFmpeg 编码）",
            "字幕字体渲染未验证",
            "provider 语义与视频内容未验证",
        ],
    }
    if media_prechecks is not None:
        report["media_precheck"] = list(media_prechecks)
        report["media_precheck_warnings"] = sum(
            len(item.get("warnings") or []) for item in media_prechecks
            if isinstance(item, dict))
    if settings is not None:
        report["encode_settings"] = {
            "width": getattr(settings, "width", None),
            "height": getattr(settings, "height", None),
            "fps": getattr(settings, "fps", None),
            "has_subtitle_font": getattr(
                getattr(settings, "subtitle_style", None), "font", None) is not None,
        }
    return report


def write_report(report: dict, path) -> None:
    """原子写报告（只用于候选/预检目录，不写正式成片）."""
    import os
    from pathlib import Path
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)


__all__ = ["SCHEMA", "build_report", "timeline_digest", "write_report"]
