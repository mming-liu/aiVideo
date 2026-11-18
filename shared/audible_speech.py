# -*- coding: utf-8 -*-
"""声音承载回读：声明了人物开口，却没有可听 SpeechUnit（2026-11-18 R7）.

判据只回答“这一镜有没有可听的人声承载”，不判断台词是否完整表达语义。
输入是当前声音合同、镜头发声声明，以及从生产入口回读的逐镜单元。
"""
from __future__ import annotations

import os
import re

_SPEECH_VERBS = ("开口", "说道", "道：", "喊道", "喝道", "骂道", "吩咐",
                 "威胁", "喊话", "呼喊", "厉声", "低声说", "出声")
_NON_SPEECH = ("惊呼", "抽泣", "呼吸", "箫声", "蝉鸣", "风声", "马蹄", "车轮",
               "鞭响", "衣料", "酒液", "瓷盏", "炭炉")


def _speech_declarations(shot: dict) -> list[dict]:
    out = []
    audio = str(shot.get("audio_cue") or "")
    m = re.search(r"人声\s*=\s*([^；;]+)", audio)
    if m:
        value = m.group(1).strip()
        if value and value not in ("无", "无。", "-"):
            if any(v in value for v in _SPEECH_VERBS):
                out.append({"field": "audio_cue", "text": value,
                            "speaker": value.split("压低")[0].split("内心")[0].strip("：:，, ")})
    motion = str(shot.get("motion_desc") or "")
    if any(v in motion for v in ("开口", "说道", "道：", "喊道", "喝道",
                                 "骂道", "吩咐", "威胁", "喊话", "呼喊")):
        out.append({"field": "motion_desc", "text": motion, "speaker": ""})
    return out


def missing_audible_speech_units(script: dict, units=None, *,
                                 voice_unit_records=None,
                                 audio_mode: str | None = None) -> list[dict]:
    """返回 ``missing_audible_speech_unit`` 告警列表（只读、零调用）.

    ``tts_mix`` 才检查；原生声音按各自合同，不在这里替它判对白。
    """
    mode = (audio_mode or os.getenv("MERGE_AUDIO_MODE", "native")).strip().lower()
    if mode != "tts_mix":
        return []
    plan = script.get("chapter_outline") if isinstance(script, dict) else {}
    shots = [s for s in (script.get("shots") or []) if isinstance(s, dict)]
    if voice_unit_records is None:
        try:
            from step01_script_gen.plan_feasibility import voice_units
            voice_unit_records = voice_units(plan, shots)
        except Exception:  # noqa: BLE001
            voice_unit_records = []
    covered: set[int] = set()
    for rec in voice_unit_records or []:
        for sid in rec.get("shot_indexes") or []:
            try:
                covered.add(int(sid))
            except (TypeError, ValueError):
                continue
    warnings = []
    for shot in shots:
        idx = int(shot.get("index") or 0)
        decls = _speech_declarations(shot)
        if not decls or idx in covered:
            continue
        for d in decls:
            warnings.append({
                "code": "missing_audible_speech_unit",
                "severity": "advisory",
                "shot": idx,
                "field": d["field"],
                "speaker_hint": d["speaker"],
                "declaration": d["text"][:160],
                "detail": "镜头声明人物开口/威胁，但回读没有任何 SpeechUnit 落在该镜",
            })
    return warnings


def check_missing_audible_speech(script: dict, units=None, **kwargs) -> list[dict]:
    return missing_audible_speech_units(script, units, **kwargs)
