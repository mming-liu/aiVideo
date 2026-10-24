# -*- coding: utf-8 -*-
"""P1a 受控运行器 v2：显式隔离根 + 发送前预检 + 完整证据落盘.

本轮只运行 `--mode dry` / `--mode replay`（零模型调用）；`--mode real` 留给下一次
获得发送预算时使用。所有证据写入 `$DSH_EXP_ROOT/run_*`，不再使用 `.tmp`。
"""
import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared import content_verification as cv
from shared import presentation_contract as pc
from shared.fact_metadata import LEGACY_ACCEPTANCE_FIELD, LEGACY_ACCEPTANCE_VERSION
from step01_script_gen import outline as o
from step01_script_gen import plan_content_repair as pcr

NOVEL = "甲留在门外。乙走进屋。甲继续等待。"
#: 两条对白：迁移 d01 后仍保留 d02，避免 `plan_no_dialogue_lines` 这种**样本缺陷**。
SAMPLE = {
    "main_events": ["甲留在门外", "乙走进屋", "甲继续等待"],
    "climax": "甲继续等待",
    "required_facts": ["甲留在门外。｜原文证据：“甲留在门外。”｜承载：B1/台词1"],
    "causal_chain": ["甲留在门外等待乙"],
    "shot_beats": ["节拍：甲留在门外｜载体：台词1",
                   "节拍：乙走进屋｜载体：台词2",
                   "节拍：甲继续等待｜载体：画面"],
    "dialogue_lines": ["甲留在门外。", "乙走进屋。"],
    "omittable": [], "hook_spoken": [], "time_budget": "动作约15秒",
    "characters": ["甲", "乙"], "emotional_arc": "平静",
    "key_scenes": ["门外", "屋内"], "character_motives": ["甲：等待", "乙：进屋"],
    "end_hook": "", "hook_carrier": "", "narration_lines": [],
    LEGACY_ACCEPTANCE_FIELD: LEGACY_ACCEPTANCE_VERSION,
}
SENDS = {"n": 0, "cap": 3, "abort": False, "calls": []}
#: 实跑用过的**原始样本**（只有一条对白）：迁移后 dialogue_lines 为空，用于复现 2 条残余。
SAMPLE_ORIGINAL = dict(
    SAMPLE,
    shot_beats=["节拍：甲留在门外｜载体：台词1",
                "节拍：乙走进屋｜载体：画面",
                "节拍：甲继续等待｜载体：画面"],
    dialogue_lines=["甲留在门外。"])


def _exp_root() -> Path:
    raw = os.environ.get("DSH_EXP_ROOT", "").strip()
    if not raw:
        raise SystemExit("DSH_EXP_ROOT 未设置：0 次发送，中止（不写 .tmp）")
    p = Path(raw)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _preflight(run_dir: Path) -> dict:
    probe = run_dir / "_preflight.json"
    body = {"schema": "p1a.preflight.v1", "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        probe.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        back = json.loads(probe.read_text(encoding="utf-8"))
        if back != body:
            raise RuntimeError("读回内容不一致")
    except Exception as exc:                                  # noqa: BLE001
        raise SystemExit("预检失败：0 次发送；%s: %s" % (type(exc).__name__, exc))
    return body


def _write(path: Path, value) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, default=str),
                    encoding="utf-8")
    return str(path)


def _cap_service(run_dir: Path):
    """所有 generate（含核验纠正）统一计数、落盘；写盘失败**不抛出**，只记 capture_error。"""
    _orig = o.create_service

    def _wrap(*a, **k):
        _svc = _orig(*a, **k)

        class _S:
            def generate(self, req):
                if SENDS["abort"] or SENDS["n"] >= SENDS["cap"]:
                    raise RuntimeError("P1a 发送上限/证据失败：停止本轮，不再发送")
                SENDS["n"] += 1
                _t0 = time.time()
                _r = _svc.generate(req)
                _row = {"n": SENDS["n"], "elapsed_sec": round(time.time() - _t0, 2),
                        "success": bool(getattr(_r, "success", False)),
                        "raw_response_text": str(getattr(_r, "text", "") or ""),
                        "error": str(getattr(_r, "error", "") or ""),
                        "call_id": str(getattr(_r, "call_id", "") or ""),
                        "request": {"system_prompt": req.system_prompt,
                                    "user_prompt": req.user_prompt,
                                    "model": req.model, "max_tokens": req.max_tokens,
                                    "temperature": req.temperature}}
                SENDS["calls"].append(_row)
                try:
                    _write(run_dir / "calls" / ("call_%02d.json" % SENDS["n"]), _row)
                except Exception as exc:                      # noqa: BLE001
                    SENDS["abort"] = True
                    try:
                        (run_dir / "capture_errors.log").open("a", encoding="utf-8").write(
                            "call %d: %s: %s\n" % (SENDS["n"], type(exc).__name__, exc))
                    except Exception:                         # noqa: BLE001
                        pass
                return _r

        return _S()

    o.create_service = _wrap


