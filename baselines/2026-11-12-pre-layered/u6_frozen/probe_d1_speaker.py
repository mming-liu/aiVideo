# -*- coding: utf-8 -*-
"""零调用取证：从实际声音消费入口解析 D1 说话人（源稿 vs 候选）.

不调用任何模型，只读两份冻结稿，调用生产同源函数：
  plan_voice_entries / shot_voice_entries / shot_voice_parts /
  dialogue_speech_text / resolve_shot_speaker
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from step01_script_gen.plan_feasibility import (  # noqa: E402
    plan_voice_entries, shot_dialogue_entries, shot_voice_parts)
from shared.audio_identity import dialogue_speech_text  # noqa: E402
from step03_tts_audio.shot_tts import resolve_shot_speaker  # noqa: E402


def probe(script: dict) -> dict:
    plan = script.get("chapter_outline") or {}
    chars = script.get("characters") or []
    entries = plan_voice_entries(plan)
    d1 = [e for e in entries if e.get("id") == "D1"]
    carriers = []
    for shot in script.get("shots") or []:
        de = shot_dialogue_entries(shot, plan)
        if any(str(e.get("id")) == "D1" for e in de):
            carriers.append(shot)
    out = {
        "plan_dialogue_lines": plan.get("dialogue_lines"),
        "plan_voice_entry_D1": d1,
        "voice_adaptations_present": bool(plan.get("voice_adaptations")),
        "carrier_shots": [s.get("index") for s in carriers],
        "characters": [c.get("identifier") for c in chars],
        "shots": [],
    }
    for shot in carriers:
        idx = shot.get("index")
        parts = shot_voice_parts(shot, plan)
        raw_for_speaker, text = dialogue_speech_text(shot, plan, chars)
        row = {
            "index": idx,
            "subtitle": shot.get("subtitle"),
            "narration_segment": shot.get("narration_segment"),
            "plan_refs": shot.get("plan_refs"),
            "voice_parts": {k: parts.get(k) for k in
                            ("dialogue", "narration", "prefix", "split_ok")},
            "voice_parts_entries": [
                {k: e.get(k) for k in ("kind", "id", "text", "speaker")}
                for e in parts.get("entries") or []],
            "raw_for_speaker": raw_for_speaker,
            "speech_text": text,
            "resolved": None,
            "resolved_error": "",
        }
        try:
            row["resolved"] = resolve_shot_speaker(shot, raw_for_speaker, chars, plan)
        except Exception as exc:  # noqa: BLE001
            row["resolved_error"] = "%s: %s" % (type(exc).__name__, exc)
        out["shots"].append(row)
    return out


def main() -> int:
    src = ROOT / "output" / "锦衣夜行" / "part_01_杀青州" / "chapter_001" / "script_result.json"
    cand = ROOT / ".pytest_tmp" / "u1_u5_closeout_20261110" / "candidate_d1a" / "candidate.json"
    payload = {
        "source_path": str(src),
        "candidate_path": str(cand),
        "source": probe(json.loads(src.read_text(encoding="utf-8-sig"))),
        "candidate": probe(json.loads(cand.read_text(encoding="utf-8-sig"))),
    }
    out = Path(__file__).with_name("l2_d1_speaker_probe.json")
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
