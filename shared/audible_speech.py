# -*- coding: utf-8 -*-
"""声音承载回读：声明了人物开口，却没有可听 SpeechUnit（2026-11-19 修）.

2026-11-19 修正：旧实现只看计划记录 `voice_unit_records`，**不消费**实际
`SpeechUnit`；因此“计划写了、最终没产出”或“同镜只有不相关旁白”会漏报。
现在以 `build_speech_units()` 的实际输出为准，计划记录只用于把旁白映射到承载镜。
"""
from __future__ import annotations

import os
import re

_SPEECH_VERBS = ("开口", "说道", "道：", "喊道", "喝道", "骂道", "吩咐",
                 "威胁", "喊话", "呼喊", "厉声", "低声说", "出声")
_ENV_ONLY = ("箫声", "蝉鸣", "风声", "马蹄", "车轮", "鞭响", "衣料", "酒液",
             "瓷盏", "炭炉", "呼吸", "抽泣")


def _speaker_hint(value: str) -> str:
    text = re.sub(r"^(人声\s*=\s*)", "", value or "").strip()
    text = re.split(r"[，,；;（(]", text, maxsplit=1)[0]
    for cut in ("压低", "内心", "厉色", "低声", "沉声", "冷声"):
        text = text.split(cut, 1)[0]
    return text.strip("：: ")


def _speech_declarations(shot: dict) -> list[dict]:
    out = []
    audio = str(shot.get("audio_cue") or "")
    m = re.search(r"人声\s*=\s*([^；;]+)", audio)
    if m:
        value = m.group(1).strip()
        if value and value not in ("无", "无。", "-") and any(v in value for v in _SPEECH_VERBS):
            out.append({"field": "audio_cue", "text": value,
                        "speaker_hint": _speaker_hint(value),
                        "requires_dialogue": not any(k in value for k in ("旁白", "内心"))})
    motion = str(shot.get("motion_desc") or "")
    if any(v in motion for v in ("开口", "说道", "道：", "喊道", "喝道",
                                 "骂道", "吩咐", "威胁", "喊话", "呼喊")):
        if not any(v in motion for v in _ENV_ONLY):
            out.append({"field": "motion_desc", "text": motion,
                        "speaker_hint": "", "requires_dialogue": True})
    return out


def _actual_units(script: dict, units, voice_unit_records):
    if units is None:
        try:
            from step03_tts_audio.shot_tts import build_speech_units
            units = build_speech_units(script)
        except Exception:  # noqa: BLE001
            units = []
    if voice_unit_records is None:
        try:
            from step01_script_gen.plan_feasibility import voice_units
            voice_unit_records = voice_units(
                script.get("chapter_outline"), script.get("shots") or [])
        except Exception:  # noqa: BLE001
            voice_unit_records = []
    by_narr = {(r.get("kind"), r.get("index")): r for r in voice_unit_records or []}
    actual = []
    for u in units or []:
        kind = str(getattr(u, "kind", "") or "")
        sidx = getattr(u, "source_index", None)
        role = str(getattr(u, "voice_role", "") or getattr(u, "speaker", "") or "")
        text = str(getattr(u, "text", "") or "")
        if kind == "dialogue":
            try:
                actual.append({"kind": "dialogue", "shot": int(sidx),
                               "role": role, "text": text})
            except (TypeError, ValueError):
                continue
        elif kind == "narration":
            rec = by_narr.get(("narration", sidx)) or {}
            for sid in rec.get("shot_indexes") or []:
                try:
                    actual.append({"kind": "narration", "shot": int(sid),
                                   "role": role, "text": text})
                except (TypeError, ValueError):
                    continue
    return actual


def missing_audible_speech_units(script: dict, units=None, *,
                                 voice_unit_records=None,
                                 audio_mode: str | None = None) -> list[dict]:
    """返回 ``missing_audible_speech_unit`` 告警列表（只读、零调用）."""
    mode = (audio_mode or os.getenv("MERGE_AUDIO_MODE", "native")).strip().lower()
    if mode != "tts_mix":
        return []
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    actual = _actual_units(script, units, voice_unit_records)
    warnings = []
    for shot in shots:
        idx = int(shot.get("index") or 0)
        decls = _speech_declarations(shot)
        if not decls:
            continue
        lines = (shot.get("plan_refs") or {}).get("lines") or []
        for d in decls:
            hint = d.get("speaker_hint") or ""
            if d.get("requires_dialogue"):
                candidates = [a for a in actual
                              if a["shot"] == idx and a["kind"] == "dialogue"]
            else:
                candidates = [a for a in actual if a["shot"] == idx]
            if hint:
                role_ok = [a for a in candidates
                           if not a.get("role") or a.get("role") == hint]
                role_bad = bool(candidates) and not role_ok
                candidates = role_ok
            else:
                role_bad = False
            if candidates:
                continue
            severity = "hard" if lines and d.get("requires_dialogue") else "advisory"
            detail = ("镜头声明人物开口/威胁，但实际 SpeechUnit 没有落在该镜"
                      if not role_bad else
                      "镜头有 SpeechUnit，但角色/声线与声明不符")
            warnings.append({
                "code": "missing_audible_speech_unit",
                "severity": severity,
                "shot": idx,
                "field": d["field"],
                "speaker_hint": hint,
                "declaration": d["text"][:160],
                "detail": detail,
                "actual_units_on_shot": len([a for a in actual if a["shot"] == idx]),
            })
    return warnings


def check_missing_audible_speech(script: dict, units=None, **kwargs) -> list[dict]:
    return missing_audible_speech_units(script, units, **kwargs)
