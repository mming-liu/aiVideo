# -*- coding: utf-8 -*-
"""声音准备与实测（S05）：独立 TTS、图外旁白、原生对白、无声音要求.

把 Step01 的声音单元与 Step03 的可用窗口变成**可核对、可反馈**的记录：

- ``required_playback_sec``：本单元需要播多久（实测优先，未实测明确记未验证）；
- ``actual_sec``：实际时长（来自本地音频探测，不猜）；
- ``available_window_sec``：本镜可用窗口（与 Step04 共用时间线口径）；
- ``conflicts``：两种对白来源、旁白叠加超窗、缺音轨、原生对白未核实。

所有时长冲突都返回**受影响的 unit_id 与 shot_index**。改词/换说话人/改内容属于
Step01，不在这里静默改写。

本模块不调用任何 TTS/媒体 provider；``measure_units`` 只探测调用方给出的本地
音频文件（ffprobe）。plan-only 与真实生成共用 ``build_voice_preparation``。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

SCHEMA = "voice_preparation.v1"

#: 无声音要求时也必须留下确认记录（不是空结果冒充通过）。
NO_AUDIO_CONFIRMATION = "no_audio_required_confirmed_at_prepare"

_KINDS = ("tts_dialogue", "narration", "native_dialogue")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _sha256_file(path: str | None) -> str | None:
    if not path:
        return None
    try:
        p = Path(path)
        if not p.is_file():
            return None
        digest = hashlib.sha256()
        with open(p, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _probe_duration(path: str | None) -> tuple[float | None, str]:
    """用本地 ffprobe 读实际时长；不可用/缺文件时明确返回原因，不猜."""
    if not path:
        return None, "audio_path_missing"
    if not Path(path).is_file():
        return None, "audio_file_missing"
    try:
        from shared.media_inspect import ffprobe_executable, probe_media
        if not ffprobe_executable():
            return None, "ffprobe_unavailable"
        info = probe_media(path)
        duration = _number(info.get("duration_sec") if isinstance(info, dict) else None)
        if duration is None and isinstance(info, dict):
            duration = _number((info.get("format") or {}).get("duration"))
        return (duration, "") if duration is not None else (None, "duration_unreadable")
    except Exception as exc:  # noqa: BLE001
        return None, f"probe_failed: {str(exc)[:200]}"


def _source_kind(unit: dict) -> str:
    kind = _text(unit.get("kind") or unit.get("source_kind")).lower()
    if kind in _KINDS:
        return kind
    if "native" in kind:
        return "native_dialogue"
    if "narration" in kind:
        return "narration"
    if "tts" in kind or "dialogue" in kind:
        return "tts_dialogue"
    return "unknown"


def _outline_texts(script: dict) -> tuple[list[str], list[str]]:
    outline = script.get("chapter_outline") or {}
    dialogue = [_text(x) for x in (outline.get("dialogue_lines") or []) if _text(x)]
    narration = []
    for entry in outline.get("narration_lines") or []:
        if isinstance(entry, dict):
            text = _text(entry.get("text") or entry.get("source"))
        else:
            text = _text(entry)
        if text:
            narration.append(text)
    return dialogue, narration


def _unit_identity_issues(unit: dict, script: dict, dialogue: list[str],
                          narration: list[str], known_characters: set[str]) -> list[dict]:
    issues: list[dict] = []
    kind = _source_kind(unit)
    text = _text(unit.get("text") or unit.get("echo_text"))
    speaker = _text(unit.get("speaker"))
    uid = _text(unit.get("unit_id") or unit.get("unit_uid"))
    if not text:
        issues.append({"code": "voice_text_missing", "unit_id": uid,
                       "path": f"voice_units[{uid}].text", "message": "声音单元正文为空"})
    elif kind in ("tts_dialogue", "native_dialogue") and dialogue and text not in dialogue:
        issues.append({"code": "voice_text_mismatch", "unit_id": uid,
                       "path": f"voice_units[{uid}].text",
                       "message": "独立/原生对白正文与 chapter_outline.dialogue_lines 不一致"})
    elif kind == "narration" and narration and text not in narration:
        issues.append({"code": "voice_text_mismatch", "unit_id": uid,
                       "path": f"voice_units[{uid}].text",
                       "message": "旁白正文与 chapter_outline.narration_lines 不一致"})
    if kind in ("tts_dialogue", "native_dialogue") and speaker \
            and known_characters and speaker not in known_characters:
        issues.append({"code": "voice_speaker_unknown", "unit_id": uid,
                       "path": f"voice_units[{uid}].speaker",
                       "message": f"说话人 {speaker!r} 不在 characters 身份表中"})
    return issues


def build_voice_preparation(script: dict, *, scope: dict, windows: dict | None = None,
                            audio_paths: dict | None = None,
                            require_native_verified: bool = True,
                            require_audio_artifacts: bool = False) -> dict:
    """构建声音准备报告（纯离线；``audio_paths`` 给定时探测实际时长）.

    ``windows`` 是 ``{shot_index: available_sec}``；缺省用 ``shot.duration_sec``。
    ``audio_paths`` 是 ``{unit_id: path}``，用于实测。
    """
    if not isinstance(script, dict):
        raise ValueError("script 必须是对象")
    outline = script.get("chapter_outline") or {}
    units_raw = outline.get("voice_units") if isinstance(outline, dict) else None
    units_raw = units_raw if isinstance(units_raw, list) else []
    shots = {s.get("index"): s for s in script.get("shots") or [] if isinstance(s, dict)}
    dialogue, narration = _outline_texts(script)
    known = {_text(c.get("identifier") or c.get("name")) if isinstance(c, dict) else _text(c)
             for c in script.get("characters") or []}
    windows = windows or {}
    audio_paths = audio_paths or {}
    units: list[dict] = []
    conflicts: list[dict] = []
    by_shot: dict[Any, list[dict]] = {}
    for i, raw in enumerate(units_raw):
        if not isinstance(raw, dict):
            continue
        uid = _text(raw.get("unit_id") or raw.get("unit_uid") or f"voice-{i + 1:02d}")
        kind = _source_kind(raw)
        shot_index = raw.get("shot_index")
        declared = _number(raw.get("actual_sec") or raw.get("required_playback_sec"))
        path = audio_paths.get(uid) or raw.get("audio_path") or raw.get("path")
        measured = None
        probe_error = ""
        if audio_paths:
            measured, probe_error = _probe_duration(path)
        actual = measured if measured is not None else declared
        window = _number(windows.get(str(shot_index))) if windows else None
        if window is None:
            shot = shots.get(shot_index) or {}
            window = _number(shot.get("duration_sec") or shot.get("duration"))
        unit_window = _number(raw.get("window_sec"))
        effective_window = (min(window, unit_window)
                            if window is not None and unit_window is not None
                            else (window if window is not None else unit_window))
        unit = {
            "unit_id": uid,
            "kind": kind,
            "speaker": _text(raw.get("speaker")),
            "text": _text(raw.get("text") or raw.get("echo_text")),
            "shot_index": shot_index,
            "required_playback_sec": declared,
            "declared_sec": declared,
            "actual_sec": actual,
            "available_window_sec": effective_window,
            "shot_audio_window_sec": window,
            "unit_window_sec": unit_window,
            "verified": measured is not None,
            "measurement": ("ffprobe" if measured is not None
                            else (probe_error or "not_measured")),
            "audio_path": str(path) if path else "",
            "audio_sha256": _sha256_file(str(path)) if path else None,
            "identity": {
                "text_sha256": hashlib.sha256(_text(raw.get("text")).encode("utf-8")).hexdigest()
                if _text(raw.get("text")) else None,
                "speaker": _text(raw.get("speaker")),
                "script_revision": script.get("script_revision"),
                "source": scope.get("source") if isinstance(scope, dict) else None,
            },
        }
        units.append(unit)
        by_shot.setdefault(shot_index, []).append(unit)
        conflicts.extend(_unit_identity_issues(raw, script, dialogue, narration, known))
        if kind == "native_dialogue" and require_native_verified:
            if raw.get("verified") is not True or actual is None or actual <= 0:
                conflicts.append({
                    "code": "native_dialogue_unverified", "unit_id": uid,
                    "shot_index": shot_index, "path": f"voice_units[{i}].verified",
                    "message": "原生对白未核实完整性（不能仅凭音轨存在判通过）",
                })
        if kind in ("tts_dialogue", "narration") and effective_window is not None \
                and actual is not None and actual > effective_window + 1e-9:
            conflicts.append({
                "code": "audio_window_overflow", "unit_id": uid,
                "shot_index": shot_index, "path": f"voice_units[{i}].actual_sec",
                "message": f"{uid} 实际 {actual:.2f}s 超过可用窗口 {effective_window:.2f}s",
                "overflow_sec": round(actual - effective_window, 6),
            })
    # 逐镜：两种对白来源 / 独立声音合计超窗 / 缺音轨 / 有声音要求但无单元。
    for shot_index, items in by_shot.items():
        kinds = {u["kind"] for u in items}
        if "native_dialogue" in kinds and "tts_dialogue" in kinds:
            conflicts.append({
                "code": "dual_dialogue_source", "shot_index": shot_index,
                "unit_ids": [u["unit_id"] for u in items],
                "path": f"shots[{shot_index}].audio",
                "message": "同一镜头同时存在原生对白与独立 TTS 对白，发声归属不唯一",
            })
        for unit in items:
            if (require_audio_artifacts and unit["kind"] in ("tts_dialogue", "narration")
                    and not unit["audio_path"]):
                conflicts.append({
                    "code": "audio_track_missing", "unit_id": unit["unit_id"],
                    "shot_index": shot_index, "path": f"voice_units[{unit['unit_id']}]",
                    "message": "需要独立音轨的声音单元没有音频产物",
                })
        independent = [u for u in items if u["kind"] in ("tts_dialogue", "narration")]
        total = sum(u["actual_sec"] or 0.0 for u in independent)
        window = independent[0]["shot_audio_window_sec"] if independent else None
        if window is not None and total > window + 1e-9:
            conflicts.append({
                "code": "audio_window_overflow", "shot_index": shot_index,
                "unit_ids": [u["unit_id"] for u in independent],
                "path": f"shots[{shot_index}].audio_window",
                "message": f"镜头 {shot_index} 独立声音合计 {total:.2f}s 超过窗口 {window:.2f}s",
                "overflow_sec": round(total - window, 6),
            })
    # 图外旁白必须有自己的 TTS 音轨；对白可能由视频原生发声（是否独立由音频策略
    # 决定），所以没有 voice_units 不能一概判缺。旁白缺失才是确定缺口。
    requires_voice = bool(narration)
    no_audio = not units
    if requires_voice and no_audio:
        conflicts.append({
            "code": "audio_units_missing", "path": "chapter_outline.voice_units",
            "message": "计划有对白/旁白，但没有任何声音单元（不是合法的无声音要求）",
        })
    confirmation = None
    if no_audio and not requires_voice:
        confirmation = {"code": NO_AUDIO_CONFIRMATION, "status": "confirmed",
                        "reason": "计划没有对白/旁白条目，执行准备确认无声音要求"}
    codes = sorted({c["code"] for c in conflicts})
    return {
        "schema": SCHEMA,
        "status": "blocked" if conflicts else "ready",
        "scope": dict(scope or {}),
        "script_revision": script.get("script_revision"),
        "units": units,
        "conflicts": conflicts,
        "codes": codes,
        "no_audio_confirmation": confirmation,
        "by_shot": {str(k): [u["unit_id"] for u in v] for k, v in by_shot.items()},
        "unverified": [u["unit_id"] for u in units if not u["verified"]],
    }


def measure_units(report: dict, audio_paths: dict) -> dict:
    """对已有报告回填实测时长（只探测本地文件，不调用 TTS）."""
    import copy
    updated = copy.deepcopy(report)
    by_id = {u["unit_id"]: u for u in updated.get("units") or []}
    for uid, path in (audio_paths or {}).items():
        unit = by_id.get(str(uid))
        if unit is None:
            continue
        duration, error = _probe_duration(str(path))
        unit["audio_path"] = str(path)
        unit["audio_sha256"] = _sha256_file(str(path))
        if duration is None:
            unit["verified"] = False
            unit["measurement"] = error
            continue
        unit["actual_sec"] = duration
        unit["verified"] = True
        unit["measurement"] = "ffprobe"
    updated["unverified"] = [u["unit_id"] for u in updated.get("units") or []
                             if not u.get("verified")]
    return updated


def feedback_for_conflicts(report: dict) -> list[dict]:
    """把冲突映射到责任阶段：时长/音轨 → S05/S06；正文/说话人 → Step01."""
    step01 = {"voice_text_mismatch", "voice_speaker_unknown", "dual_dialogue_source"}
    out: list[dict] = []
    for conflict in report.get("conflicts") or []:
        code = str(conflict.get("code") or "")
        route = "step01" if code in step01 else "voice_execution"
        out.append({"code": code, "route": route,
                    "unit_id": conflict.get("unit_id"),
                    "shot_index": conflict.get("shot_index"),
                    "path": conflict.get("path"),
                    "action": ("revise_text_or_speaker" if route == "step01"
                               else "remeasure_or_rearrange_voice")})
    return out


__all__ = [
    "SCHEMA", "NO_AUDIO_CONFIRMATION", "build_voice_preparation", "measure_units",
    "feedback_for_conflicts",
]
