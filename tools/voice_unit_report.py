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


def _tier_policy() -> str:
    """生效的声线合同（`strict` / `tiered`）；非法配置直接抛，不静默当 strict."""
    from tts.voice_registry import tier_policy

    return tier_policy()


def load_report_plan(ch: Path, script: dict, plan_dir=None) -> dict:
    """声音报告与 Step03 共用 v2 缓存校验；无缓存时只接受稿内计划."""
    from shared.plan_cache import FILENAME, PlanCacheError, load_plan_cache
    revision = str(script.get("script_revision") or "") or compute_script_revision(script)
    scope = {"book_name": script.get("book_name"), "chapter": script.get("chapter_index")}
    base = Path(plan_dir) if plan_dir else Path(ch)
    path = base if base.is_file() else (base / FILENAME)
    if path.is_file():
        try:
            return load_plan_cache(path, scope=scope, script_revision=revision)
        except PlanCacheError as exc:
            legacy = (json.loads(path.read_text(encoding="utf-8")) or {}).get("outline")
            if isinstance(legacy, dict) and legacy:
                print("[WARN] 计划缓存未带身份绑定（按旧格式读取，身份未核）: %s (%s)"
                      % (path, exc), flush=True)
                return legacy
            raise
    plan = script.get("chapter_outline")
    if not isinstance(plan, dict) or not plan:
        raise SystemExit("缺少受绑定的 chapter_outline.cache.json，且稿内无 chapter_outline")
    return plan


