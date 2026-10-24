# -*- coding: utf-8 -*-
"""P1a/P1b 受控最小真实运行（OpenCode Go `agent_plan`）.

- 所有证据写入 `$DSH_EXP_ROOT/run_*`；发送前预检；上限 3 次/场景。
- `--dry` 只预检；`--scenario p1a|p1b` 才发送。
- P1a：初始化核验 + 双对白迁移修订 + 候选重验（3 次）。
- P1b：narrow 修订 + 候选重验（2 次，候选重验仅在语法/保护结构通过时发生）。
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
from shared.content_verification import VerificationController
from shared.fact_metadata import LEGACY_ACCEPTANCE_FIELD, LEGACY_ACCEPTANCE_VERSION
from step01_script_gen import outline as o
from step01_script_gen.content_verify import verify_view_with_service
from step01_script_gen.plan_revision import RevisionBudget

NOVEL_A = "甲留在门外。乙说：“我进屋了。”"
SAMPLE_A = {
    "main_events": ["甲留在门外", "乙走进屋", "甲继续等待"],
    "climax": "甲继续等待",
    "required_facts": ["甲留在门外。｜原文证据：“甲留在门外。”｜承载：B1/台词1"],
    "causal_chain": ["甲留在门外等待乙"],
    "shot_beats": ["节拍：甲留在门外｜载体：台词1",
                   "节拍：乙说：我进屋了｜载体：台词2",
                   "节拍：甲继续等待｜载体：画面"],
    "dialogue_lines": ["甲留在门外。", "我进屋了。"],
    "omittable": [], "hook_spoken": [], "time_budget": "动作约15秒",
    "characters": ["甲", "乙"], "emotional_arc": "平静",
    "key_scenes": ["门外", "屋内"], "character_motives": ["甲：等待", "乙：进屋"],
    "end_hook": "", "hook_carrier": "", "narration_lines": [],
    LEGACY_ACCEPTANCE_FIELD: LEGACY_ACCEPTANCE_VERSION,
}

NOVEL_B = "甲留在门外。直到天黑。乙走进屋。"
SAMPLE_B = {
    "main_events": ["甲留在门外", "直到天黑", "乙走进屋"],
    "climax": "甲留在门外",
    "required_facts": ["甲留在门外直到天黑。｜原文证据：“甲留在门外。直到天黑。”｜承载：B1/旁白1"],
    "causal_chain": ["甲留在门外直到天黑"],
    "shot_beats": ["节拍：甲留在门外｜载体：旁白1",
                   "节拍：直到天黑｜载体：画面",
                   "节拍：乙走进屋｜载体：台词1"],
    "dialogue_lines": ["乙走进屋。"],
    "omittable": [], "hook_spoken": [], "time_budget": "动作约15秒",
    "characters": ["甲", "乙"], "emotional_arc": "平静",
    "key_scenes": ["门外", "屋内"], "character_motives": ["甲：等待", "乙：进屋"],
    "end_hook": "", "hook_carrier": "",
    "narration_lines": [{"text": "甲留在门外。直到天黑。乙走进屋。",
                        "source": "甲留在门外。直到天黑。乙走进屋。"}],
    LEGACY_ACCEPTANCE_FIELD: LEGACY_ACCEPTANCE_VERSION,
}

SENDS = {"n": 0, "cap": 3, "abort": False, "calls": []}


def _exp_root() -> Path:
    raw = os.environ.get("DSH_EXP_ROOT", "").strip()
    if not raw:
        raise SystemExit("DSH_EXP_ROOT 未设置：0 次发送，中止")
    p = Path(raw)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _preflight(run_dir: Path) -> dict:
    body = {"schema": "p1a_p1b.preflight.v1",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    try:
        _p = run_dir / "_preflight.json"
        _p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        if json.loads(_p.read_text(encoding="utf-8")) != body:
            raise RuntimeError("读回不一致")
    except Exception as exc:                                      # noqa: BLE001
        raise SystemExit("预检失败：0 次发送；%s: %s" % (type(exc).__name__, exc))
    return body


def _write(path: Path, value) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=1, default=str),
                    encoding="utf-8")
    return str(path)


def _cap_service(run_dir: Path):
    _orig = o.create_service

    def _wrap(*a, **k):
        _svc = _orig(*a, **k)

        class _S:
            def generate(self, req):
                if SENDS["abort"] or SENDS["n"] >= SENDS["cap"]:
                    raise RuntimeError("发送上限/证据失败：停止，不再发送")
                SENDS["n"] += 1
                _t0 = time.time()
                _r = _svc.generate(req)
                _row = {
                    "n": SENDS["n"], "elapsed_sec": round(time.time() - _t0, 2),
                    "success": bool(getattr(_r, "success", False)),
                    "raw_response_text": str(getattr(_r, "text", "") or ""),
                    "error": str(getattr(_r, "error", "") or ""),
                    "call_id": str(getattr(_r, "call_id", "") or ""),
                    "request": {"system_prompt": req.system_prompt,
                                "user_prompt": req.user_prompt,
                                "model": req.model, "max_tokens": req.max_tokens,
                                "temperature": req.temperature},
                }
                SENDS["calls"].append(_row)
                try:
                    _write(run_dir / "calls" / ("call_%02d.json" % SENDS["n"]), _row)
                except Exception as exc:                          # noqa: BLE001
                    SENDS["abort"] = True
                    try:
                        (run_dir / "capture_errors.log").open("a", encoding="utf-8").write(
                            "call %d: %s: %s\n" % (SENDS["n"], type(exc).__name__, exc))
                    except Exception:                             # noqa: BLE001
                        pass
                return _r

        return _S()

    o.create_service = _wrap


def _controller(novel_digest: str) -> VerificationController:
    return VerificationController(
        verify=lambda _view, _digest: verify_view_with_service(
            _view, _digest, novel_text=(
                NOVEL_A if novel_digest == hashlib.sha256(NOVEL_A.encode()).hexdigest()
                else NOVEL_B),
            novel_digest=novel_digest),
        novel_digest=novel_digest, max_verifications=3,
        deadline_at=time.monotonic() + 300.0)


def _run_p1a(run_dir: Path):
    _view, _err = pc.view_from_plan(SAMPLE_A)
    if not _view or _err:
        raise SystemExit("P1a 样本不可读：%r" % (_err,))
    _digest = cv.plan_digest_of(SAMPLE_A)
    _nd = hashlib.sha256(NOVEL_A.encode()).hexdigest()
    # 发送 #1：真实初始化核验
    _base = verify_view_with_service(_view, _digest, novel_text=NOVEL_A,
                                     novel_digest=_nd)
    _write(run_dir / "base_record.json", _base)
    if not isinstance(_base, dict) or _base.get("status") != "ok":
        raise SystemExit("初始化核验未成功：%r" % (_base,))
    _voice = {str(x.get("unit_uid")): x for x in (_base.get("voice_units") or [])
              if isinstance(x, dict)}
    _problems = [{"code": "verify_voice_not_speech", "kind": "verification",
                  "uid": "d01", "path": "voice_units",
                  "reason": "冻结样本：叙述句被放进对白槽"}]
    _ctl = _controller(_nd)
    _ctl.records[_digest] = _base
    _out = o.revise_plan_once(SAMPLE_A, _problems, RevisionBudget(
        max_calls=3, deadline_sec=300.0), None, novel_text=NOVEL_A,
        verification=_ctl)
    _write(run_dir / "outcome.json", _out)
    _plan = _out.get("plan") if isinstance(_out.get("plan"), dict) else {}
    _write(run_dir / "candidate_plan.json", _plan)
    _write(run_dir / "residuals.json", _out.get("evidence", {}).get("residuals", []))
    return {"scenario": "p1a", "kind": _out.get("kind"),
            "detail": _out.get("detail"), "sends": SENDS["n"],
            "dialogue_lines": _plan.get("dialogue_lines"),
            "narration_lines": _plan.get("narration_lines"),
            "base_voice_units": _voice}


def _run_p1b(run_dir: Path):
    _view, _err = pc.view_from_plan(SAMPLE_B)
    if not _view or _err:
        raise SystemExit("P1b 样本不可读：%r" % (_err,))
    _nd = hashlib.sha256(NOVEL_B.encode()).hexdigest()
    _problems = [{"code": "presentation_voice_not_in_novel", "kind": "verification",
                  "uid": "n01", "path": "presentation.voice_units",
                  "reason": "冻结样本：旁白正文需要收窄到逐字原文"}]
    _ctl = _controller(_nd)
    _out = o.revise_plan_once(SAMPLE_B, _problems, RevisionBudget(
        max_calls=3, deadline_sec=300.0), None, novel_text=NOVEL_B,
        verification=_ctl)
    _write(run_dir / "outcome.json", _out)
    _plan = _out.get("plan") if isinstance(_out.get("plan"), dict) else {}
    _write(run_dir / "candidate_plan.json", _plan)
    return {"scenario": "p1b", "kind": _out.get("kind"),
            "detail": _out.get("detail"), "sends": SENDS["n"],
            "dialogue_lines": _plan.get("dialogue_lines"),
            "narration_lines": _plan.get("narration_lines")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=("p1a", "p1b"), default="p1b")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    _root = _exp_root()
    _run = _root / ("run_" + time.strftime("%Y%m%dT%H%M%S")
                    + "_%d" % (time.time_ns() % 1000000))
    _run.mkdir(parents=True, exist_ok=False)
    _pre = _preflight(_run)
    if args.dry:
        _write(_run / "manifest.json", {"schema": "p1a_p1b.evidence.v1",
                                        "preflight": _pre, "sends": 0,
                                        "note": "预检通过；未创建 service"})
        print("dry ok:", _run)
        return
    from _text_gen.config import config
    if config.PROVIDER != "agent_plan":
        raise SystemExit("有效通道不是 OpenCode Go（agent_plan）：0 次发送，停止")
    os.environ["DSH_CAPTURE_PAYLOAD"] = str(_run / "payloads")
    _cap_service(_run)
    if args.scenario == "p1a":
        _res = _run_p1a(_run)
    else:
        _res = _run_p1b(_run)
    _write(_run / "result.json", _res)
    print("result:", json.dumps(_res, ensure_ascii=False)[:1500])
    print("sends:", SENDS["n"], "run:", _run)


if __name__ == "__main__":
    main()
