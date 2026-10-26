# -*- coding: utf-8 -*-
"""Thin Step01 runner: load an already-verified plan, run the production chain.

It does not re-implement generation/repair/commit: it only wires isolation,
explicit inputs, call counting and output collection around main.main().

Usage:
    py -3.14 tools/run_step01_from_verified_plan.py --input-manifest M
        --output-base DIR [--max-sends 12] [--audit-rounds 2] [--dry-run]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class CallCounter:
    """Count every provider POST; block after cap. No response rewriting."""

    def __init__(self, transport, *, cap, log_dir):
        self.transport = transport
        self.cap = int(cap)
        self.log_dir = Path(log_dir)
        self.attempts = 0
        self.transmitted = 0
        self.blocked = 0
        self.calls = []

    def __call__(self, url, **kw):
        if self.attempts >= self.cap:
            self.blocked += 1
            (self.log_dir / ("blocked_%02d.json" % self.blocked)).write_text(
                json.dumps({"reason": "cap %d" % self.cap}, ensure_ascii=False, indent=1),
                encoding="utf-8")
            raise RuntimeError("调用上限阻断：cap %d" % self.cap)
        self.attempts += 1
        t0 = time.monotonic()
        try:
            resp = self.transport(url, **kw)
        except BaseException as exc:
            self.calls.append({"n": self.attempts, "transmitted": False,
                               "error": "%s: %s" % (type(exc).__name__, exc),
                               "elapsed_sec": round(time.monotonic() - t0, 1)})
            raise
        self.transmitted += 1
        payload = kw.get("json") if isinstance(kw.get("json"), dict) else {}
        self.calls.append({"n": self.attempts, "transmitted": True,
                           "model": payload.get("model"),
                           "max": payload.get("max_completion_tokens"),
                           "status": getattr(resp, "status_code", None),
                           "elapsed_sec": round(time.monotonic() - t0, 1)})
        return resp


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input-manifest", required=True)
    ap.add_argument("--output-base", required=True)
    ap.add_argument("--max-sends", type=int, default=12)
    ap.add_argument("--audit-rounds", type=int, default=2)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    manifest = json.loads(Path(args.input_manifest).read_text(encoding="utf-8-sig"))
    out = Path(args.output_base).resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit("output-base 必须是全新空目录：%s" % out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "calls").mkdir(exist_ok=True)
    (out / "logs").mkdir(exist_ok=True)

    cand_path = Path(manifest["candidate"]["path"])
    novel_path = Path(manifest["novel"]["path"])
    if _sha(cand_path) != manifest["candidate"]["sha256"]:
        raise SystemExit("候选摘要不符")
    if _sha(novel_path) != manifest["novel"]["sha256"]:
        raise SystemExit("原文原始摘要不符")
    plan = json.loads(cand_path.read_text(encoding="utf-8"))
    novel = novel_path.read_bytes().decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    ndig = hashlib.sha256(novel.encode("utf-8")).hexdigest()
    if ndig != manifest["novel"]["digest"]:
        raise SystemExit("原文归一摘要不符")

    scope = manifest["scope"]
    budget = manifest["budget"]
    os.environ.update({
        "OUTPUT_BASE": str(out),
        "STEP01_LOG_PATH": str(out / "logs" / "step01.log"),
        "TEXT_CALL_LEDGER": str(out / "calls" / "step01_calls.jsonl"),
        "NOVEL_NAME": str(scope["novel_name"]),
        "BOOK_INDEX": str(scope["book_index"]),
        "BOOK_NAME": str(scope["book_name"]),
        "NOVEL_CHAPTER": str(scope["chapter"]),
        "DURATION_MIN_SEC": str(budget["min"]),
        "DURATION_MAX_SEC": str(budget["max"]),
        "DURATION_TARGET_MIN_SEC": str(budget["target_min"]),
        "DURATION_TARGET_MAX_SEC": str(budget["target_max"]),
        "VOICE_ADAPTATION_ENABLED": "1" if manifest.get("voice_adaptation_enabled") else "0",
        "SCRIPT_CANDIDATE_TOPK": "1",
        "SCRIPT_PARSE_RETRIES": "1",
        "GAP_ANALYSIS_ENABLED": "0",
        "CHARACTER_EXTRACTION_ENABLED": "0",
        "SCENE_OBJECT_EXTRACTION_ENABLED": "0",
        "SCENE_ASSET_EXTRACTION_ENABLED": "0",
        "PROP_ASSET_EXTRACTION_ENABLED": "0",
        "ASSET_GEN_ENABLED": "0",
        "HOOK_ENABLED": "1",
        "OUTLINE_ENABLED": "1",
        "CONTENT_VERIFY_MAX_TOKENS": "16000",
        "SCRIPT_CANDIDATE_MODELS": str((manifest.get("channel") or {}).get("model") or ""),
        "TEXT_PROVIDER": str((manifest.get("channel") or {}).get("provider") or "agent_plan"),
        "AGENT_PLAN_TEXT_MODEL": str((manifest.get("channel") or {}).get("model") or ""),
        "AGENT_PLAN_TEXT_MAX_TOKENS": str(manifest.get("max_tokens") or 32768),
        "DEEPSEEK_MODEL": str((manifest.get("channel") or {}).get("model") or ""),
        "DEEPSEEK_MAX_TOKENS": str(manifest.get("max_tokens") or 32768),
        "DEEPSEEK_REASONING_EFFORT": "none",
    })

    import requests
    real_post = requests.post
    from shared.content_verification import plan_digest_of
    from step01_script_gen import outline as o, plan_feasibility as pf
    digest = plan_digest_of(plan)
    if digest != manifest["plan_digest"]:
        raise SystemExit("plan_digest 不符：%s" % digest)
    probs = o.plan_acceptance_problems(dict(plan), novel) or []
    codes = [p.get("code") for p in probs]
    report = {"stage": "run", "inputs": {
                  "candidate": {"path": str(cand_path), "sha256": _sha(cand_path)},
                  "novel": {"path": str(novel_path), "sha256": _sha(novel_path), "digest": ndig}},
              "plan_digest": digest, "acceptance_codes": codes,
              "output_base": str(out), "max_sends": args.max_sends,
              "scope": scope, "budget": budget,
              "voice_adaptation_enabled": bool(manifest.get("voice_adaptation_enabled")),
              "dry_run": args.dry_run}
    if codes:
        (out / "run_log.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
        raise SystemExit("计划验收未通过：%s" % codes)
    pf.check_plan_before_generation(dict(plan), chapter_dir=out, novel_text=novel)
    if args.dry_run:
        report["dry_run_ok"] = True
        (out / "run_log.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                          encoding="utf-8")
        print(json.dumps({"dry_run": True, "plan_digest": digest, "codes": codes}))
        return 0

    import step01_script_gen.main as m
    import step01_script_gen.concurrent_extract as ce
    from shared import call_ledger

    m.read_chapter = lambda *a, **k: novel
    ce.extract_all_concurrent = lambda *a, **k: {
        "characters": None, "scene_objects": None,
        "outline": {"executable": True, "plan": plan}}
    m._extract_assets_and_update_script = lambda script: None
    m.STEP01_AUDIT_REPAIR_MAX_ROUNDS = int(args.audit_rounds)
    m.SCRIPT_PARSE_RETRIES = int(manifest.get("parse_retries") or 1)
    captured_hook = {}
    if manifest.get("hook_in_generation"):
        _orig_append = m.append_execution_contract
        _hook_note = ("\n\n同时按 schema 的 hook 字段输出钩子分镜（3–5 秒，不得复用正片台词；"
                      "没有合适钩子时 hook 为 null）。")
        m.append_execution_contract = lambda prompt, contract: _orig_append(prompt, contract) + _hook_note
        _orig_cands = m._generate_script_candidates

        def _cands_with_hook(*a, **k):
            out = _orig_cands(*a, **k)
            if out:
                captured_hook["hook"] = out[0][1].get("hook")
            return out

        m._generate_script_candidates = _cands_with_hook
        m.gen_hook_script = lambda *a, **k: captured_hook.get("hook")
    call_ledger.set_ledger_path(out / "calls" / "step01_calls.jsonl")

    guard = CallCounter(real_post, cap=args.max_sends, log_dir=out / "calls")
    requests.post = guard
    started = time.monotonic()
    error = ""
    try:
        m.main()
    except BaseException as exc:  # noqa: BLE001
        error = "%s: %s" % (type(exc).__name__, exc)
        raise
    finally:
        requests.post = real_post
        elapsed = round(time.monotonic() - started, 1)

    chapter = Path(m.CHAPTER_DIR)

    def _read(name):
        p = chapter / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    script = _read("script_result.json")
    manifest_out = _read("step01_commit.json")
    quality = _read("step01_quality.json")
    audit = _read("step01_audit.json")
    ledger = out / "calls" / "step01_calls.jsonl"
    ledger_rows = len(ledger.read_text(encoding="utf-8").splitlines()) if ledger.exists() else 0
    shots = (script or {}).get("shots") or []
    report.update({
        "elapsed_sec": elapsed, "error": error, "calls": guard.calls,
        "attempts": guard.attempts, "transmitted": guard.transmitted,
        "blocked": guard.blocked, "ledger_rows": ledger_rows,
        "chapter_dir": str(chapter),
        "commit": {"status": (manifest_out or {}).get("status"),
                   "detail": (manifest_out or {}).get("status_detail") or ""},
        "hook_present": bool((script or {}).get("hook")),
        "script": {"shots": len(shots),
                   "shots_total_sec": round(sum(float(s.get("duration_sec") or 0) for s in shots), 2),
                   "hook_sec": round(float(((script or {}).get("hook") or {}).get("duration_sec") or 0), 2),
                   "revision": (script or {}).get("script_revision")},
        "quality": {"status": (quality or {}).get("status"),
                    "hard_count": (quality or {}).get("hard_count"),
                    "visual_risk_count": (quality or {}).get("visual_risk_count"),
                    "blocking_categories": (quality or {}).get("blocking_categories")},
        "audit": {"status": (audit or {}).get("status"),
                  "issues": len((audit or {}).get("issues") or [])},
        "commit_manifest_path": str(chapter / "step01_commit.json"),
        "script_path": str(chapter / "script_result.json"),
    })
    (out / "run_log.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    print(json.dumps({"commit": report["commit"]["status"], "shots": report["script"]["shots"],
                      "sec": report["script"]["shots_total_sec"],
                      "sends": guard.transmitted, "attempts": guard.attempts,
                      "blocked": guard.blocked, "ledger": ledger_rows}, ensure_ascii=False))
    return 0 if report["commit"]["status"] == "COMMITTED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
