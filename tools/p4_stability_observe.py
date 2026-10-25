# -*- coding: utf-8 -*-
"""P4：判决稳定性观察（3 次核验，只报告，不改判据）.

治理 §8：冻结 9 事实计划 + 同原文 + 同规则 + 同通道同参数；重复 3 次（cap=3）；
只回答"该样本在同一上下文中是否出现 verdict 差异"。**无翻转不能排除运行方差**，
不作因果归因，不改判据，不写正式产物。

受控措施：
- 必须显式 `DSH_EXP_ROOT`（不得在 `output/` 内）；证据全部写在该目录；
- 每次发送前持久化 payload（写不成即停）；**首份 payload 冻结**，后续请求必须
  逐字一致（比 `payload` 的 canonical JSON，不比 `attempt/role/ts` 这些逐次不同的
  证据字段）；实际送出的提示摘要必须等于冻结摘要，否则**在 HTTP 之前**拒绝；
- 在**实际 HTTP 发送边界**扣预算（全局 cap=3，内部重试也逐次计数）；
- 端点守卫：只允许 OpenCode Go base URL（`provider == agent_plan` 不够）；
- 每次一个**独立** `VerificationController`（`max_verifications=1`、
  `allow_correction=False`）⇒ 一次物理发送=一次观察；
- **首次协议失败或调用异常即停**：每次观察保存记录后立即读 `status`，`ok` 才继续；
  证据落盘失败、端点/预算守卫触发同样停止；停止原因落 `stop_reason`，不补抽、
  不称"三次观察完成"；
- `TEXT_TRANSPORT_RETRIES=0` / `CONNECT=0` ⇒ 逻辑调用与物理发送 1:1；
- 生产四份正式产物发送前后摘要比对，任何变化如实报告；
- 受影响代码、有效配置与样本摘要在发送前落盘（只记录、不比对），用于事后复核。

用法（三个冻结摘要**必须显式提供**，缺失或不符都 0 次发送）：

    py -3.14 tools/p4_stability_observe.py --dry --plan <计划> --novel <原文>
        --expect-plan-digest <64 位> --expect-novel-digest <64 位>
        --expect-prompt-sha256 <64 位>
    py -3.14 tools/p4_stability_observe.py            # 最多 3 次真实发送
        --plan .p1a_exp/p4_stability_<前一组>/candidate_content.json
        --novel output/step01_ab/20261018T135622/d9_feasibility/input/novel.txt
        --expect-plan-digest <64 位> --expect-novel-digest <64 位>
        --expect-prompt-sha256 <64 位>

默认输入（只读外部冻结样本，见治理 §12 第 8 行）：

    output/step01_ab/20261018T135622/d11_run/real_slot_f08/candidate_plan.json
    output/step01_ab/20261018T135622/d9_feasibility/input/novel.txt
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_PLAN = (ROOT / "output" / "step01_ab" / "20261018T135622" / "d11_run"
                / "real_slot_f08" / "candidate_plan.json")
DEFAULT_NOVEL = (ROOT / "output" / "step01_ab" / "20261018T135622"
                 / "d9_feasibility" / "input" / "novel.txt")

TARGET_MODEL = "deepseek-v4.1-flash"
OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
VERIFY_MAX_TOKENS = 16000
MAX_SENDS = 3

#: 停止原因词表（**只由记录状态/守卫事件决定，不从错误文案推断**）。
STOP_COMPLETED = "completed"
STOP_PROTOCOL = "protocol_failure"
STOP_CALL = "call_failure"
STOP_EXCEPTION = "exception"
STOP_EVIDENCE = "evidence_write_failure"
STOP_ENDPOINT = "endpoint_guard"
STOP_BUDGET = "budget_guard"
STOP_PROMPT = "prompt_digest_mismatch"
STOP_PAYLOAD = "payload_mismatch"

#: `status` → 停止原因：协议失败与调用失败分开记账。
_STOP_BY_STATUS = {"call_failed": STOP_CALL, "deadline_exhausted": STOP_CALL}

#: 允许在串行观察前被确定性验收容忍的码（缺记录 = 尚未核验）。
GATE_ALLOWED = {"verify_record_missing"}

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

SENDS = {"cap": MAX_SENDS, "attempts": 0, "logical": 0, "abort": False,
         "blocked_url": None, "urls": [], "sends": [], "role": "",
         "stop": "", "expect_prompt_sha256": ""}

#: 本组**首份实际 payload**；后续请求必须逐字一致（`payload_mismatch` 即停）。
FREEZE = {"payload_sha256": "", "payload": None}

#: 守卫安装痕迹：`run()` 每次重装、`finally` 还原，避免同进程多组串证据目录。
_SEND_GUARD = {"cls": None, "orig": None}

#: 版本冻结清单（只记录摘要，不比对；比对只用于输入/提示/payload）。
FREEZE_FILES = (
    "tools/p4_stability_observe.py",
    "step01_script_gen/content_verify.py",
    "step01_script_gen/source_references.py",
    "shared/content_verification.py",
    "shared/presentation_contract.py",
    "_text_gen/agent_plan_provider.py",
    "_text_gen/retry_policy.py",
)


class EvidenceWriteError(RuntimeError):
    """证据落盘失败：已经写下的记录保留，后续发送一律不做。"""


def _reset_sends() -> None:
    """同一进程可重复 `run()`：预算、守卫痕迹与冻结件都回到初始值。"""
    SENDS.update({"cap": MAX_SENDS, "attempts": 0, "logical": 0, "abort": False,
                  "blocked_url": None, "urls": [], "sends": [], "role": "",
                  "stop": "", "expect_prompt_sha256": ""})
    FREEZE.update(payload_sha256="", payload=None)


# ---------------------------------------------------------------------------
# 证据落盘 / 发送预算 / 端点守卫 / 冻结一致性
# ---------------------------------------------------------------------------
def _sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(str(text).encode("utf-8"))


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, (dict, list)) or value is None:
        body = json.dumps(value, ensure_ascii=False, indent=1, default=str)
    else:
        body = str(value)
    path.write_text(body, encoding="utf-8")


def _effective_base_url() -> str:
    from _text_gen.config import config
    return str(config.AGENT_PLAN_TEXT_BASE_URL
               or config.AGENT_PLAN_BASE_URL or "").rstrip("/")


def _base_allowed(base_url, expected) -> bool:
    return bool(str(expected or "").strip()) and (
        str(base_url or "").rstrip("/") == str(expected or "").rstrip("/"))


def _url_allowed(url, expected) -> bool:
    base = str(expected or "").rstrip("/")
    return bool(base) and str(url or "").startswith(base + "/")


def _payload_sha256(payload) -> str:
    """payload 的 canonical 摘要（`attempt/role/ts` 不属于 payload，逐次不同）。"""
    return _sha256_text(json.dumps(payload, sort_keys=True, ensure_ascii=False))


def _user_prompt_of(payload) -> str:
    for _m in reversed((payload or {}).get("messages") or []):
        if isinstance(_m, dict) and str(_m.get("role") or "") == "user":
            return str(_m.get("content") or "")
    return ""


def _prompt_sha256_of(payload) -> str:
    return _sha256_text(_user_prompt_of(payload))


def _session_headers(service) -> dict:
    """只留网关路由会话头（Authorization 等凭据不进证据）。"""
    try:
        return {str(k): str(v) for k, v in service._headers.items()
                if str(k).lower() == "x-opencode-session"}
    except Exception:                                                # noqa: BLE001
        return {}


def _refuse(run_dir: Path, reason: str, name: str, body: dict) -> None:
    """**HTTP 之前**拒绝：留拒绝证据、记停止原因，然后抛异常断掉本次观察。"""
    SENDS["abort"] = True
    SENDS["stop"] = SENDS["stop"] or reason
    try:
        _write(run_dir / "sends" / name, dict(body, schema="p4.send_blocked.v1",
                                              reason_code=reason))
    except Exception:                                                # noqa: BLE001
        pass
    raise RuntimeError("发送前拒绝（%s）：不再发送" % reason)


def _freeze_payload(run_dir: Path, payload, sha: str) -> None:
    """首份 payload 落盘成功后才允许发送；写不成 ⇒ 本组不发送。"""
    try:
        _write(run_dir / "frozen_payload.json",
               {"schema": "p4.frozen_payload.v1", "sha256": sha,
                "role": SENDS["role"], "payload": payload})
    except Exception as exc:                                         # noqa: BLE001
        SENDS["abort"] = True
        SENDS["stop"] = SENDS["stop"] or STOP_EVIDENCE
        raise EvidenceWriteError("冻结 payload 落盘失败：不发送") from exc
    FREEZE.update(payload_sha256=sha, payload=payload)


def _uninstall_send_budget() -> None:
    """还原 `_send_once`（同一进程跑多组时，不把上一组的包装留在类上）。"""
    if _SEND_GUARD["cls"] is not None and _SEND_GUARD["orig"] is not None:
        _SEND_GUARD["cls"]._send_once = _SEND_GUARD["orig"]
    _SEND_GUARD.update(cls=None, orig=None)


def _install_send_budget(run_dir: Path, expected_base: str) -> None:
    """在**实际 HTTP 发送**边界守端点/提示/冻结 payload，并扣预算。

    顺序即生效位置：端点 → **实际提示摘要** → **冻结 payload 一致** → 预算 →
    首份 payload 落盘 → 才写请求证据、才发 HTTP。任何一项不过都抛异常，观察层
    据此停止，后续不再发送。
    """
    from _text_gen.agent_plan_provider import AgentPlanTextService
    _orig = AgentPlanTextService._send_once

    def _send(self, url, payload, timeout, trace):
        if not _url_allowed(url, expected_base):
            SENDS["blocked_url"] = str(url)
            _refuse(run_dir, STOP_ENDPOINT, "send_blocked_url.json",
                    {"url": str(url), "expected_base_url": expected_base,
                     "why": "发送 URL 不在允许的 OpenCode Go 端点"})
        _prompt_sha = _prompt_sha256_of(payload)
        if _prompt_sha != SENDS["expect_prompt_sha256"]:
            _refuse(run_dir, STOP_PROMPT, "send_blocked_prompt.json",
                    {"got": _prompt_sha, "expected": SENDS["expect_prompt_sha256"],
                     "why": "实际送出的提示与冻结摘要不符"})
        _sha = _payload_sha256(payload)
        if FREEZE["payload_sha256"] and _sha != FREEZE["payload_sha256"]:
            _refuse(run_dir, STOP_PAYLOAD, "send_blocked_payload_mismatch.json",
                    {"got": _sha, "frozen": FREEZE["payload_sha256"],
                     "why": "实际 payload 与首份冻结 payload 不一致"})
        if SENDS["abort"] or SENDS["attempts"] >= SENDS["cap"]:
            _refuse(run_dir, STOP_BUDGET, "send_blocked_budget.json",
                    {"attempts": SENDS["attempts"], "cap": SENDS["cap"],
                     "why": "实际发送预算已用尽"})
        if not FREEZE["payload_sha256"]:
            _freeze_payload(run_dir, payload, _sha)
        _next = SENDS["attempts"] + 1
        _row = {"attempt": _next, "role": SENDS["role"], "url": str(url),
                "timeout": timeout, "headers": _session_headers(self),
                "payload": payload, "ts": round(time.time(), 3)}
        try:
            _write(run_dir / "sends" / ("send_%02d_request.json" % _next), _row)
        except Exception as exc:                                     # noqa: BLE001
            SENDS["abort"] = True
            SENDS["stop"] = SENDS["stop"] or STOP_EVIDENCE
            raise EvidenceWriteError("发送前证据落盘失败：不发送") from exc
        SENDS["attempts"] = _next
        SENDS["sends"].append(_row)
        SENDS["urls"].append(str(url))
        _t0 = time.time()
        try:
            _resp = _orig(self, url, payload, timeout, trace)
        except Exception as exc:                                     # noqa: BLE001
            try:
                _write(run_dir / "sends" / ("send_%02d_error.json" % _next),
                       {"attempt": _next, "role": SENDS["role"],
                        "elapsed_sec": round(time.time() - _t0, 3),
                        "error": "%s: %s" % (type(exc).__name__, exc)})
            except Exception:                                        # noqa: BLE001
                SENDS["abort"] = True
                SENDS["stop"] = SENDS["stop"] or STOP_EVIDENCE
            raise
        try:
            _write(run_dir / "sends" / ("send_%02d_response.json" % _next),
                   {"attempt": _next, "role": SENDS["role"],
                    "status_code": int(getattr(_resp, "status_code", 0) or 0),
                    "elapsed_sec": round(time.time() - _t0, 3)})
        except Exception:                                            # noqa: BLE001
            # 响应证据写不成：本次观察仍要落记录，但本组到此为止。
            SENDS["abort"] = True
            SENDS["stop"] = SENDS["stop"] or STOP_EVIDENCE
        return _resp

    AgentPlanTextService._send_once = _send
    _SEND_GUARD.update(cls=AgentPlanTextService, orig=_orig)


def _preflight(run_dir: Path) -> dict:
    body = {"schema": "p4.preflight.v1",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "pid": os.getpid(), "dsh_exp_root": str(run_dir)}
    _p = run_dir / "_preflight.json"
    _write(_p, body)
    if json.loads(_p.read_text(encoding="utf-8")) != body:
        raise SystemExit("预检失败：0 次发送；读回不一致")
    return body


def _snapshot() -> list:
    _rows = []
    for _p in PRODUCTION_ARTIFACTS:
        try:
            _blob = _p.read_bytes()
            _rows.append({"path": str(_p.relative_to(ROOT)), "exists": True,
                          "bytes": len(_blob), "sha256": _sha256_bytes(_blob)})
        except FileNotFoundError:
            _rows.append({"path": str(_p.relative_to(ROOT)), "exists": False,
                          "bytes": None, "sha256": None})
    return _rows


# ---------------------------------------------------------------------------
# 版本/配置冻结（只记录，不比对；比对只用于输入/提示/payload）
# ---------------------------------------------------------------------------
def _git_facts() -> dict:
    """HEAD 与工作树状态；取不到就如实记错误，不伪造“干净”。"""
    import subprocess
    _out = {"head": None, "status_porcelain_sha256": None, "error": ""}
    try:
        _r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(ROOT),
                            capture_output=True, text=True, timeout=20)
        if _r.returncode == 0:
            _out["head"] = _r.stdout.strip() or None
        _s = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT),
                            capture_output=True, text=True, timeout=20)
        if _s.returncode == 0:
            _out["status_porcelain_sha256"] = _sha256_text(_s.stdout)
    except Exception as exc:                                         # noqa: BLE001
        _out["error"] = "%s: %s" % (type(exc).__name__, exc)
    return _out


def _code_freeze() -> dict:
    _rows = []
    for _rel in FREEZE_FILES:
        _p = ROOT / _rel
        try:
            _rows.append({"path": _rel, "sha256": _sha256_bytes(_p.read_bytes())})
        except OSError as exc:
            _rows.append({"path": _rel, "sha256": None, "error": str(exc)})
    return {"git": _git_facts(), "files": _rows}


def _session_header_value() -> str:
    from _text_gen.config import config
    _explicit = str(getattr(config, "AGENT_PLAN_SESSION_ID", "") or "").strip()
    if _explicit:
        return _explicit
    from _text_gen.agent_plan_provider import _AUTO_SESSION_ID
    return str(_AUTO_SESSION_ID)


def _config_freeze(base_url: str) -> dict:
    from _text_gen.config import config
    from shared import config as _shared_config
    from shared.content_verification import DEFAULT_RULE_VERSION
    _pinned = bool(str(getattr(config, "AGENT_PLAN_SESSION_ID", "") or "").strip())
    return {
        "provider": str(getattr(config, "PROVIDER", "")),
        "base_url": str(base_url),
        "model": TARGET_MODEL,
        "max_tokens": VERIFY_MAX_TOKENS,
        "temperature": 0.0,
        "reasoning_effort": "none",
        "source_references": True,
        "require_coherence": True,
        "rule_version": DEFAULT_RULE_VERSION,
        "agent_plan_text_rpm": int(getattr(config, "AGENT_PLAN_TEXT_RPM", 0) or 0),
        "agent_plan_text_max_tokens": int(
            getattr(config, "AGENT_PLAN_TEXT_MAX_TOKENS", 0) or 0),
        "prompt_max_chars": int(getattr(_shared_config, "PROMPT_MAX_CHARS", 0) or 0),
        "transport_retries": os.environ.get("TEXT_TRANSPORT_RETRIES", ""),
        "connect_retries": os.environ.get("TEXT_TRANSPORT_CONNECT_RETRIES", ""),
        "session_header_mode": "pinned" if _pinned else "auto",
        "session_header_value": _session_header_value(),
    }


def _frozen_identity_problems(info: dict, *, plan_digest: str, novel_digest: str,
                              prompt_sha256: str) -> tuple:
    """返回 `(missing, bad)`：冻结值缺失或与本次输入不符，都必须 **0 次发送**。"""
    _want = {"plan_digest": str(plan_digest or "").strip(),
             "novel_digest": str(novel_digest or "").strip(),
             "prompt_sha256": str(prompt_sha256 or "").strip()}
    _missing = [k for k, v in _want.items() if not v]
    _bad = [k for k, v in _want.items() if v and str(info.get(k) or "") != v]
    return _missing, _bad


# ---------------------------------------------------------------------------
# 输入冻结（零发送）
# ---------------------------------------------------------------------------
def _freeze_inputs(plan_path: Path, novel_path: Path) -> dict:
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    if not isinstance(plan, dict):
        raise SystemExit("计划不是 JSON 对象：0 次发送")
    # 观察对象是**候选内容**；旧核验记录（含被拒标记）不属于内容，冻结前剥离。
    stripped = {k: v for k, v in plan.items()
                if k != "plan_verification" and not str(k).startswith("verification")}
    from shared import presentation_contract as pc
    view, vp = pc.view_from_plan(stripped)
    if view is None or vp:
        raise SystemExit("视图不可读：%s；0 次发送"
                         % [(p.get("code"), p.get("path")) for p in (vp or [])])
    from shared.content_verification import plan_digest_of
    digest = plan_digest_of(stripped)
    novel = Path(novel_path).read_text(encoding="utf-8")
    if not novel.strip():
        raise SystemExit("原文为空：0 次发送")
    from step01_script_gen import outline as o
    codes = [str(p.get("code") or "") for p in
             (o.plan_acceptance_problems(stripped, novel) or [])]
    bad = [c for c in codes if c not in GATE_ALLOWED]
    # 提示摘要：用**生产同一条**构造路径预算（发送边界再核实际送出的那一份）。
    from step01_script_gen import content_verify as _cverify
    from step01_script_gen.source_references import catalog_for as _catalog_for
    _novel_digest = _sha256_text(novel)
    _catalog = _catalog_for(novel)
    _prompt = _cverify.build_verification_prompt(
        view, novel_text=novel, novel_digest=_novel_digest,
        novel_version="", source_catalog=_catalog)
    return {"plan": stripped, "view": view, "plan_digest": digest,
            "novel_text": novel, "novel_digest": _novel_digest,
            "plan_path": str(plan_path), "novel_path": str(novel_path),
            "plan_file_sha256": _sha256_bytes(Path(plan_path).read_bytes()),
            "novel_file_sha256": _sha256_bytes(Path(novel_path).read_bytes()),
            "gate_codes": codes, "gate_bad": bad,
            "prompt_sha256": _sha256_text(_prompt), "prompt_chars": len(_prompt),
            "facts": [str(f.get("uid")) for f in view.get("frozen_facts") or []]}


# ---------------------------------------------------------------------------
# 单次观察
# ---------------------------------------------------------------------------
def _axes_from_record(record) -> dict:
    if not isinstance(record, dict) or str(record.get("status") or "") != "ok":
        return {"status": str((record or {}).get("status") or "missing"),
                "code": str((record or {}).get("code") or "")}
    _facts = {}
    for row in record.get("facts") or []:
        if not isinstance(row, dict):
            continue
        _facts[str(row.get("fact_uid"))] = {
            "verdict": str(row.get("verdict") or ""),
            "missing": str(row.get("missing") or ""),
            "obligations": [[str(o.get("obligation_id") or ""),
                             str(o.get("text") or ""),
                             str(o.get("verdict") or "")]
                            for o in (row.get("obligations") or [])
                            if isinstance(o, dict)]}
    _src = {str(r.get("fact_uid")): str(r.get("verdict") or "")
            for r in record.get("source_support") or [] if isinstance(r, dict)}
    _voice = {str(r.get("unit_uid")): [str(r.get("verdict") or ""),
                                       str(r.get("source_kind") or ""),
                                       str(r.get("speaker") or "")]
              for r in record.get("voice_units") or [] if isinstance(r, dict)}
    _coh = record.get("coherence") if isinstance(record.get("coherence"), dict) else {}
    return {"status": "ok", "facts": _facts, "sources": _src,
            "voices": _voice,
            "coherence": {"verdict": str(_coh.get("verdict") or ""),
                          "conflict_beats": [str(x) for x in
                                             (_coh.get("conflict_beats") or [])]}}


def _axes_from_raw(raw_text: str):
    """**未准入**原始响应的各轴（仅诊断：不盖章、不进验收、不参与采纳）."""
    try:
        data = json.loads(str(raw_text or ""))
    except Exception:                                                # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    _facts = {}
    for row in data.get("facts") or []:
        if not isinstance(row, dict):
            continue
        _facts[str(row.get("fact_uid"))] = {
            "verdict": str(row.get("verdict") or ""),
            "missing": str(row.get("missing") or ""),
            "obligations": [[str(o.get("obligation_id") or ""),
                             str(o.get("text") or ""),
                             str(o.get("verdict") or "")]
                            for o in (row.get("obligations") or [])
                            if isinstance(o, dict)]}
    if not _facts:
        return None
    _src = {str(r.get("fact_uid")): str(r.get("verdict") or "")
            for r in data.get("source_support") or [] if isinstance(r, dict)}
    _voice = {str(r.get("unit_uid")): [str(r.get("verdict") or ""),
                                       str(r.get("source_kind") or ""),
                                       str(r.get("speaker") or "")]
              for r in data.get("voice_units") or [] if isinstance(r, dict)}
    _coh = data.get("coherence") if isinstance(data.get("coherence"), dict) else {}
    return {"status": "ok", "admitted": False, "facts": _facts, "sources": _src,
            "voices": _voice,
            "coherence": {"verdict": str(_coh.get("verdict") or ""),
                          "conflict_beats": [str(x) for x in
                                             (_coh.get("conflict_beats") or [])]}}


def _diff_axes(rows: list) -> dict:
    """比较各 run 的 axes：拆成 **verdict / content / shape** 三类差异.

    - verdict：事实/来源/声音的 verdict、coherence verdict（P4 直接回答的对象）；
    - content：`missing` 文字、义务 **verdict** 向量、声音 source_kind/speaker、
      coherence `conflict_beats`（语义/文字，不是 verdict 本身）；
    - shape：义务 **id/text**（协议形状；未准入记录里常与 verdict 无关）。
    """
    out = {"verdict": [], "content": [], "shape": []}

    def _add(kind, axis, key, vals, note=""):
        _seen = {json.dumps(v, ensure_ascii=False, sort_keys=True) for v in vals}
        if len(_seen) > 1:
            _row = {"axis": axis, "key": key, "values": vals}
            if note:
                _row["note"] = note
            out[kind].append(_row)

    if len(rows) < 2:
        return out
    _uids = set()
    for r in rows:
        _uids |= set((r.get("facts") or {}))
    for uid in sorted(_uids):
        _f = [(r.get("facts") or {}).get(uid) or {} for r in rows]
        _add("verdict", "facts", uid, [x.get("verdict") for x in _f])
        _add("content", "facts", uid, [x.get("missing") for x in _f], "missing")
        _add("content", "facts", uid,
             [[[o[2] for o in (x.get("obligations") or [])] for x in _f]][0],
             "obligation_verdicts")
        _add("shape", "facts", uid,
             [[[o[0] for o in (x.get("obligations") or [])] for x in _f]][0],
             "obligation_ids")
        _add("shape", "facts", uid,
             [[[o[1] for o in (x.get("obligations") or [])] for x in _f]][0],
             "obligation_texts")
    _keys = set()
    for r in rows:
        _keys |= set(r.get("sources") or {})
    for k in sorted(_keys):
        _add("verdict", "sources", k, [(r.get("sources") or {}).get(k) for r in rows])
    _keys = set()
    for r in rows:
        _keys |= set(r.get("voices") or {})
    for k in sorted(_keys):
        _v = [(r.get("voices") or {}).get(k) or [] for r in rows]
        _add("verdict", "voices", k, [(x[0] if x else None) for x in _v])
        _add("content", "voices", k, [(x[1:] if x else None) for x in _v],
             "source_kind/speaker")
    _add("verdict", "coherence", "verdict",
         [((r.get("coherence") or {}).get("verdict")) for r in rows])
    _add("content", "coherence", "conflict_beats",
         [((r.get("coherence") or {}).get("conflict_beats")) for r in rows])
    return out


def _choose_basis(runs: list) -> tuple:
    """优先用**已准入**记录；不足 2 份时退到**未准入**原始响应（仅诊断）."""
    admitted = [r for r in runs if r.get("axes", {}).get("status") == "ok"]
    if len(admitted) >= 2:
        return "admitted", admitted
    raw = [r for r in runs if (r.get("raw_axes") or {}).get("status") == "ok"]
    if len(raw) >= 2:
        return "raw_unadmitted", raw
    return "none", []


def _observe(info: dict, index: int, run_dir: Path) -> dict:
    from shared import content_verification as cv
    from step01_script_gen.content_verify import verify_view_with_service

    _archive = run_dir / "runs" / ("run_%d" % index) / "verify_archive.jsonl"
    _archive.parent.mkdir(parents=True, exist_ok=True)
    SENDS["role"] = "verify_%d" % index

    def _verify(view, digest):
        return verify_view_with_service(
            view, digest, model=TARGET_MODEL, novel_text=info["novel_text"],
            novel_digest=info["novel_digest"], source_references=True,
            max_tokens=VERIFY_MAX_TOKENS, reasoning_effort="none",
            deadline_monotonic=time.monotonic() + 600,
            archive_path=str(_archive))

    _ctl = cv.VerificationController(
        verify=_verify, require_coherence=True,
        validate_record=lambda r, v, d: cv.record_protocol_problems(
            r, v, plan_digest=d, novel_text=info["novel_text"],
            novel_digest=info["novel_digest"]),
        max_verifications=1, novel_digest=info["novel_digest"],
        rule_version=cv.DEFAULT_RULE_VERSION)
    record = _ctl.record_for(info["view"], info["plan_digest"],
                             allow_correction=False)
    from step01_script_gen import outline as o
    attached = dict(info["plan"])
    attached["plan_verification"] = record
    acceptance = [{"code": str(p.get("code") or ""), "uid": str(p.get("uid") or ""),
                   "path": str(p.get("path") or "")}
                  for p in (o.plan_acceptance_problems(attached,
                                                      info["novel_text"]) or [])]
    try:
        _write(run_dir / "runs" / ("run_%d" % index) / "record.json", record)
        _write(run_dir / "runs" / ("run_%d" % index) / "acceptance.json", acceptance)
    except Exception as exc:                                         # noqa: BLE001
        # 记录写不成 ⇒ 本组已取得的证据到此为止，**不再发送**（区别于协议失败）。
        raise EvidenceWriteError("证据落盘失败（run_%d）：%s" % (index, exc)) from exc
    _raw = ""
    _prompt = ""
    try:
        _lines = _archive.read_text(encoding="utf-8").splitlines()
        _last = json.loads(_lines[-1]) if _lines else {}
        _raw = str(_last.get("raw") or "")
        _prompt = str(_last.get("prompt") or "")
    except Exception:                                                # noqa: BLE001
        _raw = ""
    return {"index": index, "role": SENDS["role"],
            "status": str((record or {}).get("status") or "missing"),
            "code": str((record or {}).get("code") or ""),
            "protocol_problems": [str(p.get("code") or "")
                                  for p in ((record or {}).get("problems") or [])
                                  if isinstance(p, dict)],
            "axes": _axes_from_record(record),
            "raw_axes": _axes_from_raw(_raw),
            "raw_sha256": _sha256_text(_raw) if _raw else "",
            "prompt_sha256": _sha256_text(_prompt) if _prompt else "",
            "acceptance": acceptance, "sends_before": SENDS["attempts"]}


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def _conclude(result: dict) -> None:
    """按"准入优先、原始响应退让"填 `comparison_basis` / 三类差异 / 结论."""
    basis, rows = _choose_basis(result.get("runs") or [])
    _axes = [r["axes"] for r in rows] if basis == "admitted" else (
        [r["raw_axes"] for r in rows] if basis == "raw_unadmitted" else [])
    _d = _diff_axes(_axes) if _axes else {"verdict": [], "content": [], "shape": []}
    result["comparison_basis"] = basis
    result["verdict_differences"] = _d["verdict"]
    result["content_differences"] = _d["content"]
    result["shape_differences"] = _d["shape"]
    result["differences"] = _d["verdict"] + _d["content"] + _d["shape"]
    _nv, _nc, _ns = len(_d["verdict"]), len(_d["content"]), len(_d["shape"])
    _n = len(result.get("runs") or [])
    _admitted = len([r for r in (result.get("runs") or [])
                     if r.get("axes", {}).get("status") == "ok"])
    if basis == "admitted":
        if _nv:
            result["conclusion"] = (
                "unstable：%d 份准入记录在 **verdict** 上 %d 处差异"
                "（另 content %d / shape %d）；只报告，不作因果归因"
                % (len(rows), _nv, _nc, _ns))
        elif _nc or _ns:
            result["conclusion"] = (
                "verdict_stable：%d 份准入记录 0 处 verdict 差异；"
                "content %d / shape %d 为文字或协议形状差异" % (len(rows), _nc, _ns))
        else:
            result["conclusion"] = ("stable：%d 份准入记录在各轴逐项一致；"
                                    "不排除运行方差，不作因果归因" % len(rows))
    elif basis == "raw_unadmitted":
        result["conclusion"] = (
            "raw_only：%d/%d 份记录通过协议准入；未准入原始响应在 verdict 上 %d 处"
            "差异（另 content %d / shape %d）——仅诊断，不构成采纳结论，不改判据"
            % (_admitted, _n, _nv, _nc, _ns))
    else:
        result["conclusion"] = ("no_data：有效记录 <2（%d/%d），无法比较 verdict；"
                                "只报告" % (_admitted, _n))
    _stop = str(result.get("stop_reason") or "")
    if _stop and _stop != STOP_COMPLETED:
        # 中途停止 ≠ 样本不足：说清按哪条规则停、已取得几份，**不补抽**。
        _planned = int(result.get("planned_runs") or 0)
        _done = len([r for r in (result.get("runs") or [])
                     if str(r.get("status") or "") != "exception"])
        _aborted = len(result.get("runs") or []) - _done
        _nv2 = len(result.get("verdict_differences") or [])
        result["conclusion"] = (
            "stopped（%s）：已观察 %d/%d 次，其中 %d 份通过协议准入%s；"
            "按规则不再发送、不补抽，不称“三次观察完成”%s"
            % (_stop, _done, _planned, _admitted,
               ("（另有 %d 次观察未完成）" % _aborted) if _aborted else "",
               ("；已取得的准入记录 verdict 差异 %d 处" % _nv2) if _nv2 else ""))
    result["protocol_rejections"] = [
        {"index": r.get("index"), "status": r.get("status"), "code": r.get("code"),
         "problems": r.get("protocol_problems") or []}
        for r in (result.get("runs") or []) if r.get("status") != "ok"]


def _rebuild(run_dir: Path) -> dict:
    """从已有证据重算比较结果（**0 次发送**；不改记录、不改判据）."""
    path = Path(run_dir) / "stability.json"
    result = json.loads(path.read_text(encoding="utf-8"))
    for r in result.get("runs") or []:
        _i = int(r.get("index") or 0)
        _dir = Path(run_dir) / "runs" / ("run_%d" % _i)
        _rec = {}
        if (_dir / "record.json").is_file():
            _rec = json.loads((_dir / "record.json").read_text(encoding="utf-8"))
        r["status"] = str((_rec or {}).get("status") or "missing")
        r["code"] = str((_rec or {}).get("code") or "")
        r["protocol_problems"] = [str(p.get("code") or "")
                                  for p in ((_rec or {}).get("problems") or [])
                                  if isinstance(p, dict)]
        r["axes"] = _axes_from_record(_rec)
        _raw = ""
        _prompt = ""
        _arch = _dir / "verify_archive.jsonl"
        try:
            _lines = _arch.read_text(encoding="utf-8").splitlines()
            _last = json.loads(_lines[-1]) if _lines else {}
            _raw = str(_last.get("raw") or "")
            _prompt = str(_last.get("prompt") or "")
        except Exception:                                            # noqa: BLE001
            _raw = ""
        r["raw_axes"] = _axes_from_raw(_raw)
        r["raw_sha256"] = _sha256_text(_raw) if _raw else ""
        r["prompt_sha256"] = _sha256_text(_prompt) if _prompt else ""
    result["prompt_sha256"] = sorted({str(r.get("prompt_sha256") or "")
                                      for r in (result.get("runs") or [])
                                      if r.get("prompt_sha256")})
    _conclude(result)
    _write(path, result)
    print("rebuilt:", result["comparison_basis"], "|", result["conclusion"])
    print("differences:", len(result["differences"]))
    return result


def _failed_row(index: int, exc: BaseException) -> dict:
    return {"index": index, "role": "verify_%d" % index, "status": "exception",
            "error": "%s: %s" % (type(exc).__name__, exc),
            "axes": {"status": "exception"}}


def _write_exception(run_dir: Path, index: int, exc: BaseException) -> None:
    """异常证据尽力而为：写不成也不能把已取得的记录带走。"""
    try:
        _write(run_dir / "runs" / ("run_%d" % index) / "exception.json",
               {"type": type(exc).__name__, "error": str(exc)})
    except Exception:                                                 # noqa: BLE001
        pass


def run(*, plan_path: Path, novel_path: Path, runs: int = 3, dry: bool = False,
        expected_base: str = OPENCODE_GO_BASE_URL, root=None,
        expect_plan_digest: str = "", expect_novel_digest: str = "",
        expect_prompt_sha256: str = "") -> dict:
    raw_root = os.environ.get("DSH_EXP_ROOT", "").strip() if root is None else str(root)
    if not raw_root:
        raise SystemExit("DSH_EXP_ROOT 未设置：0 次发送，中止")
    root = Path(raw_root)
    if (root.resolve()).is_relative_to((ROOT / "output").resolve()):
        raise SystemExit("DSH_EXP_ROOT 不得位于 output/ 内：0 次发送")

    _reset_sends()
    run_dir = root / ("p4_stability_" + time.strftime("%Y%m%dT%H%M%S"))
    run_dir.mkdir(parents=True, exist_ok=False)
    # 隔离先于任何 shared/step01 导入：日志与派生输出都落在证据目录。
    os.environ.update({
        "OUTPUT_BASE": str(run_dir / "output"),
        "STEP01_LOG_PATH": str(run_dir / "step01.log"),
        "TEXT_TRANSPORT_RETRIES": "0",
        "TEXT_TRANSPORT_CONNECT_RETRIES": "0",
        "CONTENT_VERIFY_MAX_TOKENS": str(VERIFY_MAX_TOKENS),
    })

    try:
        info = _freeze_inputs(Path(plan_path), Path(novel_path))
    except SystemExit:
        raise
    except Exception as exc:                                          # noqa: BLE001
        raise SystemExit("输入冻结失败（%s: %s）：0 次发送"
                         % (type(exc).__name__, exc)) from exc
    # **发送前核对冻结摘要**：缺值或不符都在这里 0 次发送，不自动换文件。
    _missing, _bad = _frozen_identity_problems(
        info, plan_digest=expect_plan_digest, novel_digest=expect_novel_digest,
        prompt_sha256=expect_prompt_sha256)
    if _missing:
        raise SystemExit("缺少冻结摘要 %s：0 次发送" % _missing)
    if _bad:
        raise SystemExit("冻结摘要不符 %s：0 次发送（实际 %s）"
                         % (_bad, {k: info.get(k) for k in _bad}))
    if info["gate_bad"]:
        raise SystemExit("确定性门控未通过：%s；0 次发送" % info["gate_bad"])
    from _text_gen.config import config
    if str(config.PROVIDER) != "agent_plan":
        raise SystemExit("有效通道不是 OpenCode Go（provider=%r）：0 次发送"
                         % config.PROVIDER)
    _base = _effective_base_url()
    if not _base_allowed(_base, expected_base):
        raise SystemExit("生效 base URL %r 不是允许端点 %r：0 次发送"
                         % (_base, expected_base))

    SENDS["expect_prompt_sha256"] = str(info["prompt_sha256"])
    from shared import content_verification as _cv
    _preflight(run_dir)
    _write(run_dir / "input_identity.json", {
        "plan_path": info["plan_path"], "plan_file_sha256": info["plan_file_sha256"],
        "plan_digest": info["plan_digest"], "facts": info["facts"],
        "novel_path": info["novel_path"], "novel_file_sha256": info["novel_file_sha256"],
        "novel_digest": info["novel_digest"], "gate_codes": info["gate_codes"],
        "prompt_sha256": info["prompt_sha256"], "prompt_chars": info["prompt_chars"],
        "model": TARGET_MODEL, "max_tokens": VERIFY_MAX_TOKENS,
        "temperature": 0.0, "reasoning_effort": "none",
        "source_references": True, "require_coherence": True,
        "rule_version": _cv.DEFAULT_RULE_VERSION,
        "base_url": _base, "cap": MAX_SENDS, "runs": runs,
        "expect": {"plan_digest": str(expect_plan_digest).strip(),
                   "novel_digest": str(expect_novel_digest).strip(),
                   "prompt_sha256": str(expect_prompt_sha256).strip()},
        "code": _code_freeze(), "config": _config_freeze(_base)})
    _write(run_dir / "candidate_content.json", info["plan"])

    before = _snapshot()
    _write(run_dir / "production_artifacts.before.json", before)
    _planned = max(1, min(int(runs), MAX_SENDS))
    result = {"schema": "p4.stability.v1", "dry": bool(dry), "runs": [],
              "cap": MAX_SENDS, "sends": 0, "differences": [],
              "conclusion": "", "run_dir": str(run_dir), "stop_reason": "",
              "planned_runs": _planned, "observed_runs": 0,
              "attempted_runs": 0, "stopped_early": False}
    if dry:
        result["stop_reason"] = "dry"
        result["conclusion"] = "dry：预检通过，0 次发送"
        _write(run_dir / "stability.json", result)
        print("dry ok:", info["plan_digest"], "facts:", len(info["facts"]))
        print("run:", run_dir)
        return result

    _install_send_budget(run_dir, _base)
    _stop = ""
    try:
        for _i in range(1, _planned + 1):
            try:
                _rec = _observe(info, _i, run_dir)
                result["runs"].append(_rec)
            except EvidenceWriteError as exc:
                result["runs"].append(_failed_row(_i, exc))
                _write_exception(run_dir, _i, exc)
                _stop = STOP_EVIDENCE
                break
            except Exception as exc:                                  # noqa: BLE001
                result["runs"].append(_failed_row(_i, exc))
                _write_exception(run_dir, _i, exc)
                _stop = SENDS["stop"] or STOP_EXCEPTION
                break
            if SENDS["abort"]:
                _stop = SENDS["stop"] or STOP_EXCEPTION
                break
            # **停止条件生效位置**：只读本次记录的状态，`ok` 才允许下一次观察。
            _status = str(_rec.get("status") or "")
            if _status != "ok":
                _stop = _STOP_BY_STATUS.get(_status, STOP_PROTOCOL)
                break
        else:
            _stop = STOP_COMPLETED
    finally:
        _uninstall_send_budget()

    try:
        after = _snapshot()
        _write(run_dir / "production_artifacts.after.json", after)
    except Exception as exc:                                          # noqa: BLE001
        after = None
        result["artifacts_snapshot_error"] = "%s: %s" % (type(exc).__name__, exc)
    result["sends"] = SENDS["attempts"]
    result["urls"] = list(SENDS["urls"])
    result["prompt_sha256"] = sorted({str(r.get("prompt_sha256") or "")
                                      for r in (result.get("runs") or [])
                                      if r.get("prompt_sha256")})
    result["production_artifacts_unchanged"] = (before == after) if after is not None else None
    result["stop_reason"] = _stop or STOP_EXCEPTION
    result["attempted_runs"] = len(result["runs"])
    result["observed_runs"] = len([r for r in result["runs"]
                                   if str(r.get("status") or "") != "exception"])
    result["stopped_early"] = result["stop_reason"] != STOP_COMPLETED
    _conclude(result)
    print("conclusion:", result["conclusion"])
    print("sends:", result["sends"], "runs:", len(result["runs"]),
          "stop_reason:", result["stop_reason"])
    print("run:", run_dir)
    try:
        _write(run_dir / "stability.json", result)
    except Exception as exc:                                          # noqa: BLE001
        print("证据落盘失败（stability.json）：%s｜stop_reason=%s｜run_dir=%s"
              % (exc, result["stop_reason"], run_dir), file=sys.stderr)
        raise
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", default=str(DEFAULT_PLAN))
    ap.add_argument("--novel", default=str(DEFAULT_NOVEL))
    ap.add_argument("--runs", type=int, default=MAX_SENDS)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--analyze", default="",
                    help="从已有证据目录重算比较（0 次发送）")
    ap.add_argument("--expect-base-url", default=OPENCODE_GO_BASE_URL)
    ap.add_argument("--expect-plan-digest", default="")
    ap.add_argument("--expect-novel-digest", default="")
    ap.add_argument("--expect-prompt-sha256", default="")
    args = ap.parse_args()
    if args.analyze:
        _rebuild(Path(args.analyze))
        return
    if not (args.expect_plan_digest and args.expect_novel_digest
            and args.expect_prompt_sha256):
        ap.error("必须显式提供 --expect-plan-digest / --expect-novel-digest / "
                 "--expect-prompt-sha256（冻结摘要缺失 ⇒ 0 次发送）")
    run(plan_path=Path(args.plan), novel_path=Path(args.novel),
        runs=args.runs, dry=args.dry, expected_base=args.expect_base_url,
        expect_plan_digest=args.expect_plan_digest,
        expect_novel_digest=args.expect_novel_digest,
        expect_prompt_sha256=args.expect_prompt_sha256)


if __name__ == "__main__":
    main()
