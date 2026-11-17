# -*- coding: utf-8 -*-
"""S04：精确声音清单（只读，2026-11-17）.

以生产构造器 ``build_speech_units()`` 的实际输出为准（不预设单元数/字数）：
逐单元给全文、来源定位、播出窗口、voice_id、模型、请求 key、现有音轨与身份
侧车、待合成字符数。零 provider 调用。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.audio_identity import read_identity
from shared.paths import audio_file, chapter_dir, narration_audio_file
from shared.quality_gate import compute_script_revision
from shared.run_scope import current_scope_identity


def build_report(script: dict, *, plan_dir: Path | None = None) -> dict:
    from step03_tts_audio.shot_tts import build_speech_units
    from tts.synthesis_job import request_key_for

    revision = str(script.get("script_revision") or compute_script_revision(script))
    units = build_speech_units(script, scope=current_scope_identity(),
                               script_revision=revision)
    rows = []
    for unit in units:
        if unit.kind == "narration":
            out = narration_audio_file(int(unit.source_index or 0))
        else:
            out = audio_file(int(unit.source_index or 0))
        identity = read_identity(out) if out.is_file() else None
        key = request_key_for(unit)
        if not out.is_file():
            cache_state = "missing"
        elif identity and str(identity.get("request_key") or "") == key:
            cache_state = "hit"
        else:
            cache_state = "present_unverified"
        window = getattr(unit, "planned_window", None)
        rows.append({
            "unit_id": unit.unit_id,
            "kind": unit.kind,
            "text": unit.text,
            "chars": len(unit.text),
            "source_locator": unit.source_locator,
            "source_index": unit.source_index,
            "shot_index": unit.source_index if unit.kind == "dialogue" else None,
            "voice_role": getattr(unit, "voice_role", "") or "",
            "voice_profile_id": unit.voice_profile_id,
            "model_id": unit.model_id,
            "model_revision": unit.model_revision,
            "speaker": unit.speaker,
            "provider": unit.provider,
            "script_revision": unit.script_revision,
            "planned_window": list(window) if isinstance(window, (list, tuple)) else None,
            "request_key": key,
            "audio_path": str(out),
            "audio_exists": out.is_file(),
            "cache_state": cache_state,
            "identity_request_key": (identity or {}).get("request_key"),
        })
    prep = None
    if plan_dir is not None:
        p = Path(plan_dir) / "voice_preparation.json"
        if p.is_file():
            try:
                prep = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                prep = None
    return {
        "schema": "voice_unit_report.v1",
        "scope": current_scope_identity(),
        "script_revision": revision,
        "unit_count": len(rows),
        "total_chars": sum(r["chars"] for r in rows),
        "units": rows,
        "voice_preparation": prep,
        "provider_calls": 0,
    }


def _md(report: dict) -> str:
    lines = ["# 精确声音清单", "",
             "| unit | 类型 | 全文 | 字符 | 来源 | 窗口 | voice_id | 模型 | speaker | key(前12) | 音轨/缓存 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in report["units"]:
        win = r.get("planned_window")
        win_s = "%.2f–%.2fs" % (win[0], win[1]) if win else "-"
        lines.append("| {u} | {k} | {t} | {c} | {s} | {w} | {v} | {m} | {sp} | {key} | {a}/{cs} |".format(
            u=r["unit_id"], k=r["kind"], t=r["text"].replace("|", "\\|"),
            c=r["chars"], s=r["source_locator"], w=win_s,
            v=r["voice_profile_id"], m=r["model_id"], sp=r["speaker"] or "-",
            key=(r["request_key"] or "-")[:12], a="有" if r["audio_exists"] else "无",
            cs=r["cache_state"]))
    lines += ["", "汇总：%d 个单元 / %d 字符；provider 调用 0。" % (
        report["unit_count"], report["total_chars"])]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="精确声音清单（只读）")
    parser.add_argument("--chapter-dir", default=None)
    parser.add_argument("--plan-dir", default=None)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", default=None)
    args = parser.parse_args()

    ch = Path(args.chapter_dir) if args.chapter_dir else chapter_dir()
    script = json.loads((ch / "script_result.json").read_text(encoding="utf-8"))
    report = build_report(script, plan_dir=Path(args.plan_dir) if args.plan_dir else None)
    out = Path(args.out_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.out_md:
        Path(args.out_md).write_text(_md(report), encoding="utf-8")
    print(json.dumps({"ok": True, "unit_count": report["unit_count"],
                      "total_chars": report["total_chars"],
                      "json": str(out), "provider_calls": 0}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
