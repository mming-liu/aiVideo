# -*- coding: utf-8 -*-
"""P3-E：受控的有限真实拆分验证（OpenCode Go `agent_plan`）.

预算与边界（用户已裁定，硬上限 **4 次实际发送**）：

- **E1-Pos**：1 次真实 M 调用（合法完整拆分）→ 代码盖章五字段，真实 M 必须
  `verdict == "supported"`；
- **E1-Neg**：1 次真实 M 调用（偷删「在天黑前」与「说」）→ 必须
  `verdict in ("partial", "contradicted")` 且 `missing` 非空；同一份原始响应回放给
  `run_split_transaction`，证明事务在 M 处中止（零 apply、零 R）；
- **E2**：复用 E1-Pos 的原始响应（同输入、无第二次 M 发送），只发 **1 次真实 R**：
  `verify_view_with_service`（生产完整核验契约；视图带 `plan_coherence_version`，
  `record_protocol_problems` 按 `require_coherence=True` 同源校验）→ apply → R →
  `split_state` → 真实 `outline.plan_acceptance_problems`。E1-Pos 失败 ⇒ 直接停，
  **不发 E2 的 R**。

受控措施：

- 必须显式设置 `DSH_EXP_ROOT`（且不得位于 `output/` 内）；所有证据写在该目录；
- 发送前 `_preflight.json` 写读探针；`submit_attempts=1`（无隐藏重试）；
- 在 **实际 HTTP 发送边界** 扣预算（内部重试也逐次计数，超限即停）；
- 发送前持久化请求 payload；写盘失败 ⇒ 后续零发送；
- 生产正式产物（`output/.../chapter_00{1,2}` 的 `step01_commit.json` /
  `chapter_outline.cache.json`）发送前后逐字节摘要比对，任何变化都如实报告；
- `--dry` 只预检；`--selftest` 用本地桩走同一编排（0 网络、0 发送）。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from _text_gen import retry_policy                                            # noqa: E402
from shared import content_verification as cv                                 # noqa: E402
from shared import fact_split as fs                                           # noqa: E402
from shared import presentation_contract as pc                                # noqa: E402
from shared.content_verification import VerificationController                # noqa: E402
from step01_script_gen import content_verify as cverify                       # noqa: E402
from step01_script_gen import outline as o                                    # noqa: E402
from step01_script_gen import split_judge as sj                               # noqa: E402
from step01_script_gen import split_transaction as st                         # noqa: E402
from step01_script_gen.content_verify import verify_view_with_service         # noqa: E402

SAMPLE_PATH = ROOT / "tools" / "samples" / "p3_split_f02.json"
#: 本轮唯一允许的文本计费端点（OpenCode Go）。配置变化后必须显式改这里并重新评审，
#: 不能只凭 `provider == agent_plan` 放行——该 provider 也支持其他端点。
OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
MAX_SENDS = 4
M_MAX_TOKENS = 512
M_SYSTEM_PROMPT = ("你是严格的事实拆分完整性判定器。只判断子主张合起来是否完整保留"
                   "父主张的主体、受事、否定、条件、时间、因果与限定语。"
                   "只输出约定的 JSON，不要解释。")

PRODUCTION_ARTIFACTS = (
    ROOT / "output" / "video" / "锦衣夜行" / "part_01_杀青州" / "chapter_001"
    / "chapter_outline.cache.json",
    ROOT / "output" / "video" / "锦衣夜行" / "part_01_杀青州" / "chapter_001"
    / "step01_commit.json",
    ROOT / "output" / "video" / "锦衣夜行" / "part_01_杀青州" / "chapter_002"
    / "chapter_outline.cache.json",
    ROOT / "output" / "video" / "锦衣夜行" / "part_01_杀青州" / "chapter_002"
    / "step01_commit.json",
)

SENDS = {"logical": 0, "attempts": 0, "cap": MAX_SENDS, "abort": False,
         "role": "", "calls": [], "sends": [], "urls": [],
         "blocked_url": None}


# ---------------------------------------------------------------------------
# 证据落盘 / 预检 / 发送预算
# ---------------------------------------------------------------------------
def _sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(str(text).encode("utf-8"))


def _json_digest(value) -> str:
    """与 `split_judge._digest` **同一口径**（JSON 规范化后取 sha256）."""
    return _sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"), default=str))


def _capture_error(run_dir: Path, message: str) -> None:
    try:
        with (run_dir / "capture_errors.log").open("a", encoding="utf-8") as _f:
            _f.write(message.rstrip() + "\n")
    except Exception:                                                # noqa: BLE001
        pass


def _write(path: Path, value, *, run_dir: Path, critical: bool = False) -> str:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(value, (dict, list)) or value is None:
            body = json.dumps(value, ensure_ascii=False, indent=1, default=str)
        else:
            body = str(value)
        path.write_text(body, encoding="utf-8")
        return str(path)
    except Exception as exc:                                         # noqa: BLE001
        SENDS["abort"] = True
        _capture_error(run_dir, "evidence %s: %s: %s"
                       % (path, type(exc).__name__, exc))
        if critical:
            raise RuntimeError("发送前证据落盘失败：%s" % path) from exc
        return ""


def _load_sample() -> dict:
    _s = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    if not isinstance(_s, dict) or not isinstance(_s.get("plan"), dict) \
            or not str(_s.get("novel") or "").strip():
        raise SystemExit("样本包不合法：%s" % SAMPLE_PATH)
    return _s


def _exp_root() -> Path:
    raw = os.environ.get("DSH_EXP_ROOT", "").strip()
    if not raw:
        raise SystemExit("DSH_EXP_ROOT 未设置：0 次发送，中止")
    p = Path(raw).resolve()
    _prod = (ROOT / "output").resolve()
    if p == _prod or _prod in p.parents:
        raise SystemExit("DSH_EXP_ROOT 不得位于生产 output/ 内：0 次发送，中止")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _preflight(run_dir: Path) -> dict:
    body = {"schema": "p3e.preflight.v1",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "pid": os.getpid(),
            "dsh_exp_root": str(run_dir)}
    try:
        _p = run_dir / "_preflight.json"
        _p.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        if json.loads(_p.read_text(encoding="utf-8")) != body:
            raise RuntimeError("读回不一致")
    except Exception as exc:                                         # noqa: BLE001
        raise SystemExit("预检失败：0 次发送；%s: %s"
                         % (type(exc).__name__, exc))
    return body


def _snapshot_artifacts() -> list:
    _rows = []
    for _p in PRODUCTION_ARTIFACTS:
        try:
            _blob = _p.read_bytes()
            _rows.append({"path": str(_p.relative_to(ROOT)), "exists": True,
                          "bytes": len(_blob), "sha256": _sha256_bytes(_blob)})
        except FileNotFoundError:
            _rows.append({"path": str(_p.relative_to(ROOT)), "exists": False,
                          "bytes": None, "sha256": None})
        except Exception as exc:                                     # noqa: BLE001
            _rows.append({"path": str(_p.relative_to(ROOT)), "exists": None,
                          "error": "%s: %s" % (type(exc).__name__, exc)})
    return _rows


def _artifacts_unchanged(before: list, after: list) -> bool:
    return before == after


def _effective_base_url() -> str:
    """**最终生效**的文本 base URL（provider 的取法：专用值优先，留空回落共享值）."""
    from _text_gen.config import config
    return str(config.AGENT_PLAN_TEXT_BASE_URL
               or config.AGENT_PLAN_BASE_URL or "").rstrip("/")


def _base_allowed(base_url, expected_base_url) -> bool:
    return bool(str(expected_base_url or "").strip()) and (
        str(base_url or "").rstrip("/")
        == str(expected_base_url or "").rstrip("/"))


def _url_allowed(url, expected_base_url) -> bool:
    """实际发送 URL 必须以允许的 base URL + `/` 开头（防止换 host / 换路径）."""
    _base = str(expected_base_url or "").rstrip("/")
    if not _base:
        return False
    return str(url or "").startswith(_base + "/")


def _install_send_budget(run_dir: Path, expected_base_url: str) -> None:
    """在**实际 HTTP 发送**边界扣预算：内部重试也逐次计数；证据写不成即停.

    同时做**端点**守卫：`url` 不在允许的 OpenCode Go base URL 下 ⇒ 不发送并中止。
    """
    from _text_gen.agent_plan_provider import AgentPlanTextService
    _orig = AgentPlanTextService._send_once
    _base = str(expected_base_url or "").rstrip("/")

    def _send(self, url, payload, timeout, trace):
        if not _url_allowed(url, _base):
            SENDS["abort"] = True
            SENDS["blocked_url"] = str(url)
            _write(run_dir / "sends" / "send_blocked_url.json",
                   {"url": str(url), "expected_base_url": _base,
                    "reason": "发送 URL 不在允许的 OpenCode Go 端点"},
                   run_dir=run_dir)
            raise RuntimeError("发送 URL 不在允许的 OpenCode Go 端点：%s" % url)
        if SENDS["abort"] or SENDS["attempts"] >= SENDS["cap"]:
            SENDS["abort"] = True
            raise RuntimeError("实际发送预算/证据失败：停止，不再发送")
        SENDS["attempts"] += 1
        SENDS["urls"].append(str(url))
        _row = {"attempt": SENDS["attempts"], "logical_call": SENDS["logical"],
                "role": SENDS["role"], "url": str(url), "timeout": timeout,
                "payload": payload, "ts": round(time.time(), 3)}
        SENDS["sends"].append(_row)
        try:
            _write(run_dir / "sends" / ("send_%02d_request.json" % SENDS["attempts"]),
                   _row, run_dir=run_dir, critical=True)
        except Exception as exc:                                     # noqa: BLE001
            SENDS["abort"] = True
            _capture_error(run_dir, "send %d request: %s: %s"
                           % (SENDS["attempts"], type(exc).__name__, exc))
            raise RuntimeError("发送前证据落盘失败：不发送") from exc
        _t0 = time.time()
        try:
            _resp = _orig(self, url, payload, timeout, trace)
        except Exception as exc:                                     # noqa: BLE001
            try:
                _write(run_dir / "sends" / ("send_%02d_error.json" % SENDS["attempts"]),
                       {"attempt": SENDS["attempts"], "role": SENDS["role"],
                        "elapsed_sec": round(time.time() - _t0, 3),
                        "error": "%s: %s" % (type(exc).__name__, exc)},
                       run_dir=run_dir)
            except Exception as _ev_exc:                             # noqa: BLE001
                SENDS["abort"] = True
                _capture_error(run_dir, "send %d error evidence: original=%s: %s; "
                               "evidence=%s: %s"
                               % (SENDS["attempts"], type(exc).__name__, exc,
                                  type(_ev_exc).__name__, _ev_exc))
            raise
        _write(run_dir / "sends" / ("send_%02d_response.json" % SENDS["attempts"]),
               {"attempt": SENDS["attempts"], "role": SENDS["role"],
                "status_code": int(getattr(_resp, "status_code", 0) or 0),
                "elapsed_sec": round(time.time() - _t0, 3)},
               run_dir=run_dir)
        return _resp

    AgentPlanTextService._send_once = _send


def _cap_service(run_dir: Path) -> None:
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
                    "logical_call": _lid, "role": SENDS["role"],
                    "attempts_used": len(SENDS["sends"]),
                    "elapsed_sec": round(time.time() - _t0, 2),
                    "success": bool(getattr(_r, "success", False)),
                    "raw_response_text": str(getattr(_r, "text", "") or ""),
                    "error": str(getattr(_r, "error", "") or ""),
                    "call_id": str(getattr(_r, "call_id", "") or ""),
                    "request": {"system_prompt": req.system_prompt,
                                "user_prompt": req.user_prompt,
                                "model": req.model, "max_tokens": req.max_tokens,
                                "temperature": req.temperature}}
                SENDS["calls"].append(_row)
                _write(run_dir / "calls" / ("call_%02d.json" % _lid), _row,
                       run_dir=run_dir)
                return _r

        return _S()

    o.create_service = _wrap


# ---------------------------------------------------------------------------
# 样本 / 夹具记录
# ---------------------------------------------------------------------------
def _parent_claim(view) -> str:
    return str(next(f["claim"] for f in view["frozen_facts"]
                    if f["uid"] == PARENT))


def _carrier_texts(view) -> dict:
    _out = {}
    for _b in view.get("beats") or []:
        _out[str(_b.get("uid"))] = str(_b.get("text") or "")
    for _u in (view.get("dialogue_units") or []) + (view.get("narration_units") or []):
        _out[str(_u.get("uid"))] = str(_u.get("text") or "")
    return _out


def _fixture_record(view, plan_digest, *, partial=(), spans=None) -> dict:
    """夹具记录（只承载本样本已知裁决；P3-E 里明确标注为 fixture，不是模型结论）."""
    _texts = _carrier_texts(view)
    _binds = {str(b.get("fact_uid")): (b.get("carriers") or [])
              for b in (view.get("fact_bindings") or []) if isinstance(b, dict)}
    _facts, _sources, _voices = [], [], []
    for _f in view.get("frozen_facts") or []:
        _uid = str(_f.get("uid"))
        _verdict = "partial" if _uid in partial else "supported"
        _car = (_binds.get(_uid) or [{}])[0]
        _cuid = str(_car.get("uid") or "")
        _ctext = _texts.get(_cuid, "")
        _facts.append({
            "fact_uid": _uid, "verdict": _verdict,
            "support_spans": ([_ctext] if _verdict == "supported" and _ctext else []),
            "missing": ("未展示完整主张" if _verdict != "supported" else ""),
            "suggested_carriers": [],
            "obligations": [{
                "obligation_id": "claim", "text": str(_f.get("claim") or ""),
                "verdict": _verdict,
                "evidence": ([{"carrier_uid": _cuid, "exhibitor_span": _ctext}]
                             if _verdict == "supported" and _cuid and _ctext else []),
                "missing": ("未展示完整主张" if _verdict != "supported" else ""),
                "reason": ""}]})
        _spans = (spans or {}).get(_uid) or [str(_f.get("evidence") or "")]
        _sources.append({"fact_uid": _uid, "verdict": "supported",
                         "evidence_spans": [x for x in _spans if str(x)],
                         "reason": ""})
    for _u in view.get("dialogue_units") or []:
        _so = _u.get("source") if isinstance(_u.get("source"), dict) else {}
        _voices.append({"unit_uid": str(_u.get("uid")), "verdict": "supported",
                        "source_kind": "novel_speech",
                        "speaker": str(_so.get("speaker") or "（夹具：未指定说话人）"),
                        "echo_text": str(_u.get("text") or ""), "reason": ""})
    for _u in view.get("narration_units") or []:
        _voices.append({"unit_uid": str(_u.get("uid")), "verdict": "supported",
                        "source_kind": "novel_narration", "speaker": "",
                        "echo_text": str(_u.get("text") or ""), "reason": ""})
    _beats = [str(b.get("uid")) for b in (view.get("beats") or [])]
    return {
        "schema": cv.SCHEMA_VERIFICATION, "status": "ok",
        "plan_digest": str(plan_digest),
        "novel_digest": cverify.novel_identity(NOVEL)["digest"],
        "rule_version": cv.DEFAULT_RULE_VERSION,
        "facts": _facts, "source_support": _sources, "voice_units": _voices,
        "obligation_basis": cv.obligation_basis_digest(view),
        "coherence": {
            "beat_uids": _beats, "verdict": "supported",
            "conflict_beats": [], "evidence_spans": [],
            "reason": "按计划给定的先后顺序承接",
            "dependencies": [
                {"beat_uid": _uid, "requires_before": _beats[:_i],
                 "reason": "按计划给定的先后顺序承接"}
                for _i, _uid in enumerate(_beats)]}}


def _ledger(base_view, digest, record, children) -> dict:
    _parent = next(f for f in base_view["frozen_facts"] if f["uid"] == PARENT)
    return {
        "version": fs.VERSION, "novel_digest": record["novel_digest"],
        "novel_text": NOVEL, "base_digest": digest,
        "splits": [{
            "parent_uid": PARENT, "parent_claim": str(_parent["claim"]),
            "parent_evidence": str(_parent["evidence"]),
            "parent_evidence_spans": list(PARENT_SPANS),
            "baseline_claim": None,
            "authority": {"rule_version": cv.DEFAULT_RULE_VERSION,
                          "plan_digest": digest, "fact_uid": PARENT,
                          "code": "verify_fact_unsupported", "verdict": "partial"},
            "children": copy.deepcopy(children), "narrowed_voices": [],
            "judgment": {"mode": "model", "verdict": "supported", "missing": "",
                         "reason": "",
                         "inputs_digest": fs.inputs_digest(str(_parent["claim"]),
                                                           children),
                         "call_id": "fixture-m", "prompt_digest": "c" * 64,
                         "response_digest": "d" * 64}}]}


# ---------------------------------------------------------------------------
# 真实 M / R
# ---------------------------------------------------------------------------
def _m_call(run_dir: Path, role: str, deadline: float, captured: dict):
    def _call(prompt: str):
        _req_row = {"role": role, "prompt": prompt, "prompt_sha256": _sha256_text(prompt),
                    "json_schema": sj.M_SCHEMA,
                    "params": {"temperature": 0.0, "max_tokens": M_MAX_TOKENS,
                               "model": None},
                    "ts": round(time.time(), 3)}
        _write(run_dir / role / "request.json", _req_row, run_dir=run_dir,
               critical=True)
        from _text_gen.models import TextGenerationRequest
        SENDS["role"] = role
        _svc = o.create_service()
        _req = TextGenerationRequest(
            system_prompt=M_SYSTEM_PROMPT, user_prompt=prompt,
            max_tokens=M_MAX_TOKENS, temperature=0.0,
            json_schema=sj.M_SCHEMA, model=None,
            deadline_monotonic=deadline)
        _res = _svc.generate(_req)
        captured.update({
            "raw": str(getattr(_res, "text", "") or ""),
            "provider_call_id": str(getattr(_res, "call_id", "") or ""),
            "prompt": prompt,
            "success": bool(getattr(_res, "success", False)),
            "error": str(getattr(_res, "error", "") or "")})
        _write(run_dir / role / "response.json", captured, run_dir=run_dir)
        if not _res.success:
            raise RuntimeError("M 调用失败：%s" % captured["error"][:200])
        return captured["raw"]
    return _call


def _judge(role: str, run_dir: Path, parent_claim: str, children, logical_id: str,
           deadline: float) -> tuple:
    _captured: dict = {}
    _out = sj.judge_split(parent_claim, children,
                          call=_m_call(run_dir, role, deadline, _captured),
                          call_id=logical_id)
    _write(run_dir / role / "judgment.json",
           {"judgment": _out.get("judgment"), "problems": _out.get("problems"),
            "ok": _out.get("ok"), "captured": _captured},
           run_dir=run_dir)
    return _out, _captured


def _stamp_checks(judgment, parent_claim, children, prompt, raw) -> dict:
    _j = judgment or {}
    return {
        "mode_model": str(_j.get("mode") or "") == "model",
        "inputs_digest": (str(_j.get("inputs_digest") or "")
                          == fs.inputs_digest(parent_claim, children)),
        "call_id": bool(str(_j.get("call_id") or "").strip()),
        "prompt_digest": (str(_j.get("prompt_digest") or "")
                          == _json_digest(prompt)),
        "response_digest": (str(_j.get("response_digest") or "")
                            == _json_digest(raw)),
    }


def _real_r2(cand_view, *, run_dir: Path, deadline: float, state: dict):
    """**1 次真实 R**：生产 `VerificationController`（`require_coherence=True`，
    无纠正重发）→ 记录协议准入 → R2."""
    _plan = pc.plan_from_view(cand_view)
    _digest = cv.plan_digest_of(_plan)
    _novel_id = cverify.novel_identity(NOVEL)
    _e2 = run_dir / "e2"
    _write(_e2 / "candidate_plan.json", _plan, run_dir=run_dir)
    _write(_e2 / "candidate_view.json", cand_view, run_dir=run_dir)
    SENDS["role"] = "p3e_r2"
    _write(_e2 / "r2_request_meta.json",
           {"role": "p3e_r2", "plan_digest": _digest,
            "novel_digest": _novel_id["digest"],
            "novel_version": _novel_id["version"],
            "novel_chars": _novel_id["chars"], "ts": round(time.time(), 3)},
           run_dir=run_dir, critical=True)
    _error: dict = {}

    def _verify_once(view, d):
        try:
            _rec = verify_view_with_service(
                view, d, novel_text=NOVEL, novel_digest=_novel_id["digest"],
                novel_version=_novel_id["version"], source_references=True,
                deadline_monotonic=deadline,
                archive_path=str(_e2 / "r2_archive.jsonl"),
                call_context={"role": "p3e_r2", "plan_digest": d})
        except Exception as exc:                                     # noqa: BLE001
            _result = getattr(exc, "result", None)
            _evidence = getattr(exc, "evidence", None)
            _error["error"] = "%s: %s" % (type(exc).__name__, exc)
            _write(_e2 / "r2_call_error.json",
                   {"type": type(exc).__name__, "error": str(exc),
                    "sent": int(getattr(exc, "sent", 0) or 0),
                    "raw_response_text": str(getattr(_result, "text", "") or ""),
                    "evidence": _evidence}, run_dir=run_dir)
            raise
        _write(_e2 / "r2_record.raw.json", _rec, run_dir=run_dir)
        state["r2_raw"] = _rec
        return _rec

    _ctl = VerificationController(
        verify=_verify_once, require_coherence=True, max_verifications=1,
        deadline_at=deadline, novel_digest=_novel_id["digest"],
        validate_record=lambda rec, view, d: cv.record_protocol_problems(
            rec, view, plan_digest=d, novel_text=NOVEL,
            novel_digest=_novel_id["digest"]),
        repair=None)
    _rec = _ctl.record_for(cand_view, _digest)
    state["r2_record"] = _rec
    _write(_e2 / "r2_record.stamped.json", _rec, run_dir=run_dir)
    if _error.get("error"):
        state["r2_error"] = _error["error"]
    if not isinstance(_rec, dict) or str(_rec.get("status") or "") != "ok":
        state["r2_status"] = (_rec or {}).get("status") if isinstance(_rec, dict) else None
        return None
    _shape = cv.record_shape_problems(_rec, where="R2 记录")
    _protocol = cv.record_protocol_problems(
        _rec, cand_view, plan_digest=_digest, novel_text=NOVEL,
        novel_digest=_novel_id["digest"])
    _basis_ok = bool(cv.record_basis_matches(_rec, _plan))
    _write(_e2 / "r2_protocol_problems.json",
           {"shape": _shape, "protocol": _protocol, "basis_matches": _basis_ok},
           run_dir=run_dir)
    state.update({"r2_shape": _shape, "r2_protocol": _protocol,
                  "r2_basis_ok": _basis_ok})
    if _shape or _protocol or not _basis_ok:
        return None
    return _rec


# ---------------------------------------------------------------------------
# E1 / E2
# ---------------------------------------------------------------------------
def _run_e1_pos(run_dir: Path, parent_claim: str, deadline: float) -> dict:
    _out, _cap = _judge("e1_pos", run_dir, parent_claim, CHILDREN,
                        "p3e-e1pos-%d" % (time.time_ns() % 1000000), deadline)
    _j = _out.get("judgment") or {}
    _prompt = str(_cap.get("prompt") or "")
    _raw = str(_cap.get("raw") or "")
    _stamps = _stamp_checks(_j, parent_claim, CHILDREN, _prompt, _raw)
    _ok = bool(_out.get("ok") and _j.get("verdict") == "supported"
               and all(_stamps.values()))
    return {"ok": _ok, "judgment": _j, "problems": _out.get("problems") or [],
            "captured": _cap, "stamps": _stamps,
            "verdict": _j.get("verdict"), "missing": _j.get("missing")}


def _run_e1_neg(run_dir: Path, parent_claim: str, deadline: float,
                base_view, plan, record, digest) -> dict:
    _out, _cap = _judge("e1_neg", run_dir, parent_claim, NEGATIVE_CHILDREN,
                        "p3e-e1neg-%d" % (time.time_ns() % 1000000), deadline)
    _j = _out.get("judgment") or {}
    _prompt = str(_cap.get("prompt") or "")
    _raw = str(_cap.get("raw") or "")
    _stamps = _stamp_checks(_j, parent_claim, NEGATIVE_CHILDREN, _prompt, _raw)
    _discriminated = bool(_out.get("ok")
                          and _j.get("verdict") in ("partial", "contradicted")
                          and str(_j.get("missing") or "").strip())
    _res = {"ok": _discriminated, "judgment": _j,
            "problems": _out.get("problems") or [], "captured": _cap,
            "stamps": _stamps, "verdict": _j.get("verdict"),
            "missing": _j.get("missing"), "transaction": None}
    if not _discriminated:
        _res["transaction_note"] = ("真实 M 未判出负例（verdict=%r，missing=%r）"
                                    "⇒ 不执行回放，不声称事务在 M 处中止"
                                    % (_j.get("verdict"), _j.get("missing")))
        _write(run_dir / "e1_neg" / "transaction.json", _res,
               run_dir=run_dir)
        return _res
    # **同一份原始响应回放**：零新增发送，证明事务在 M 处中止。
    _r_calls: list = []
    _prompt_seen: list = []

    def _replay(prompt):
        _prompt_seen.append(prompt)
        if prompt != _prompt:
            raise RuntimeError("M 回放请求与真实请求不一致")
        return _raw

    def _verify(cand):
        _r_calls.append(cand)
        return _fixture_record(cand, cv.plan_digest_of(pc.plan_from_view(cand)))

    _before = cv.plan_digest_of(pc.plan_from_view(base_view))
    _tx = st.run_split_transaction(
        base_view, plan, record,
        {"parent_uid": PARENT, "children": copy.deepcopy(NEGATIVE_CHILDREN)},
        scope=copy.deepcopy(SCOPE),
        ledger=_ledger(base_view, digest, record, NEGATIVE_CHILDREN),
        novel_text=NOVEL, m_call=_replay, m_call_id=_j.get("call_id") or "",
        verify_candidate=_verify)
    _after = cv.plan_digest_of(pc.plan_from_view(base_view))
    _res["transaction"] = {
        "ok": _tx.get("ok"), "stage": _tx.get("stage"),
        "m_calls": _tx.get("m_calls"), "r_calls": len(_r_calls),
        "candidate_is_none": _tx.get("candidate") is None,
        "problems": _tx.get("problems") or [],
        "prompt_replayed_equal": bool(_prompt_seen and _prompt_seen[0] == _prompt),
        "baseline_digest_before": _before, "baseline_digest_after": _after,
        "baseline_unchanged": _before == _after}
    _ok = bool(_tx.get("ok") is False and _tx.get("stage") == "m"
               and _tx.get("m_calls") == 1 and not _r_calls
               and _tx.get("candidate") is None and _before == _after)
    _res["stopped_at_m"] = _ok
    _write(run_dir / "e1_neg" / "transaction.json", _res, run_dir=run_dir)
    return _res


def _run_e2(run_dir: Path, base_view, plan, record, digest, pos: dict,
            deadline: float) -> dict:
    _state: dict = {"r_calls": 0, "r2_record": None, "r2_error": None}
    _pos_prompt = str((pos.get("captured") or {}).get("prompt") or "")
    _pos_raw = str((pos.get("captured") or {}).get("raw") or "")
    _pos_call_id = str((pos.get("judgment") or {}).get("call_id") or "")

    def _replay(prompt):
        if prompt != _pos_prompt:
            raise RuntimeError("E1-Pos 回放请求与真实请求不一致")
        return _pos_raw

    def _verify(cand):
        _state["r_calls"] += 1
        return _real_r2(cand, run_dir=run_dir, deadline=deadline, state=_state)

    _tx = st.run_split_transaction(
        base_view, plan, record,
        {"parent_uid": PARENT, "children": copy.deepcopy(CHILDREN)},
        scope=copy.deepcopy(SCOPE),
        ledger=_ledger(base_view, digest, record, CHILDREN),
        novel_text=NOVEL, m_call=_replay, m_call_id=_pos_call_id,
        verify_candidate=_verify)
    _write(run_dir / "e2" / "transaction.json", _tx, run_dir=run_dir)
    _candidate_plan = _tx.get("plan")
    _residuals = (o.plan_acceptance_problems(_candidate_plan, NOVEL)
                  if isinstance(_candidate_plan, dict) else [])
    _write(run_dir / "e2" / "residuals.json",
           {"plan_present": isinstance(_candidate_plan, dict),
            "problems": _residuals, "codes": [p.get("code") for p in _residuals]},
           run_dir=run_dir)
    _split_state = None
    if isinstance(_candidate_plan, dict):
        _view, _vp = pc.view_from_plan(_candidate_plan)
        _ctx = fs.context(_view) or {}
        _entries = _ctx.get("splits") or []
        if not _vp and _entries:
            _split_state = fs.split_state(
                _view, _entries[0],
                record=_candidate_plan.get("plan_verification"))
    return {
        "ok": bool(_tx.get("ok")),
        "stage": _tx.get("stage"),
        "committed": bool(_tx.get("ok")),
        "state": _split_state or _tx.get("state"),
        "m_calls": _tx.get("m_calls"),
        "r_calls": _state["r_calls"],
        "r2_error": _state.get("r2_error"),
        "r2_protocol": _state.get("r2_protocol"),
        "r2_basis_ok": _state.get("r2_basis_ok"),
        "candidate_is_none": _tx.get("candidate") is None,
        "problems": _tx.get("problems") or [],
        "residuals": _residuals,
        "residual_codes": [p.get("code") for p in _residuals],
    }


# ---------------------------------------------------------------------------
# 样本加载 / 自测
# ---------------------------------------------------------------------------
def _load_constants(sample: dict):
    global NOVEL, PLAN, SPLIT, PARENT, CHILDREN, NEGATIVE_CHILDREN, SCOPE, \
        PARENT_SPANS
    NOVEL = str(sample["novel"])
    PLAN = sample["plan"]
    SPLIT = sample["split"]
    PARENT = str(SPLIT["parent_uid"])
    CHILDREN = copy.deepcopy(SPLIT["children"])
    NEGATIVE_CHILDREN = copy.deepcopy(SPLIT["negative_children"])
    SCOPE = copy.deepcopy(SPLIT["scope"])
    PARENT_SPANS = list(SPLIT.get("parent_evidence_spans") or [])


class _StubTextService:
    """`--selftest` 的本地桩：按请求 schema 回放合法 M / R 响应（0 网络）."""

    def __init__(self):
        self.calls = 0

    def generate(self, request):
        from _text_gen.models import TextGenerationResult
        self.calls += 1
        _name = str((request.json_schema or {}).get("name") or "")
        if _name == sj.M_SCHEMA.get("name"):
            _prompt = str(request.user_prompt or "")
            if "甲会回来" in _prompt and "甲转身离开" in _prompt:
                _body = {"verdict": "partial",
                         "missing": "漏掉「说」与「在天黑前」的时间限定",
                         "reason": "子主张只保留「会回来」，未保留父主张的言说与时间限定"}
            else:
                _body = {"verdict": "supported", "missing": "", "reason": ""}
            return TextGenerationResult(success=True,
                                        text=json.dumps(_body, ensure_ascii=False),
                                        call_id="stub-m-%d" % self.calls)
        raise AssertionError("selftest 未预期的 schema：%r" % _name)


def _selftest_verify(view, plan_digest, **kwargs):
    return _fixture_record(view, plan_digest)


def _run_selftest(run_dir: Path, sample: dict) -> dict:
    global verify_view_with_service
    verify_view_with_service = _selftest_verify
    _stub = _StubTextService()
    o.create_service = lambda *a, **k: _stub
    _base = _baseline()
    _pos = _run_e1_pos(run_dir, _base["parent_claim"], time.monotonic() + 60)
    _neg = _run_e1_neg(run_dir, _base["parent_claim"], time.monotonic() + 60,
                       _base["view"], PLAN, _base["record"], _base["digest"])
    _e2 = ({}
           if not _pos["ok"] else
           _run_e2(run_dir, _base["view"], PLAN, _base["record"], _base["digest"],
                   _pos, time.monotonic() + 60))
    return {"selftest": True, "e1_pos": _pos, "e1_neg": _neg, "e2": _e2,
            "sends": 0, "stub_calls": _stub.calls}


def _baseline() -> dict:
    _view, _vp = pc.view_from_plan(PLAN)
    if _vp:
        raise SystemExit("样本计划不可读：%r"
                         % [(p.get("code"), p.get("path")) for p in _vp])
    if o.plan_acceptance_problems(PLAN, NOVEL):
        raise SystemExit("样本计划未通过真实验收：%r"
                         % [p.get("code")
                            for p in o.plan_acceptance_problems(PLAN, NOVEL)])
    _digest = cv.plan_digest_of(PLAN)
    _record = _fixture_record(_view, _digest, partial=(PARENT,),
                              spans={PARENT: PARENT_SPANS})
    _protocol = cv.record_protocol_problems(
        _record, _view, plan_digest=_digest, novel_text=NOVEL,
        novel_digest=_record["novel_digest"])
    if _protocol:
        raise SystemExit("R0 夹具未通过协议准入：%r"
                         % [p.get("code") for p in _protocol])
    return {"view": _view, "digest": _digest, "record": _record,
            "parent_claim": _parent_claim(_view)}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    # **无隐藏重试**：初次提交 1 次（连接类也不额外重试）。必须在建 service 前设置。
    os.environ["TEXT_TRANSPORT_RETRIES"] = "0"
    os.environ["TEXT_TRANSPORT_CONNECT_RETRIES"] = "0"
    os.environ.setdefault("TEXT_TRANSPORT_BUDGET_SEC", "180")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="只预检，不创建 service")
    ap.add_argument("--selftest", action="store_true",
                    help="本地桩走同一编排（0 网络、0 发送）")
    ap.add_argument("--sample", default="")
    ap.add_argument("--expect-base-url", default=OPENCODE_GO_BASE_URL,
                    help="本轮唯一允许的文本 base URL（默认 OpenCode Go）")
    args = ap.parse_args()
    global SAMPLE_PATH
    if str(args.sample or "").strip():
        SAMPLE_PATH = Path(args.sample)
    _sample = _load_sample()
    _load_constants(_sample)
    _root = _exp_root()
    _run = _root / (("selftest_" if args.selftest else "run_")
                    + time.strftime("%Y%m%dT%H%M%S")
                    + "_%d" % (time.time_ns() % 1000000))
    _run.mkdir(parents=True, exist_ok=False)
    _pre = _preflight(_run)
    _before = _snapshot_artifacts()
    _write(_run / "production_artifacts.before.json", _before, run_dir=_run,
           critical=True)

    from _text_gen.config import config, TextGenCfg
    _effective_base = _effective_base_url()
    _base_ok = _base_allowed(_effective_base, args.expect_base_url)
    _manifest = {
        "schema": "p3e.evidence.v1", "preflight": _pre,
        "sample": str(SAMPLE_PATH), "novel_chars": len(NOVEL),
        "novel_digest": cverify.novel_identity(NOVEL)["digest"],
        "plan_digest": cv.plan_digest_of(PLAN),
        "parent_uid": PARENT, "children": CHILDREN,
        "negative_children": NEGATIVE_CHILDREN,
        "provider": config.PROVIDER,
        "default_model": TextGenCfg.default_text_model(),
        "effective_base_url": _effective_base,
        "expected_base_url": str(args.expect_base_url).rstrip("/"),
        "base_url_ok": _base_ok,
        "cap": MAX_SENDS,
        "submit_attempts": {str(k): retry_policy.submit_attempts(k)
                            for k in retry_policy.FailureClass},
        "require_coherence": True,
        "r0_source": "fixture（只承载本样本已知裁决；不是模型结论）",
        "sends": 0}
    _write(_run / "manifest.json", _manifest, run_dir=_run, critical=True)

    if args.dry:
        _after = _snapshot_artifacts()
        _write(_run / "production_artifacts.after.json", _after, run_dir=_run)
        _write(_run / "result.json",
               {"dry": True, "sends": 0,
                "provider": config.PROVIDER,
                "effective_base_url": _effective_base,
                "expected_base_url": str(args.expect_base_url).rstrip("/"),
                "base_url_ok": _base_ok,
                "submit_attempts": _manifest["submit_attempts"],
                "production_artifacts_unchanged":
                    _artifacts_unchanged(_before, _after)},
               run_dir=_run)
        print("dry ok:", _run)
        print("provider:", config.PROVIDER,
              "model:", TextGenCfg.default_text_model())
        print("base_url:", _effective_base, "expected:", args.expect_base_url,
              "ok:", _base_ok)
        print("submit_attempts:", _manifest["submit_attempts"])
        if not _base_ok:
            raise SystemExit("最终生效 URL 不是允许的 OpenCode Go 端点：0 次发送，停止")
        return

    if args.selftest:
        try:
            _res = _run_selftest(_run, _sample)
        finally:
            pass
        _after = _snapshot_artifacts()
        _res["production_artifacts_unchanged"] = _artifacts_unchanged(_before, _after)
        _res["sends"] = SENDS["attempts"]
        _res["effective_base_url"] = _effective_base
        _res["expected_base_url"] = str(args.expect_base_url).rstrip("/")
        _res["base_url_ok"] = _base_ok
        _write(_run / "production_artifacts.after.json", _after, run_dir=_run)
        _write(_run / "result.json", _res, run_dir=_run)
        print("selftest:", json.dumps(
            {k: _res[k] for k in ("selftest", "sends", "stub_calls")},
            ensure_ascii=False))
        print("e2:", json.dumps(_res.get("e2") or {}, ensure_ascii=False)[:1200])
        print("run:", _run)
        return

    if config.PROVIDER != "agent_plan":
        raise SystemExit("有效通道不是 OpenCode Go（agent_plan）：0 次发送，停止")
    if any(int(v) != 1 for v in _manifest["submit_attempts"].values()):
        raise SystemExit("submit_attempts 不是 1（存在隐藏重试）：0 次发送，停止")
    if not _base_ok:
        raise SystemExit(
            "最终生效 URL 不是允许的 OpenCode Go 端点（effective=%r, expected=%r）："
            "0 次发送，停止" % (_effective_base, args.expect_base_url))
    os.environ["DSH_CAPTURE_PAYLOAD"] = str(_run / "payloads")
    _install_send_budget(_run, args.expect_base_url)
    _cap_service(_run)
    _base = _baseline()
    _result = {"schema": "p3e.result.v1", "e1_pos": None, "e1_pos_ok": False,
               "e1_neg": None, "e2": None, "sends_planned": 3,
               "effective_base_url": _effective_base,
               "expected_base_url": str(args.expect_base_url).rstrip("/"),
               "base_url_ok": _base_ok}
    try:
        _pos = _run_e1_pos(_run, _base["parent_claim"],
                           time.monotonic() + 240.0)
        _result["e1_pos"] = _pos
        _result["e1_pos_ok"] = bool(_pos["ok"])
        if not _pos["ok"]:
            _result["stopped"] = "e1_pos（真实 M 未返回 supported 或盖章不完整）"
        else:
            _neg = _run_e1_neg(_run, _base["parent_claim"],
                               time.monotonic() + 240.0, _base["view"], PLAN,
                               _base["record"], _base["digest"])
            _result["e1_neg"] = _neg
            _result["e1_neg_ok"] = bool(_neg.get("ok"))
            _result["e2"] = _run_e2(_run, _base["view"], PLAN, _base["record"],
                                    _base["digest"], _pos,
                                    time.monotonic() + 300.0)
    except Exception as exc:                                         # noqa: BLE001
        # 任何未预期异常都要留下已产生的证据与明确归因；不把异常当成"没发生"。
        _result["runner_error"] = "%s: %s" % (type(exc).__name__, exc)
        _result["stopped"] = _result.get("stopped") or "runner_error"
        _capture_error(_run, "runner: %s: %s" % (type(exc).__name__, exc))
    _after = _snapshot_artifacts()
    _result["production_artifacts_unchanged"] = _artifacts_unchanged(_before, _after)
    _result["sends"] = SENDS["attempts"]
    _result["send_cap"] = MAX_SENDS
    _result["sent_urls"] = list(SENDS["urls"])
    _result["blocked_url"] = SENDS["blocked_url"]
    _result["capture_errors"] = (str(_run / "capture_errors.log")
                                 if (_run / "capture_errors.log").exists() else None)
    _write(_run / "production_artifacts.after.json", _after, run_dir=_run)
    _write(_run / "result.json", _result, run_dir=_run)
    print("result:", json.dumps(
        {k: _result.get(k) for k in
         ("e1_pos_ok", "e1_neg_ok", "sends", "production_artifacts_unchanged",
          "runner_error")},
        ensure_ascii=False))
    if _result.get("e2"):
        print("e2:", json.dumps(
            {k: _result["e2"].get(k) for k in
             ("committed", "stage", "state", "r_calls", "residual_codes")},
            ensure_ascii=False))
    print("run:", _run)


if __name__ == "__main__":
    main()
