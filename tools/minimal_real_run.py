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
from step01_script_gen import outline as o
from step01_script_gen.content_verify import verify_view_with_service
from step01_script_gen.plan_revision import RevisionBudget

DEFAULT_SAMPLES = {
    "p1a": ROOT / "tools" / "samples" / "p1a_double_dialogue.json",
    "p1b": ROOT / "tools" / "samples" / "p1b_source_defect.json",
}


def _load_sample(path: Path) -> dict:
    _s = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(_s, dict) or not isinstance(_s.get("plan"), dict) \
            or not str(_s.get("novel") or "").strip():
        raise SystemExit("样本包不合法：%s" % path)
    return {"novel": str(_s["novel"]), "plan": _s["plan"],
            "expected": _s.get("expected") or {}, "path": str(path)}

SENDS = {"logical": 0, "attempts": 0, "cap": 3, "abort": False,
         "calls": [], "sends": []}


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


def _install_send_budget(run_dir: Path):
    """在**实际 HTTP 发送**边界扣预算：内部重试也逐次计数，写盘失败即停后续发送."""
    from _text_gen.agent_plan_provider import AgentPlanTextService
    _orig = AgentPlanTextService._send_once

    def _send(self, url, payload, timeout, trace):
        if SENDS["abort"] or SENDS["attempts"] >= SENDS["cap"]:
            SENDS["abort"] = True
            raise RuntimeError("实际发送预算/证据失败：停止，不再发送")
        SENDS["attempts"] += 1
        _row = {"attempt": SENDS["attempts"], "logical_call": SENDS["logical"],
                "url": str(url), "timeout": timeout,
                "payload": payload, "ts": round(time.time(), 3)}
        SENDS["sends"].append(_row)
        try:
            # **发送前**持久化请求；写不成就不发。
            _write(run_dir / "sends" / ("send_%02d_request.json" % SENDS["attempts"]),
                   _row)
        except Exception as exc:                                  # noqa: BLE001
            SENDS["abort"] = True
            try:
                (run_dir / "capture_errors.log").open("a", encoding="utf-8").write(
                    "send %d request: %s: %s\n"
                    % (SENDS["attempts"], type(exc).__name__, exc))
            except Exception:                                     # noqa: BLE001
                pass
            raise RuntimeError("发送前证据落盘失败：不发送") from exc
        _t0 = time.time()
        try:
            _resp = _orig(self, url, payload, timeout, trace)
        except Exception as exc:                                  # noqa: BLE001
            try:
                _write(run_dir / "sends" / ("send_%02d_error.json" % SENDS["attempts"]),
                       {"attempt": SENDS["attempts"], "elapsed_sec":
                        round(time.time() - _t0, 3), "error":
                        "%s: %s" % (type(exc).__name__, exc)})
            except Exception:                                     # noqa: BLE001
                pass
            raise
        try:
            _write(run_dir / "sends" / ("send_%02d_response.json" % SENDS["attempts"]),
                   {"attempt": SENDS["attempts"],
                    "status_code": int(getattr(_resp, "status_code", 0) or 0),
                    "elapsed_sec": round(time.time() - _t0, 3)})
        except Exception as exc:                                  # noqa: BLE001
            SENDS["abort"] = True
            try:
                (run_dir / "capture_errors.log").open("a", encoding="utf-8").write(
                    "send %d response: %s: %s\n"
                    % (SENDS["attempts"], type(exc).__name__, exc))
            except Exception:                                     # noqa: BLE001
                pass
        return _resp

    AgentPlanTextService._send_once = _send


