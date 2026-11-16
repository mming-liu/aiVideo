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


from shared.send_cap import (  # noqa: E402
    CallCounter, SendBudgetExceeded, is_hook_request as _is_hook_request,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


_STAGE_BY_SCHEMA = {
    "storyboard_script": "body_generation",
    "hook_shot": "hook",
    "script_audit": "audit",
    "repair_shot_replacements": "repair",
}


def _stage_of(schema: str) -> str:
    return _STAGE_BY_SCHEMA.get(str(schema or ""), "other")


def _summarize_ledger(path: Path) -> list:
    """把台账行归并为“逻辑调用 + 阶段”；台账缺失/坏行不抛错."""
    rows = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        rows.append({"schema": row.get("schema"),
                     "stage": _stage_of(row.get("schema")),
                     "ok": row.get("ok"),
                     "prompt_chars": row.get("prompt_chars"),
                     "response_chars": row.get("response_chars"),
                     "call_id": row.get("call_id")})
    return rows


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
        # 钩子分类预算（P05 实测 3 次钩子会挤占审计/修复；固定计划运行默认 1）
        "HOOK_MAX_SENDS": str(manifest.get("hook_max_sends", 1)),
        "HOOK_MAX_ATTEMPTS": str(manifest.get("hook_max_attempts", 4)),
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

    # **计划缓存**（C03）：隔离章节目录写入既有格式的计划缓存，提交点的
    # 恢复清单（snapshots/step01_resume_*.json）才能冻结计划身份。
    _chapter_dir = Path(m.CHAPTER_DIR)
    _chapter_dir.mkdir(parents=True, exist_ok=True)
    (_chapter_dir / "chapter_outline.cache.json").write_text(
        json.dumps({"outline": plan}, ensure_ascii=False), encoding="utf-8")

    guard = CallCounter(real_post, cap=args.max_sends, log_dir=out / "calls",
                        hook_cap=int(manifest.get("hook_max_attempts",
                                                 os.environ.get("HOOK_MAX_ATTEMPTS", 4) or 4)))
    requests.post = guard
    # M2.1：装进进程内安装点，`_text_gen` 的**逻辑调用层**才会登记（未安装 = no-op）
    from shared.outbound_guard import install as _install_guard
    _prev_guard = _install_guard(guard.guard)
    started = time.monotonic()
    error = ""
    try:
        m.main()
    except SendBudgetExceeded as exc:
        # 预算耗尽：**不重抛**——保存检查点、写报告（候选/产物按现状保留）
        error = "send_budget_exhausted: %s" % exc
    except BaseException as exc:  # noqa: BLE001
        error = "%s: %s" % (type(exc).__name__, exc)
        raise
    finally:
        _install_guard(_prev_guard)
        requests.post = real_post
        elapsed = round(time.monotonic() - started, 1)

    chapter = Path(m.CHAPTER_DIR)

    def _read(name):
        p = chapter / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    script = _read("script_result.json")
    manifest_out = _read("step01_commit.json")
    quality = _read("step01_quality.json")
    audit = _read("step01_audit.json") or _read("step01_audit.blocked.json")
    # BLOCK 时 `script_result.json` 不存在；候选在 `.dsh/step01_budget_frozen/<run_id>/`
    _frozen = None
    # ---- 恢复清单（先算，供冻结候选回退使用）----
    _recovery = {"path": "", "loadable": False, "error": ""}
    _snap_dir = chapter / "snapshots"
    _resume_snaps = sorted(_snap_dir.glob("step01_resume_*.json")) if _snap_dir.is_dir() else []
    if _resume_snaps:
        _recovery["path"] = str(_resume_snaps[-1])
        try:
            from step01_script_gen import resume as _resume
            _man = json.loads(_resume_snaps[-1].read_text(encoding="utf-8"))
            _resume.validate_static(_man)
            _recovery["loadable"] = True
        except Exception as _exc:  # noqa: BLE001
            _recovery["error"] = "%s: %s" % (type(_exc).__name__, _exc)
    # ---- BLOCK 候选回退：budget_frozen → resume manifest snapshot → snapshots ----
    _frozen = None
    _run_id = str((manifest_out or {}).get("run_id") or "")
    if script is None:
        _cands = []
        if _run_id:
            _cands.append((ROOT / ".dsh" / "step01_budget_frozen" / _run_id
                           / "frozen_script.json", "budget_frozen"))
        if _recovery.get("path"):
            try:
                _man = json.loads(Path(_recovery["path"]).read_text(encoding="utf-8"))
                if _man.get("snapshot_path"):
                    _cands.append((Path(_man["snapshot_path"]), "resume_manifest"))
            except Exception:  # noqa: BLE001
                pass
        if _snap_dir.is_dir():
            _snaps = sorted(_snap_dir.glob("script_result_*.json"))
            if _snaps:
                _cands.append((_snaps[-1], "snapshot"))
        for _fp, _src in _cands:
            if _fp and Path(_fp).exists():
                script = json.loads(Path(_fp).read_text(encoding="utf-8"))
                _frozen = {"run_id": _run_id, "path": str(_fp), "source": _src}
                break
    # ---- 台账（真实落点是章节目录；不是 out/calls）----
    ledger = chapter / "step01_calls.jsonl"
    _logical = _summarize_ledger(ledger)
    ledger_rows = len(_logical)
    shots = (script or {}).get("shots") or []
    _commit_detail = str((manifest_out or {}).get("detail")
                         or (manifest_out or {}).get("status_detail") or "")
    # ---- 审计：正式文件 → blocked → commit manifest ----
    _audit = audit
    _audit_source = ""
    if _audit is not None:
        _audit_source = ("step01_audit.json" if (chapter / "step01_audit.json").exists()
                         else "step01_audit.blocked.json")
    elif manifest_out:
        _audit_source = "commit_manifest"
    _audit_status = ((_audit or {}).get("status")
                     or (manifest_out or {}).get("audit_status"))
    _blocking = [{"code": x.get("code"), "shot_id": x.get("shot_id"),
                  "severity": x.get("severity"),
                  "message": str(x.get("message") or "")[:160]}
                 for x in ((_audit or {}).get("issues")
                           or (manifest_out or {}).get("blocking") or [])]
    _audit_issues = len(_blocking)
    _snap = guard.snapshot()
    report.update({
        "elapsed_sec": elapsed, "error": error, "calls": guard.calls,
        # M2.1 三指标：逻辑调用 / 网络尝试 / 收到响应（`transmitted` 不再出现）
        "logical_calls": _snap["logical_calls"],
        "logical_total": _snap["logical_total"],
        "http_attempts": _snap["http_attempts"],
        "http_attempts_total": _snap["http_attempts_total"],
        "responses_received": _snap["responses_received"],
        "responses_total": _snap["responses_total"],
        "hook_attempts": guard.hook_attempts, "hook_responses": guard.hook_responses,
        "blocked": guard.blocked, "hook_blocked": guard.hook_blocked,
        "ledger_calls": _logical, "ledger_rows": ledger_rows,
        "chapter_dir": str(chapter),
        "recovery": _recovery,
        "frozen_candidate": _frozen,
        "commit": {"status": (manifest_out or {}).get("status"),
                   "detail": _commit_detail},
        "hook_present": bool((script or {}).get("hook")),
        "script": {"shots": len(shots),
                   "shots_total_sec": round(sum(float(s.get("duration_sec") or 0) for s in shots), 2),
                   "hook_sec": round(float(((script or {}).get("hook") or {}).get("duration_sec") or 0), 2),
                   "revision": ((script or {}).get("script_revision")
                                or (manifest_out or {}).get("script_revision"))},
        "quality": {"status": (quality or {}).get("status"),
                    "hard_count": (quality or {}).get("hard_count"),
                    "visual_risk_count": (quality or {}).get("visual_risk_count"),
                    "blocking_categories": (quality or {}).get("blocking_categories")},
        "audit": {"status": _audit_status, "issues": _audit_issues,
                  "source": _audit_source, "blocking": _blocking},
        "commit_manifest_path": str(chapter / "step01_commit.json"),
        "script_path": str(_frozen["path"] if _frozen else chapter / "script_result.json"),
    })
    (out / "run_log.json").write_text(json.dumps(report, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    print(json.dumps({"commit": report["commit"]["status"], "shots": report["script"]["shots"],
                      "sec": report["script"]["shots_total_sec"],
                      "http_attempts": guard.attempts,
                      "responses_received": guard.responses_received,
                      "hook_attempts": guard.hook_attempts,
                      "hook_responses": guard.hook_responses,
                      "blocked": guard.blocked, "hook_blocked": guard.hook_blocked,
                      "ledger": ledger_rows}, ensure_ascii=False))
    return 0 if report["commit"]["status"] == "COMMITTED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
