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


def _story_role(shot: dict, plan: dict, characters) -> str:
    """从**剧本对白绑定**取故事角色；provider speaker 不能替代人物身份."""
    try:
        from shared.audio_identity import dialogue_speech_text
        from step03_tts_audio.shot_tts import resolve_shot_speaker
        raw, _text = dialogue_speech_text(shot, plan, characters)
        if raw is None:
            return ""
        return str(resolve_shot_speaker(shot, raw, characters, plan).get("role") or "")
    except Exception:  # noqa: BLE001
        return ""


def _actual_units(script: dict, units, voice_unit_records):
    error = ""
    if units is None:
        try:
            from step03_tts_audio.shot_tts import build_speech_units
            units = build_speech_units(script)
        except Exception as exc:  # noqa: BLE001
            return [], "audible_speech_readback_failed: %s" % exc
    if voice_unit_records is None:
        try:
            from step01_script_gen.plan_feasibility import voice_units
            voice_unit_records = voice_units(
                script.get("chapter_outline"), script.get("shots") or [])
        except Exception as exc:  # noqa: BLE001
            return [], "audible_speech_readback_failed: %s" % exc
    by_narr = {(r.get("kind"), r.get("index")): r for r in voice_unit_records or []}
    plan = script.get("chapter_outline") or {}
    characters = script.get("characters") or []
    shot_by_idx = {int(s.get("index") or 0): s
                   for s in (script.get("shots") or []) if isinstance(s, dict)}
    actual = []
    for u in units or []:
        kind = str(getattr(u, "kind", "") or "")
        sidx = getattr(u, "source_index", None)
        role = str(getattr(u, "voice_role", "") or "")
        text = str(getattr(u, "text", "") or "")
        if kind == "dialogue":
            try:
                idx = int(sidx)
            except (TypeError, ValueError):
                continue
            # 故事角色只从剧本绑定取；provider 的 speaker 是音色标识，不参与人物核对。
            actual.append({"kind": "dialogue", "shot": idx,
                           "role": _story_role(shot_by_idx.get(idx) or {}, plan, characters),
                           "text": text})
        elif kind == "narration":
            rec = by_narr.get(("narration", sidx)) or {}
            for sid in rec.get("shot_indexes") or []:
                try:
                    actual.append({"kind": "narration", "shot": int(sid),
                                   "role": role, "text": text})
                except (TypeError, ValueError):
                    continue
    return actual, error


def missing_audible_speech_units(script: dict, units=None, *,
                                 voice_unit_records=None,
                                 audio_mode: str | None = None) -> list[dict]:
    """返回 ``missing_audible_speech_unit`` 告警列表（只读、零调用）."""
    mode = (audio_mode or os.getenv("MERGE_AUDIO_MODE", "native")).strip().lower()
    if mode != "tts_mix":
        return []
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    actual, readback_error = _actual_units(script, units, voice_unit_records)
    if readback_error:
        return [{
            "code": "audible_speech_readback_failed",
            "severity": "hard",
            "shot": "",
            "field": "",
            "speaker_hint": "",
            "declaration": "",
            "detail": readback_error,
            "actual_units_on_shot": 0,
        }]
    warnings = []
    for shot in shots:
        idx = int(shot.get("index") or 0)
        lines = (shot.get("plan_refs") or {}).get("lines") or []
        has_dialogue = any(a["shot"] == idx and a["kind"] == "dialogue" for a in actual)
        # 结构化对白引用：不依赖 audio_cue/motion 的自由文本关键词。
        if lines and not has_dialogue:
            warnings.append({
                "code": "missing_audible_speech_unit",
                "severity": "hard",
                "shot": idx,
                "field": "plan_refs.lines",
                "speaker_hint": "",
                "declaration": "plan_refs.lines=%s" % list(lines),
                "detail": "剧本对白绑定要求该镜有对白，但实际 SpeechUnit 缺失",
                "actual_units_on_shot": len([a for a in actual if a["shot"] == idx]),
            })
        decls = _speech_declarations(shot)
        if not decls:
            continue
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