def _cap_service(run_dir: Path):
    _orig = o.create_service

    def _wrap(*a, **k):
        _svc = _orig(*a, **k)

        class _S:
            def generate(self, req):
                if SENDS["abort"] or SENDS["attempts"] >= SENDS["cap"]:
                    raise RuntimeError("实际发送预算/证据失败：停止，不再发送")
                SENDS["logical"] += 1
                _lid = SENDS["logical"]
                _t0 = time.time()
                _r = _svc.generate(req)
                _row = {
                    "logical_call": _lid,
                    "attempts_used": len(SENDS["sends"]),
                    "elapsed_sec": round(time.time() - _t0, 2),
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
                    _write(run_dir / "calls" / ("call_%02d.json" % _lid), _row)
                except Exception as exc:                          # noqa: BLE001
                    SENDS["abort"] = True
                    try:
                        (run_dir / "capture_errors.log").open("a", encoding="utf-8").write(
                            "call %d: %s: %s\n" % (_lid, type(exc).__name__, exc))
                    except Exception:                             # noqa: BLE001
                        pass
                return _r

        return _S()

    o.create_service = _wrap


def _controller(novel: str, novel_digest: str) -> VerificationController:
    return VerificationController(
        verify=lambda _view, _digest: verify_view_with_service(
            _view, _digest, novel_text=novel, novel_digest=novel_digest),
        novel_digest=novel_digest, max_verifications=3,
        deadline_at=time.monotonic() + 300.0)


def _run_p1a(sample: dict, run_dir: Path):
    _plan = sample["plan"]
    _novel = sample["novel"]
    _view, _err = pc.view_from_plan(_plan)
    if not _view or _err:
        raise SystemExit("P1a 样本不可读：%r" % (_err,))
    _digest = cv.plan_digest_of(_plan)
    _nd = hashlib.sha256(_novel.encode()).hexdigest()
    # 发送 #1：真实初始化核验
    _base = verify_view_with_service(_view, _digest, novel_text=_novel,
                                     novel_digest=_nd)
    _write(run_dir / "base_record.json", _base)
    if not isinstance(_base, dict) or _base.get("status") != "ok":
        raise SystemExit("初始化核验未成功：%r" % (_base,))
    _voice = {str(x.get("unit_uid")): x for x in (_base.get("voice_units") or [])
              if isinstance(x, dict)}
    _problems = [{"code": "verify_voice_not_speech", "kind": "verification",
                  "uid": "d01", "path": "voice_units",
                  "reason": "冻结样本：叙述句被放进对白槽"}]
    _ctl = _controller(_novel, _nd)
    _ctl.records[_digest] = _base
    _out = o.revise_plan_once(_plan, _problems, RevisionBudget(
        max_calls=3, deadline_sec=300.0), None, novel_text=_novel,
        verification=_ctl)
    _write(run_dir / "outcome.json", _out)
    _cand = _out.get("plan") if isinstance(_out.get("plan"), dict) else {}
    _write(run_dir / "candidate_plan.json", _cand)
    return {"scenario": "p1a", "kind": _out.get("kind"),
            "detail": _out.get("detail"), "sends": SENDS["attempts"],
            "dialogue_lines": _cand.get("dialogue_lines"),
            "narration_lines": _cand.get("narration_lines"),
            "base_voice_units": _voice}


def _run_p1b(sample: dict, run_dir: Path):
    _plan = sample["plan"]
    _novel = sample["novel"]
    _view, _err = pc.view_from_plan(_plan)
    if not _view or _err:
        raise SystemExit("P1b 样本不可读：%r" % (_err,))
    # **先由实际检查入口产生目标问题**；样本只声明预期 code，不直接注入问题对象。
    _all = o.plan_acceptance_problems(_plan, _novel)
    _want = {str(x) for x in (sample.get("expected") or {}).get("problem_codes")
             or []}
    _actual = [p for p in _all if str(p.get("code") or "") in _want]
    _write(run_dir / "actual_problems.json", _all)
    if not _actual:
        raise SystemExit("目标问题未出现：0 次发送；实际=%r"
                         % [p.get("code") for p in _all])
    _write(run_dir / "selected_problems.json", _actual)
    _nd = hashlib.sha256(_novel.encode()).hexdigest()
    _ctl = _controller(_novel, _nd)
    _out = o.revise_plan_once(_plan, _actual, RevisionBudget(
        max_calls=3, deadline_sec=300.0), None, novel_text=_novel,
        verification=_ctl)
    _write(run_dir / "outcome.json", _out)
    _cand = _out.get("plan") if isinstance(_out.get("plan"), dict) else {}
    _write(run_dir / "candidate_plan.json", _cand)
    return {"scenario": "p1b", "kind": _out.get("kind"),
            "detail": _out.get("detail"), "sends": SENDS["attempts"],
            "dialogue_lines": _cand.get("dialogue_lines"),
            "narration_lines": _cand.get("narration_lines"),
            "problem_codes": [p.get("code") for p in _actual]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", choices=("p1a", "p1b"), default="p1b")
    ap.add_argument("--sample", default="", help="显式样本包 JSON；缺省用 tools/samples/")
    ap.add_argument("--dry", action="store_true")
    args = ap.parse_args()
    _sample_path = Path(args.sample) if str(args.sample or "").strip() \
        else DEFAULT_SAMPLES[args.scenario]
    _sample = _load_sample(_sample_path)
    _root = _exp_root()
    _run = _root / ("run_" + time.strftime("%Y%m%dT%H%M%S")
                    + "_%d" % (time.time_ns() % 1000000))
    _run.mkdir(parents=True, exist_ok=False)
    _pre = _preflight(_run)
    if args.dry:
        _write(_run / "manifest.json", {"schema": "p1a_p1b.evidence.v1",
                                        "preflight": _pre, "sends": 0,
                                        "sample": str(_sample_path),
                                        "note": "预检通过；未创建 service"})
        print("dry ok:", _run)
        return
    from _text_gen.config import config
    if config.PROVIDER != "agent_plan":
        raise SystemExit("有效通道不是 OpenCode Go（agent_plan）：0 次发送，停止")
    os.environ["DSH_CAPTURE_PAYLOAD"] = str(_run / "payloads")
    _install_send_budget(_run)
    _cap_service(_run)
    if args.scenario == "p1a":
        _res = _run_p1a(_sample, _run)
    else:
        _res = _run_p1b(_sample, _run)
    _write(_run / "result.json", _res)
    print("result:", json.dumps(_res, ensure_ascii=False)[:1500])
    print("sends:", SENDS["attempts"], "run:", _run)


if __name__ == "__main__":
    main()