def _candidate_plan(sample=None) -> dict:
    sample = sample or SAMPLE
    view, err = pc.view_from_plan(sample)
    if not view or err:
        raise SystemExit("样本不可读：%r" % (err,))
    rec = {
        "schema": cv.SCHEMA_VERIFICATION, "status": "ok",
        "plan_digest": cv.plan_digest_of(sample),
        "novel_digest": hashlib.sha256(NOVEL.encode()).hexdigest(),
        "rule_version": cv.DEFAULT_RULE_VERSION,
        "facts": [{"fact_uid": "f01", "verdict": "partial", "support_spans": [],
                   "missing": "", "suggested_carriers": [], "obligations": []}],
        "source_support": [{"fact_uid": "f01", "verdict": "supported",
                            "evidence_spans": ["甲留在门外。"], "reason": ""}],
        "voice_units": [{"unit_uid": "d01", "verdict": "supported",
                         "source_kind": "novel_narration", "speaker": "",
                         "echo_text": "甲留在门外。", "reason": ""}],
    }
    slot = pcr.content_slots(
        view, [{"code": "verify_voice_not_speech", "uid": "d01"}],
        verification_record=rec)["slots"][0]
    scope = pcr.build_editable_scope(view, [slot])
    ans = {"schema": pcr.SLOT_ANSWER_SCHEMA,
           "answers": [{"slot_id": slot["slot_id"], "action": pcr.ACT_RECLASSIFY}]}
    delta, dp = pcr.compile_delta(view, [slot], ans, novel_text=NOVEL)
    if delta is None:
        raise SystemExit("离线编译失败：%r" % (dp,))
    cand_view, cp, rk = pc.apply_delta_view(view, delta, editable_scope=scope,
                                            accept=lambda _v: [])
    if not cand_view:
        raise SystemExit("离线应用失败：%r %r" % (rk, cp))
    return pc.plan_from_view(cand_view)


def mode_dry(run_dir: Path, pre: dict):
    _write(run_dir / "manifest.json", {
        "schema": "p1a.evidence.v1", "mode": "dry", "preflight": pre,
        "exp_root": str(run_dir.parent), "sends": 0,
        "note": "预检读写成功；未创建 service、未发送。",
    })
    print("dry ok:", run_dir)


def mode_replay(run_dir: Path, pre: dict):
    orig = _candidate_plan(SAMPLE_ORIGINAL)
    fixed = _candidate_plan(SAMPLE)
    orig_probs = o.plan_acceptance_problems(orig, NOVEL)
    fixed_probs = o.plan_acceptance_problems(fixed, NOVEL)
    plan_codes = [p for p in orig_probs
                  if p.get("code") in ("acceptance_incomplete",
                                       "plan_no_dialogue_lines")]
    _write(run_dir / "candidate_plan_original.json", orig)
    _write(run_dir / "candidate_plan_fixed.json", fixed)
    for _name, _cand in (("original", orig), ("fixed", fixed)):
        _write(run_dir / ("candidate_%s_sha256.txt" % _name),
               hashlib.sha256(json.dumps(_cand, ensure_ascii=False,
                                         sort_keys=True).encode("utf-8")).hexdigest())
    _write(run_dir / "residuals_original.json", orig_probs)
    _write(run_dir / "residuals_fixed.json", fixed_probs)
    _write(run_dir / "analysis.json", {
        "schema": "p1a.residual-replay.v1", "mode": "replay",
        "preflight": pre, "sends": 0,
        "original_candidate_dialogue_lines": orig.get("dialogue_lines"),
        "original_candidate_narration_lines": orig.get("narration_lines"),
        "original_all_codes": [p.get("code") for p in orig_probs],
        "reconstructed_plan_level_codes": [p.get("code") for p in plan_codes],
        "fixed_sample_codes": [p.get("code") for p in fixed_probs],
        "note": ("发送 #3 的 530 字节原始响应未落盘；这里用原始候选计划做确定性验收。"
                 "原始样本只剩一条对白，迁移后 dialogue_lines 为空，命中 2 条计划级 code；"
                 "实跑记录存在时 verify_record_missing 被记录替代，故残余数为 2。"
                 "两条 code 未与丢失的原始响应逐字对账，归因保留此边界。"),
    })
    print("replay ok:", run_dir)
    print("reconstructed plan-level codes:",
          [p.get("code") for p in plan_codes])
    print("fixed sample codes:", [p.get("code") for p in fixed_probs])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("dry", "replay", "real"), default="dry")
    args = ap.parse_args()
    root = _exp_root()
    run_dir = root / ("run_" + time.strftime("%Y%m%dT%H%M%S")
                      + "_%d" % (time.time_ns() % 1000000))
    run_dir.mkdir(parents=True, exist_ok=False)
    pre = _preflight(run_dir)
    if args.mode == "dry":
        mode_dry(run_dir, pre)
        return
    if args.mode == "replay":
        mode_replay(run_dir, pre)
        return
    raise SystemExit("real 模式需下一次发送预算；本脚本本轮不执行")


if __name__ == "__main__":
    main()
