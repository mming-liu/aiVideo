# -*- coding: utf-8 -*-
"""**一次性审计授权**的占用、发送尝试与结果记账（M07/M09，2026-10-29）.

解决的问题（本包唯一目标）：同一份一次性授权被重复消费，以及**未发送也被记作发送**。

三段事实分开记，互不推断：

  · `claim.json`  —— **授权占用**（谁先拿到这份授权的排他发送资格）。用 `O_CREAT|O_EXCL`
    创建：同一授权的并发启动只有一个进程能创建成功，另一个拿到 `FileExistsError`。
    **不能**写成「先判断不存在、再普通写入」——那两步之间就是重复消费的窗口。
  · `attempts.jsonl` —— **SDK 发送尝试**。由 provider 在**真正调用** `client.responses.create()`
    之前写入并 fsync：先落盘、再发请求。因此「有 attempt」= 进过发送边界；
    「有 claim、没有 attempt」= 占了授权但**确认没有发送**（前置拒收/写入失败）。
  · `outcomes.jsonl` —— 每次尝试的结果（`returned` / `failed` / `unknown`）。
    attempt 之后没有 outcome = 进程在发送边界附近中断 ⇒ 状态**不明**，不是 0 次发送。

授权已占用后，即使不能证明供应商收到请求，也**不得自动重用**：这是费用上限的保守
处理，不等于「供应商已扣费」。本模块不删除占用记录、不按文件年龄解锁、不扫描运行目录。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from shared.paths import step01_audit_authorization_dir, validate_authorization_id

#: 本包只支持一次性授权（多额度分配不在范围内，也不为未来需求预埋）.
MAX_SENDS = 1

CLAIM_FILE = "claim.json"
ATTEMPTS_FILE = "attempts.jsonl"
OUTCOMES_FILE = "outcomes.jsonl"
RESULT_FILE = "result.json"

ATTEMPT_OUTCOMES = ("returned", "failed", "unknown")
#: 授权的**发送状态**（与「审计结论」「提交状态」分开）.
SEND_STATES = ("not_sent", "reserved", "returned", "failed", "unknown")

SCHEMA = "step01.audit_authorization/1"


class AuthorizationError(RuntimeError):
    """授权状态相关的失败基类（调用方一律按**零发送**停止）."""


class AuthorizationClaimed(AuthorizationError):
    """这份授权已被占用（重复/并发启动的后来者）."""


class AuthorizationBindingMismatch(AuthorizationError):
    """同一个授权 id 指向了不同的对象/规则/通道绑定（拒绝改绑后重发）."""


class AuthorizationStateError(AuthorizationError):
    """授权状态不可读/不可写/只写了一半 ⇒ 状态不明，拒绝发送，也不删除现场."""


class SendNotPermitted(AuthorizationError):
    """本次逻辑调用不允许再进入发送边界（一次授权只允许一次尝试）."""


def _now() -> str:
    """带时区的本地时间（审计记录要能自证是哪一刻，不能用无时区的裸字符串）."""
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")


def authorization_binding_digest(binding: dict) -> str:
    """授权绑定摘要：**规范化 JSON** 的 sha256 截断（键序固定，跨进程可复算）."""
    blob = json.dumps(dict(binding or {}), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:                                       # noqa: BLE001
        raise AuthorizationStateError(f"授权状态文件不可读（状态不明，拒绝发送）: {path}: {exc}") from exc


def _read_jsonl(path: Path) -> list:
    """严格读 JSONL：**缺内容/截断/坏行**一律报状态不明（不跳过、不重建）."""
    path = Path(path)
    if not path.is_file():
        return []
    out = []
    try:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            text = line.strip()
            if not text:
                continue
            try:
                out.append(json.loads(text))
            except json.JSONDecodeError as exc:
                raise AuthorizationStateError(
                    f"授权状态记录第 {number} 行损坏（状态不明，拒绝发送）: {path}: {exc}") from exc
    except AuthorizationStateError:
        raise
    except Exception as exc:                                       # noqa: BLE001
        raise AuthorizationStateError(f"授权状态记录不可读（状态不明，拒绝发送）: {path}: {exc}") from exc
    return out


def _append_jsonl(path: Path, row: dict) -> None:
    """追加一行并 fsync；失败 ⇒ 抛错（调用方据此**不发送**）."""
    line = json.dumps(row, ensure_ascii=False) + "\n"
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        raise AuthorizationStateError(f"授权状态写入失败（未发送）: {path}: {exc}") from exc


def _write_json_atomic(path: Path, row: dict) -> str:
    """原子写（tmp + replace）；返回错误文本，不抛（已发生的审计结果不能被它吞掉）."""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(row, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp), str(path))
        return ""
    except OSError as exc:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return f"授权结果留档写入失败: {exc}"


def read_state(state_dir) -> dict:
    """只读回授权状态：`{claim, attempts, outcomes, result}`；不可读即抛错.

    **不把缺失解释成 0 次发送**：这里只返回事实，状态判定在 `describe_state()` 里。
    """
    state_dir = Path(state_dir)
    if not state_dir.exists():
        return {"claim": None, "attempts": [], "outcomes": [], "result": None}
    claim_path = state_dir / CLAIM_FILE
    attempts_path = state_dir / ATTEMPTS_FILE
    outcomes_path = state_dir / OUTCOMES_FILE
    result_path = state_dir / RESULT_FILE
    claim = _read_json(claim_path) if claim_path.is_file() else None
    if claim is not None and not isinstance(claim, dict):
        raise AuthorizationStateError(f"授权占用记录格式非法（状态不明）: {claim_path}")
    return {
        "claim": claim,
        "attempts": _read_jsonl(attempts_path),
        "outcomes": _read_jsonl(outcomes_path),
        "result": _read_json(result_path) if result_path.is_file() else None,
    }


def describe_state(state: dict) -> dict:
    """把事实翻译成**发送状态**（`not_sent` / `reserved` / `returned` / `failed` / `unknown`）.

    `reserved` 表示「授权已占用，但尚无发送尝试」——确认没有进入 SDK，但额度已经不可重用。
    """
    claim = state.get("claim")
    attempts = list(state.get("attempts") or [])
    outcomes = list(state.get("outcomes") or [])
    by_attempt = {}
    for row in outcomes:
        if isinstance(row, dict) and row.get("attempt_id"):
            by_attempt[str(row["attempt_id"])] = str(row.get("outcome") or "")
    attempt_ids = [str(a.get("attempt_id") or "") for a in attempts if isinstance(a, dict)]
    if claim is None and not attempts:
        send_state = "not_sent"
    elif claim is None:
        # 有尝试却没有占用凭据 = 现场不完整；按不明处理，不猜没发过。
        send_state = "unknown"
    elif not attempts:
        send_state = "reserved"
    elif any(not by_attempt.get(aid) for aid in attempt_ids):
        send_state = "unknown"
    elif any(by_attempt.get(aid) == "failed" for aid in attempt_ids):
        send_state = "failed"
    elif any(by_attempt.get(aid) == "unknown" for aid in attempt_ids):
        send_state = "unknown"
    else:
        send_state = "returned"
    result = state.get("result") if isinstance(state.get("result"), dict) else None
    return {
        "send_state": send_state,
        "claimed": bool(claim),
        "claimed_at": (claim or {}).get("claimed_at") if isinstance(claim, dict) else None,
        "claim_run_id": (claim or {}).get("run_id") if isinstance(claim, dict) else None,
        "claim_pid": (claim or {}).get("pid") if isinstance(claim, dict) else None,
        "claim_call_id": (claim or {}).get("call_id") if isinstance(claim, dict) else None,
        "claim_out_root": (claim or {}).get("out_root") if isinstance(claim, dict) else None,
        "binding_digest": (claim or {}).get("binding_digest") if isinstance(claim, dict) else None,
        "attempts": len(attempts),
        "attempt_ids": attempt_ids,
        "outcomes": len(outcomes),
        "send_attempted": bool(attempts),
        "finished": result is not None,
        "result_status": (result or {}).get("status") if isinstance(result, dict) else None,
    }


#: 通道 → 允许的**传输端点标签**（provider 在 `before_send` 里报的那个）.
#: 只做"同一通道不许换端点"的族级核对：具体走 responses 还是 chat 由通道配置决定，
#: 这里不替通道选型，也不把某个标签写成唯一合法值。
PROVIDER_ENDPOINTS = {
    "agent_plan": ("chat.completions",),
    "deepseek": ("responses.create", "chat.completions.create"),
}


def _chain_problem(code: str, *, kind: str = "attempt", ref: str = "",
                   path: str = "", expected=None, actual=None, reason: str = "") -> dict:
    """一条**身份关联**问题：码 + 记录身份 + 字段路径 + 期望 + 实际."""
    return {"code": code, "kind": kind, "ref": str(ref or ""),
            "path": str(path or ""), "expected": expected, "actual": actual,
            "reason": str(reason or "")}


def validate_authorization_chain(state: dict, *, expected: "dict | None" = None) -> list:
    """逐条核验 `claim → attempt → outcome → 调用台账` 的**身份关联**（R05/F5）.

    与 `describe_state` 的分工：那边回答"发送状态是什么"，这里回答"这些记录能不能
    互相对上"。`describe_state` 过去按**数量与唯一性**判断（attempt 数 == outcome 数、
    attempt_id 不重复），因此**数量完全相同但身份被交换**的现场会被判完整——
    `attempt/A` 的 outcome 记到 `attempt/B` 头上时，计数一模一样。

    `expected` 由入口按**本次运行**给出：`authorization_id` / `binding_digest` /
    `run_id` / `provider` / `model` / `call_ids`（调用台账里本次运行的行）。缺哪个就
    跳过哪一项（不凭空补造），但**给了就必须对上**。

    返回值是问题记录（空表 = 关联成立），每条带 code / ref / path / expected / actual，
    调用方直接并入证据问题，不用从文案里猜是哪条记录。
    """
    exp = dict(expected or {})
    claim = state.get("claim") if isinstance(state.get("claim"), dict) else None
    attempts = [a for a in (state.get("attempts") or []) if isinstance(a, dict)]
    outcomes = [o for o in (state.get("outcomes") or []) if isinstance(o, dict)]
    problems: list = []
    auth_id = str(exp.get("authorization_id") or "")
    binding = str(exp.get("binding_digest") or "")
    run_id = str(exp.get("run_id") or "")

    if not attempts and claim is None:
        # 没有发送尝试、也没有占用：没有链要核（`not_sent` 是合法状态）
        return problems

    if claim is None:
        problems.append(_chain_problem("authorization_claim_missing", kind="claim",
                                       expected=auth_id, actual=None,
                                       reason="有发送尝试却没有占用凭据：现场不完整"))
    else:
        if auth_id and str(claim.get("authorization_id") or "") != auth_id:
            problems.append(_chain_problem("authorization_id_mismatch", kind="claim",
                                           ref=auth_id,
                                           path="claim.authorization_id",
                                           expected=auth_id,
                                           actual=str(claim.get("authorization_id") or ""),
                                           reason="占用凭据不属于本次授权"))
        if binding and str(claim.get("binding_digest") or "") != binding:
            problems.append(_chain_problem("authorization_binding_mismatch", kind="claim",
                                           ref=auth_id, path="claim.binding_digest",
                                           expected=binding,
                                           actual=str(claim.get("binding_digest") or ""),
                                           reason="占用凭据绑定的是另一份对象/规则/通道"))
        if run_id and str(claim.get("run_id") or "") != run_id:
            problems.append(_chain_problem("authorization_run_mismatch", kind="claim",
                                           ref=auth_id, path="claim.run_id",
                                           expected=run_id,
                                           actual=str(claim.get("run_id") or ""),
                                           reason="占用凭据来自另一次运行"))
        # 2026-10-31 复核：占用记录也带 call_id——不核它，就无法证明「占用」与「发送」
        # 是同一次调用（把 claim.call_id 换成别的调用，旧实现照样返回空表）。
        _claim_call = str(claim.get("call_id") or "")
        _known_calls = [str(c) for c in (exp.get("call_ids") or []) if str(c or "").strip()]
        if str(exp.get("call_id") or ""):
            _known_calls = [str(exp["call_id"])]
        # 只有真的发生过发送尝试时，"占用没带 call_id"才妨碍关联；
        # 纯粹的 reserved（占用但未发送）没有可关联对象，不算链上问题。
        if not _claim_call and attempts:
            problems.append(_chain_problem("claim_call_id_missing", kind="claim",
                                           ref=auth_id, path="claim.call_id",
                                           reason="占用凭据没有 call_id，无法与发送关联"))
        elif _claim_call and _known_calls and _claim_call not in _known_calls:
            problems.append(_chain_problem("claim_call_mismatch", kind="claim",
                                           ref=auth_id, path="claim.call_id",
                                           expected=_known_calls,
                                           actual=_claim_call,
                                           reason="占用凭据对应的不是本次调用"))

    if len(attempts) > MAX_SENDS:
        problems.append(_chain_problem("attempt_over_single_send_cap",
                                       expected=MAX_SENDS, actual=len(attempts),
                                       reason="一次性授权最多一次发送尝试"))

    seen: list = []
    endpoints: set = set()
    for index, row in enumerate(attempts, 1):
        aid = str(row.get("attempt_id") or "")
        if not aid:
            problems.append(_chain_problem("attempt_id_missing", ref="",
                                           path="attempts[%d].attempt_id" % (index - 1),
                                           reason="发送尝试没有 attempt_id，无法关联结果"))
        elif aid in seen:
            problems.append(_chain_problem("attempt_id_duplicated", ref=aid,
                                           path="attempts[%d].attempt_id" % (index - 1),
                                           actual=aid,
                                           reason="同一个 attempt_id 出现两次，结果无法唯一归属"))
        seen.append(aid)
        if int(row.get("attempt_index") or 0) != index:
            problems.append(_chain_problem("attempt_index_mismatch", ref=aid,
                                           path="attempts[%d].attempt_index" % (index - 1),
                                           expected=index,
                                           actual=row.get("attempt_index"),
                                           reason="尝试序号与记录顺序不一致"))
        for field, want in (("authorization_id", auth_id),
                            ("binding_digest", binding),
                            ("run_id", run_id)):
            if want and str(row.get(field) or "") != want:
                problems.append(_chain_problem("%s_mismatch" % field, ref=aid,
                                               path="attempts[%d].%s" % (index - 1, field),
                                               expected=want,
                                               actual=str(row.get(field) or ""),
                                               reason="发送尝试的身份与本次运行不符"))
        if claim is not None:
            for field in ("authorization_id", "binding_digest"):
                want = str(claim.get(field) or "")
                if want and str(row.get(field) or "") != want:
                    problems.append(_chain_problem("attempt_claim_mismatch", ref=aid,
                                                   path="attempts[%d].%s" % (index - 1, field),
                                                   expected=want,
                                                   actual=str(row.get(field) or ""),
                                                   reason="发送尝试与占用凭据不是同一份授权"))
        if exp.get("provider") and str(row.get("provider") or "") != str(exp["provider"]):
            problems.append(_chain_problem("attempt_provider_mismatch", ref=aid,
                                           path="attempts[%d].provider" % (index - 1),
                                           expected=str(exp["provider"]),
                                           actual=str(row.get("provider") or ""),
                                           reason="发送尝试走的是另一条通道"))
        if exp.get("model") and str(row.get("model") or "") != str(exp["model"]):
            problems.append(_chain_problem("attempt_model_mismatch", ref=aid,
                                           path="attempts[%d].model" % (index - 1),
                                           expected=str(exp["model"]),
                                           actual=str(row.get("model") or ""),
                                           reason="发送尝试用的不是本次声明的模型"))
        endpoint = str(row.get("endpoint") or "")
        if endpoint:
            endpoints.add(endpoint)
        _row_call = str(row.get("call_id") or "")
        if not _row_call.strip():
            problems.append(_chain_problem("attempt_call_id_missing", ref=aid,
                                           path="attempts[%d].call_id" % (index - 1),
                                           reason="发送尝试缺 call_id，无法与调用台账关联"))
        elif claim is not None and str(claim.get("call_id") or "") \
                and _row_call != str(claim.get("call_id") or ""):
            problems.append(_chain_problem("attempt_claim_call_mismatch", ref=aid,
                                           path="attempts[%d].call_id" % (index - 1),
                                           expected=str(claim.get("call_id") or ""),
                                           actual=_row_call,
                                           reason="发送尝试与占用凭据不是同一次调用"))

    if len(endpoints) > 1:
        problems.append(_chain_problem("attempt_endpoint_drifted",
                                       expected=sorted(endpoints)[0],
                                       actual=sorted(endpoints),
                                       reason="同一次授权里端点发生了变化"))
    provider = str(exp.get("provider") or "")
    allowed = PROVIDER_ENDPOINTS.get(provider)
    if allowed and endpoints:
        for endpoint in sorted(endpoints):
            if endpoint not in allowed:
                problems.append(_chain_problem("attempt_endpoint_mismatch",
                                               expected=list(allowed), actual=endpoint,
                                               path="attempts[].endpoint",
                                               reason="端点标签不属于该通道"))

    by_attempt: dict = {}
    for index, row in enumerate(outcomes):
        aid = str(row.get("attempt_id") or "")
        path = "outcomes[%d].attempt_id" % index
        if not aid:
            problems.append(_chain_problem("outcome_attempt_id_missing", kind="outcome",
                                           path=path,
                                           reason="发送结果没有 attempt_id，无法关联尝试"))
            continue
        if aid not in seen:
            problems.append(_chain_problem("outcome_orphaned", kind="outcome", ref=aid,
                                           path=path, actual=aid,
                                           reason="发送结果指向一条不存在的发送尝试"))
            continue
        if aid in by_attempt:
            problems.append(_chain_problem("outcome_duplicated", kind="outcome", ref=aid,
                                           path=path, actual=aid,
                                           reason="同一次发送有两条结果记录，终局不唯一"))
            continue
        # 2026-10-31 复核：结果状态本身也要合法（returned/failed/unknown）。
        # 旧实现只核 attempt_id 对不对，把 outcome 改成任意字符串仍然返回空表。
        _status = str(row.get("outcome") or "")
        if _status not in ATTEMPT_OUTCOMES:
            problems.append(_chain_problem("outcome_status_invalid", kind="outcome", ref=aid,
                                           path="outcomes[%d].outcome" % index,
                                           expected=list(ATTEMPT_OUTCOMES),
                                           actual=_status,
                                           reason="发送结果不是允许的终局状态"))
        by_attempt[aid] = row

    for aid in seen:
        if aid and aid not in by_attempt:
            problems.append(_chain_problem("outcome_missing", kind="outcome", ref=aid,
                                           path="outcomes",
                                           expected="一条结果记录", actual=None,
                                           reason="发送尝试没有结果记录：状态不明，不是 0 次发送"))

    call_ids = [str(c) for c in (exp.get("call_ids") or []) if str(c or "").strip()]
    if call_ids or exp.get("ledger_checked"):
        for row in attempts:
            cid = str(row.get("call_id") or "")
            if cid and cid not in call_ids:
                problems.append(_chain_problem("attempt_call_missing_from_ledger",
                                               ref=cid,
                                               path="step01_calls.jsonl",
                                               expected="本次调用的台账行", actual=None,
                                               reason="发送尝试的 call_id 不在调用台账里"))
        if len(call_ids) < len(attempts):
            problems.append(_chain_problem("ledger_rows_lt_attempts", kind="ledger",
                                           expected=len(attempts), actual=len(call_ids),
                                           path="step01_calls.jsonl",
                                           reason="调用台账行数少于发送尝试数"))
    return problems

class AuditAuthorization:
    """一份一次性审计授权：**绑定校验 + 排他占用 + 发送尝试 + 结果**.

    生命周期（全部发生在真实入口里）：
      `claim()`（排他创建 claim.json）→ `before_send()`（写 attempt 后返回 attempt_id，
      provider 拿到它才真正调用 SDK）→ `record_send_outcome()` → `write_result()`。

    该对象同时充当 `_text_gen.send_guard` 的守卫实现：接口只有
    `before_send(provider=, model=, endpoint=, call_id=)` 与
    `record_send_outcome(attempt_id, outcome, metadata)`。
    """

    def __init__(self, *, authorization_id: str, binding: dict, state_dir,
                 run_id: str = "", out_root: str = "") -> None:
        self.authorization_id = validate_authorization_id(authorization_id)
        self.binding = dict(binding or {})
        self.binding_digest = authorization_binding_digest(self.binding)
        self.state_dir = Path(state_dir)
        self.run_id = str(run_id or "")
        self.out_root = str(out_root or "")
        self._claimed = False
        self._attempt_count = 0
        self._write_errors: list[str] = []

    # -- 构造 -----------------------------------------------------------
    @classmethod
    def for_state_dir(cls, authorization_id: str, binding: dict, **kwargs) -> "AuditAuthorization":
        """按**稳定 id** 解析状态目录（与运行根/包文件名无关）."""
        return cls(authorization_id=authorization_id, binding=binding,
                   state_dir=step01_audit_authorization_dir(authorization_id), **kwargs)

    def read_state(self) -> dict:
        return read_state(self.state_dir)

    def summary(self) -> dict:
        """当前状态摘要；状态不可读时返回 `unknown` 而不是抛给调用方."""
        try:
            state = self.read_state()
        except AuthorizationStateError as exc:
            return {"send_state": "unknown", "claimed": None, "error": str(exc),
                    "attempts": None, "finished": False}
        row = describe_state(state)
        row["state_dir"] = str(self.state_dir)
        row["authorization_id"] = self.authorization_id
        return row

    # -- 占用 -----------------------------------------------------------
    def claim(self, *, call_id: str = "") -> dict:
        """**排他占用**：`O_CREAT|O_EXCL`；已存在 ⇒ 比对绑定后拒收.

        写入并 fsync 成功才算占用完成；写失败时**不删半截文件**（按状态不明拒绝），
        也绝不回退到普通覆盖写。
        """
        if self._claimed:
            return {"authorization_id": self.authorization_id,
                    "binding_digest": self.binding_digest, "reused": True}
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AuthorizationStateError(f"授权状态目录创建失败（未发送）: {exc}") from exc
        claim_path = self.state_dir / CLAIM_FILE
        payload = {
            "schema": SCHEMA,
            "authorization_id": self.authorization_id,
            "binding_digest": self.binding_digest,
            "binding": self.binding,
            "run_id": self.run_id,
            "call_id": str(call_id or ""),
            "pid": os.getpid(),
            "out_root": self.out_root,
            "claimed_at": _now(),
        }
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
        try:
            fd = os.open(str(claim_path), flags)
        except FileExistsError as exc:
            # 并发败者可能在胜者「已创建、未写完」之间读取（复核 2026-10-30）。
            # 有界重读：能读到完整记录就按"已占用/绑定不符"拒收；仍读不出则按状态不明拒收。
            existing = None
            _last_err = None
            for _ in range(4):
                try:
                    existing = _read_json(claim_path)
                    _last_err = None
                    break
                except AuthorizationStateError as _exc:              # noqa: PERF203
                    _last_err = _exc
                    time.sleep(0.02)
            if existing is None and _last_err is not None:
                raise _last_err from exc
            stored = str((existing or {}).get("binding_digest") or "")
            if stored != self.binding_digest:
                raise AuthorizationBindingMismatch(
                    f"授权 {self.authorization_id} 已绑定不同对象/规则/通道"
                    f"（已存 {stored or '缺失'}，本次 {self.binding_digest}）；不得改绑后重发") from exc
            raise AuthorizationClaimed(
                f"授权 {self.authorization_id} 已被占用（{existing.get('claimed_at')} / "
                f"run_id={existing.get('run_id')}）；同一授权累计至多一次发送") from exc
        except OSError as exc:
            raise AuthorizationStateError(f"授权占用写入失败（未发送）: {exc}") from exc
        try:
            os.write(fd, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
            os.fsync(fd)
        except OSError as exc:
            raise AuthorizationStateError(
                f"授权占用写入未完成（状态不明，未发送）: {exc}") from exc
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
        self._claimed = True
        return payload

    # -- 发送边界（`_text_gen.send_guard` 的守卫接口） ---------------------
    def before_send(self, *, provider: str, model: str, endpoint: str,
                    call_id: str = "") -> str:
        """发送前调用：占用授权 + 落 attempt，返回 `attempt_id`；任何失败都**不得**发送.

        attempt 记录先 fsync 再返回：这样「授权已占用、attempt 已落盘、SDK 未返回」的
        崩溃会留下**可识别的中间态**，而不是被误报成 0 次发送。
        """
        cid = str(call_id or "").strip()
        if not cid:
            raise AuthorizationStateError("发送边界缺少 call_id（无法关联授权，未发送）")
        if self._attempt_count >= MAX_SENDS:
            raise SendNotPermitted(
                f"授权 {self.authorization_id} 的一次性发送资格已用（第 {self._attempt_count + 1} 次被拒绝）")
        self.claim(call_id=cid)
        attempt_id = "att_" + uuid.uuid4().hex[:16]
        record = {
            "schema": SCHEMA,
            "attempt_id": attempt_id,
            "attempt_index": self._attempt_count + 1,
            "authorization_id": self.authorization_id,
            "binding_digest": self.binding_digest,
            "run_id": self.run_id,
            "call_id": cid,
            "provider": str(provider or ""),
            "model": str(model or ""),
            "endpoint": str(endpoint or ""),
            "pid": os.getpid(),
            "at": _now(),
        }
        _append_jsonl(self.state_dir / ATTEMPTS_FILE, record)
        self._attempt_count += 1
        return attempt_id

    def record_send_outcome(self, attempt_id: str, outcome: str,
                            metadata: "dict | None" = None) -> str:
        """记一次尝试的结果；**不抛**（请求已经发出，不能因为记账失败掩盖结果）.

        写失败会被记进 `write_errors` 并让调用方把证据标为不完整——但绝不重发。
        """
        aid = str(attempt_id or "").strip()
        if not aid:
            err = "发送结果缺少 attempt_id（无法关联）"
            self._write_errors.append(err)
            return err
        if outcome not in ATTEMPT_OUTCOMES:
            err = f"非法发送结果 {outcome!r}（只允许 {ATTEMPT_OUTCOMES}）"
            self._write_errors.append(err)
            return err
        row = {"schema": SCHEMA, "attempt_id": aid, "outcome": outcome,
               "at": _now(), "metadata": dict(metadata or {})}
        try:
            _append_jsonl(self.state_dir / OUTCOMES_FILE, row)
        except AuthorizationStateError as exc:
            print(f"[WARN] 发送结果留档失败（**证据不完整**，不重发）: {exc}")
            self._write_errors.append(str(exc))
            return str(exc)
        return ""

    # -- 终局 -----------------------------------------------------------
    def write_result(self, row: dict) -> str:
        """写本次授权的终局记录（审计结论/提交状态/证据），返回错误文本."""
        payload = {"schema": SCHEMA, "authorization_id": self.authorization_id,
                   "binding_digest": self.binding_digest, "run_id": self.run_id,
                   "finished_at": _now(), **(row or {})}
        err = _write_json_atomic(self.state_dir / RESULT_FILE, payload)
        if err:
            self._write_errors.append(err)
        return err

    @property
    def write_errors(self) -> list:
        return list(self._write_errors)


__all__ = [
    "ATTEMPT_OUTCOMES", "AuditAuthorization", "AuthorizationBindingMismatch",
    "AuthorizationClaimed", "AuthorizationError", "AuthorizationStateError",
    "MAX_SENDS", "PROVIDER_ENDPOINTS", "SEND_STATES", "SendNotPermitted",
    "authorization_binding_digest", "validate_authorization_chain",
    "describe_state", "read_state",
]