def build_report(script: dict, *, plan_dir: Path | None = None,
                 audio_mode: str | None = None, plan: dict | None = None) -> dict:
    from shared.audible_speech import missing_audible_speech_units
    from step01_script_gen.plan_feasibility import layout_voice_entries, voice_units
    from step03_tts_audio.shot_tts import (
        _compute_voice_windows, build_speech_units, check_voice_uniqueness,
    )
    from tts.synthesis_job import request_key_for

    # 2026-11-19：候选文件可能残留正式稿 revision；报告以**重算值**为准，
    # 并把文件内旧值单独记账，禁止把候选单元标在正式稿身份下。
    computed_revision = compute_script_revision(script)
    file_revision = str(script.get("script_revision") or "")
    revision = computed_revision
    units = build_speech_units(script, scope=current_scope_identity(),
                               script_revision=revision, plan=plan)
    try:
        from step03_tts_audio.shot_tts import _role_counts_free, derive_voice_tiers
        tiers = derive_voice_tiers(script, plan, roles=_role_counts_free(script, plan))
    except Exception as _tier_exc:  # noqa: BLE001 - 分级失败不掩盖单元报告本身
        tiers = {"error": str(_tier_exc)[:200]}
    vrecs = voice_units(plan if isinstance(plan, dict) and plan
                        else script.get("chapter_outline"), script.get("shots"))
    bindings = {(r.get("kind"), r.get("index")): r for r in vrecs}
    win, _ = _compute_voice_windows(script, script.get("shots") or [])
    tracks, layout_problems = layout_voice_entries(vrecs, win)
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
        if unit.kind == "dialogue":
            binding = next((r for r in bindings.values()
                            if r.get("kind") == "dialogue"
                            and str(unit.source_index) in (r.get("shot_indexes") or [])), {})
            track = next((t for t in tracks if t.get("kind") == "dialogue"
                          and t.get("index") == binding.get("index")), {})
        else:
            binding = bindings.get((unit.kind, unit.source_index)) or {}
            track = next((t for t in tracks if t.get("kind") == unit.kind
                          and t.get("index") == unit.source_index), {})
        rows.append({
            "unit_id": unit.unit_id,
            "kind": unit.kind,
            "text": unit.text,
            "chars": len(unit.text),
            "source_locator": unit.source_locator,
            "source_index": unit.source_index,
            "shot_index": unit.source_index if unit.kind == "dialogue" else None,
            "shot_indexes": list(binding.get("shot_indexes") or []),
            "declared_shot": binding.get("declared_shot"),
            "est_sec": binding.get("est_sec"),
            "voice_role": getattr(unit, "voice_role", "") or "",
            "voice_tier": getattr(unit, "voice_tier", "") or "",
            "voice_profile_id": unit.voice_profile_id,
            "model_id": unit.model_id,
            "model_revision": unit.model_revision,
            "speaker": unit.speaker,
            "provider": unit.provider,
            "script_revision": unit.script_revision,
            "planned_window": list(window) if isinstance(window, (list, tuple)) else None,
            "layout_window": ([track.get("start"), track.get("end")]
                              if track else None),
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
        # 2026-11-28 O6：报告必须带出生效声线合同（strict/tiered），否则
        # 同一份 unit 表在两种合同下语义不同却无法从报告区分。
        "voice_tier_policy": _tier_policy(),
        "shared_tiers_enabled": _tier_policy() == "tiered",
        "script_revision": revision,
        "file_script_revision": file_revision,
        "revision_mismatch": bool(file_revision and file_revision != revision),
        "unit_count": len(rows),
        "total_chars": sum(r["chars"] for r in rows),
        "units": rows,
        "layout_problems": layout_problems,
        "voice_tiers": tiers,
        "voice_uniqueness": check_voice_uniqueness(
            units, None, script.get("characters") or []),
        "audible_warnings": missing_audible_speech_units(
            script, units=units, audio_mode=audio_mode),
        "voice_preparation": prep,
        "provider_calls": 0,
    }


def _md(report: dict) -> str:
    lines = ["# 精确声音清单", "",
             "| unit | 类型 | 全文 | 字符 | 来源 | 窗口 | voice_id | 模型 | speaker | key(前12) | 音轨/缓存 |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in report["units"]:
        win = r.get("layout_window") or r.get("planned_window")
        win_s = "%.2f–%.2fs" % (win[0], win[1]) if win else "-"
        lines.append("| {u} | {k} | {t} | {c} | {s} | {w} | {v} | {m} | {sp} | {key} | {a}/{cs} |".format(
            u=r["unit_id"], k=r["kind"], t=r["text"].replace("|", "\\|"),
            c=r["chars"], s=r["source_locator"], w=win_s,
            v=r["voice_profile_id"], m=r["model_id"], sp=r["speaker"] or "-",
            key=(r["request_key"] or "-")[:12], a="有" if r["audio_exists"] else "无",
            cs=r["cache_state"]))
    uniq = report.get("voice_uniqueness") or {}
    tiers = report.get("voice_tiers") or {}
    tier_counts: dict = {}
    for rec in tiers.values() if isinstance(tiers, dict) else ():
        if isinstance(rec, dict) and rec.get("tier"):
            tier_counts[rec["tier"]] = tier_counts.get(rec["tier"], 0) + 1
    lines += ["", "汇总：%d 个单元 / %d 字符；排版问题 %d；声音唯一性=%s；分级=%s；provider 调用 0。" % (
        report["unit_count"], report["total_chars"],
        len(report.get("layout_problems") or []),
        "ok" if uniq.get("ok") else "conflict",
        "/".join("%s:%d" % (k, v) for k, v in sorted(tier_counts.items())) or "-")]
    for c in uniq.get("conflicts") or []:
        lines.append("- 唯一性冲突：%s" % json.dumps(c, ensure_ascii=False))
    warnings = report.get("audible_warnings") or []
    lines += ["", "## 声音承载告警", ""]
    if not warnings:
        lines.append("无 missing_audible_speech_unit。")
    else:
        for w in warnings:
            lines.append("- shot %s（%s）：%s；声明：%s" % (
                w.get("shot"), w.get("field"), w.get("code"),
                (w.get("declaration") or "").replace("|", "\\|")))
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="精确声音清单（只读）")
    parser.add_argument("--chapter-dir", default=None)
    parser.add_argument("--plan-dir", default=None)
    parser.add_argument("--audio-mode", default=None,
                        help="声音合同（如 tts_mix；默认读 MERGE_AUDIO_MODE）")
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--out-md", default=None)
    args = parser.parse_args()

    ch = Path(args.chapter_dir) if args.chapter_dir else chapter_dir()
    script = json.loads((ch / "script_result.json").read_text(encoding="utf-8"))
    # S41：只读报告不得因“给新角色分配音色”写正式 global/voice_registry.json。
    # 把注册表路径临时指向复制件；没有现成注册表时用空临时表。
    import os
    import shutil
    import tempfile
    from tts.voice_registry import get_registry, reset_registry_cache
    _prev_registry = os.environ.get("TTS_VOICE_REGISTRY_PATH")
    # 无条件是复制件：即使调用方显式把 env 指向正式注册表，报告也不能写它。
    _tmp_dir = Path(tempfile.mkdtemp(prefix="voice_report_registry_"))
    _src = (Path(_prev_registry) if _prev_registry
            else Path(get_registry().registry_path))
    _dst = _tmp_dir / "voice_registry.json"
    if _src.is_file():
        shutil.copyfile(_src, _dst)
    os.environ["TTS_VOICE_REGISTRY_PATH"] = str(_dst)
    reset_registry_cache()
    plan = load_report_plan(ch, script, plan_dir=args.plan_dir)
    try:
        report = build_report(script, plan_dir=Path(args.plan_dir) if args.plan_dir else None,
                              audio_mode=args.audio_mode, plan=plan)
    finally:
        if _prev_registry is None:
            os.environ.pop("TTS_VOICE_REGISTRY_PATH", None)
        else:
            os.environ["TTS_VOICE_REGISTRY_PATH"] = _prev_registry
        reset_registry_cache()
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
