"""shared.video_attempts - 远端视频任务的生命周期（计划 §V2/§F2）.

职责边界：本模块**只**负责"任务身份与状态迁移"——它不提交、不下载、不写产物；
所有落盘（事件 seq + fsync、lifecycle 投影、不可变 manifest、accepted 指针）
一律委托 ``shared.enhancement_io``，不得实现第二套提交协议。

三个身份彼此独立，不能互相替代（§F2/§V2）：
- ``request_key``：**事实输入**摘要（内容相同 ⇒ key 相同），不代表同一个远端任务；
- ``attempt_id``：**本地执行/恢复尝试**的身份（每次本地尝试唯一，不复用）；
- ``generation_id``：**一次真实生成 POST** 的身份。恢复（同 task_id）沿用同一
  generation_id，**不计新生成**；两次 POST 共用 generation_id 则是隐瞒费用。

状态机（§V2「身份与合法状态」）：
    draft → ready → submitting → submitted → running → downloaded → checked → accepted
独立分支：submission_unknown / provider_failed / io_failed / rejected / unverified /
commit_unconfirmed。远端任务终局与本地验收终局**分别记录**。
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from shared import enhancement_io as eio

STATE_DRAFT = "draft"
STATE_READY = "ready"
STATE_SUBMITTING = "submitting"
STATE_SUBMITTED = "submitted"
STATE_RUNNING = "running"
STATE_DOWNLOADED = "downloaded"
STATE_CHECKED = "checked"
STATE_ACCEPTED = "accepted"

STATE_SUBMISSION_UNKNOWN = "submission_unknown"
STATE_PROVIDER_FAILED = "provider_failed"
STATE_IO_FAILED = "io_failed"
STATE_REJECTED = "rejected"
STATE_UNVERIFIED = "unverified"
STATE_COMMIT_UNCONFIRMED = "commit_unconfirmed"

#: 正常推进链（顺序迁移）。
FORWARD_CHAIN = (STATE_DRAFT, STATE_READY, STATE_SUBMITTING, STATE_SUBMITTED,
                 STATE_RUNNING, STATE_DOWNLOADED, STATE_CHECKED, STATE_ACCEPTED)

#: 允许的迁移：目标状态 → 允许的来源状态集合。分支状态可从对应来源进入；
#: 终局（accepted）只能从 checked 进入。
TRANSITIONS: dict[str, set[str]] = {
    STATE_READY: {STATE_DRAFT},
    STATE_SUBMITTING: {STATE_READY, STATE_DRAFT},
    STATE_SUBMITTED: {STATE_SUBMITTING},
    STATE_RUNNING: {STATE_SUBMITTED},
    STATE_DOWNLOADED: {STATE_RUNNING, STATE_SUBMITTED},
    STATE_CHECKED: {STATE_DOWNLOADED},
    STATE_ACCEPTED: {STATE_CHECKED},
    STATE_SUBMISSION_UNKNOWN: {STATE_SUBMITTING},
    STATE_PROVIDER_FAILED: {STATE_SUBMITTING, STATE_RUNNING},
    STATE_IO_FAILED: {STATE_DRAFT, STATE_READY, STATE_RUNNING, STATE_DOWNLOADED, STATE_CHECKED},
    STATE_REJECTED: {STATE_CHECKED, STATE_DOWNLOADED},
    STATE_UNVERIFIED: {STATE_DOWNLOADED, STATE_CHECKED},
    STATE_COMMIT_UNCONFIRMED: {STATE_CHECKED, STATE_ACCEPTED},
}

TERMINAL_STATES = {STATE_ACCEPTED, STATE_REJECTED, STATE_UNVERIFIED,
                   STATE_PROVIDER_FAILED, STATE_SUBMISSION_UNKNOWN}


def new_generation_id() -> str:
    """一次真实生成 POST 的身份（进程内唯一即可，跨进程靠前缀区分）。"""
    return f"gen-{os.getpid()}-{time.time_ns()}"


def new_attempt_id(label: str = "run") -> str:
    return f"{label}-{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}-{time.time_ns() % 1_000_000:06d}"


class LifecycleError(eio.EnhancementError):
    def __init__(self, code: str, message: str, **fields):
        super().__init__(code, "lifecycle", message, exit_code=eio.EXIT_REJECTED, **fields)


def open_attempt(package_root: Path, attempt_id: str, *, request_key: str, scope: dict,
                 script_revision: str, provider: str, model_id: str,
                 generation_id: str | None = None, parent_attempt_id: str | None = None,
                 credential_profile_ref: str | None = None,
                 initial_state: str = STATE_DRAFT, task_id: str | None = None,
                 carry: dict | None = None) -> Path:
    """建立一次本地尝试（目录 + 冻结身份 + 首次事件）。返回 attempt 目录。

    ``initial_state`` 供恢复尝试直接以 submitted 起步（task 已存在，见
    ``open_recovery_attempt``）；新建尝试一律 draft。
    """
    attempt = Path(package_root) / eio.ATTEMPTS_DIRNAME / eio.assert_attempt_id(attempt_id)
    attempt.mkdir(parents=True, exist_ok=True)
    payload = dict(carry or {})          # 恢复链继承的冻结事实（冻结请求/镜头身份…）
    payload.update({
        "attempt_id": attempt_id,
        "parent_attempt_id": parent_attempt_id,
        "generation_id": generation_id,
        "request_key": request_key,
        "scope": scope,
        "script_revision": script_revision,
        "provider": provider,
        "model_id": model_id,
        "credential_profile_ref": credential_profile_ref,
        # 起步状态写进冻结身份：恢复校验要按它核状态链（不能猜成 draft）
        "initial_state": initial_state,
        "opened_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    if eio.read_inputs(attempt) is None:
        eio.freeze_inputs(attempt, payload)
    lock = eio.AttemptLock(attempt / eio.LOCK_DIRNAME, purpose=f"open {attempt_id}")
    with lock:
        events, _tail = eio.read_events_strict(attempt)
        if not events:
            eio.append_event(attempt, "opened",
                             after={"state": initial_state},
                             request_key=request_key, generation_id=generation_id,
                             parent_attempt_id=parent_attempt_id, task_id=task_id)
        eio.write_lifecycle(attempt, {**payload, "state": _projected_state(attempt, initial_state),
                                      "projected_from_event_seq": _last_seq(attempt),
                                      "task_id": task_id})
    return attempt


def _last_seq(attempt: Path) -> int:
    events, _ = eio.read_events_strict(attempt)
    return max((int(e.get("seq") or 0) for e in events), default=0)


def _projected_state(attempt: Path, fallback: str) -> str:
    for e in reversed(eio.read_events_strict(attempt)[0]):
        after = e.get("after")
        if isinstance(after, dict) and after.get("state"):
            return str(after["state"])
    return fallback


def current_state(attempt: Path) -> str:
    proj = eio.read_lifecycle(Path(attempt))
    if isinstance(proj, dict) and proj.get("state"):
        return str(proj["state"])
    return _projected_state(Path(attempt), STATE_DRAFT)


def transition(attempt: Path, to_state: str, *, kind: str | None = None, **fields) -> dict:
    """合法迁移 + 事件 + 投影（锁内）。非法迁移拒绝，不静默改写状态。"""
    attempt = Path(attempt)
    if to_state not in TRANSITIONS and to_state != STATE_DRAFT:
        raise LifecycleError("unknown_state", f"未知状态: {to_state}", attempt_id=attempt.name)
    lock = eio.AttemptLock(attempt / eio.LOCK_DIRNAME, purpose=f"transition→{to_state}")
    with lock:
        frm = current_state(attempt)
        allowed = TRANSITIONS.get(to_state, set())
        if frm != to_state and frm not in allowed:
            raise LifecycleError(
                "illegal_transition",
                f"非法状态迁移 {frm} → {to_state}（允许来源: {sorted(allowed)}）",
                attempt_id=attempt.name, field_path="state")
        event = eio.append_event(attempt, kind or f"state:{to_state}",
                                 before={"state": frm}, after={"state": to_state}, **fields)
        proj = eio.read_lifecycle(attempt) or {}
        proj.update({"state": to_state, "attempt_id": attempt.name,
                     "projected_from_event_seq": event["seq"]})
        eio.write_lifecycle(attempt, proj)
        return event


def record_submit(attempt: Path, *, task_id: str | None, credential_profile_ref: str | None,
                  generation_id: str, http_status: int | None = None,
                  tool_exit_code: int | None = None) -> dict:
    """登记一次**真实生成 POST**（成功建任务或已知失败都要记，供费用/次数审计）。"""
    return transition(attempt, STATE_SUBMITTED if task_id else STATE_SUBMISSION_UNKNOWN,
                      kind="submit_post", task_id=task_id,
                      credential_profile_ref=credential_profile_ref,
                      generation_id=generation_id, http_status=http_status,
                      tool_exit_code=tool_exit_code)


def submit_post_count(experiment_root: Path) -> int:
    """实验根下**真实生成 POST** 的总数（从事件历史算，不信任内存计数）。"""
    total = 0
    attempts_root = Path(experiment_root) / eio.ATTEMPTS_DIRNAME
    if not attempts_root.is_dir():
        return 0
    for att in sorted(p for p in attempts_root.iterdir() if p.is_dir()):
        events, _ = eio.read_events_strict(att)
        total += sum(1 for e in events if e.get("kind") == "submit_post")
    return total


def generations(experiment_root: Path) -> dict[str, dict]:
    """按 generation_id 归并的尝试关系（用于证明"恢复不新增 generation"）。"""
    out: dict[str, dict] = {}
    attempts_root = Path(experiment_root) / eio.ATTEMPTS_DIRNAME
    if not attempts_root.is_dir():
        return out
    for att in sorted(p for p in attempts_root.iterdir() if p.is_dir()):
        inputs = eio.read_inputs(att) or {}
        gid = inputs.get("generation_id")
        if not gid:
            continue
        rec = out.setdefault(str(gid), {"attempts": [], "task_id": None, "submit_posts": 0})
        rec["attempts"].append(att.name)
        for e in eio.read_events_strict(att)[0]:
            if e.get("kind") == "submit_post":
                rec["submit_posts"] += 1
                rec["task_id"] = rec["task_id"] or e.get("task_id")
    return out


#: 记录过"本次尝试所用的远端 task 身份"的事件种类。恢复必须认全部——
#: 只认 ``submit_post`` 会让恢复出来的子尝试（身份写在 opened/late_task_id/
#: resume_query）在下一轮被判成 submission_unknown（2026-10-03 复核实测）。
TASK_ID_EVENT_KINDS = ("submit_post", "late_task_id", "resume_query", "opened")


def task_id_from_events(events: list[dict]) -> dict:
    """从事件历史回溯任务身份；后写的事件优先（晚到 ID / 恢复查询是更新的证据）。"""
    task_id = generation_id = source_event = None
    for e in events:
        kind = e.get("kind")
        if kind in TASK_ID_EVENT_KINDS and e.get("task_id"):
            task_id = str(e["task_id"])
            source_event = kind
            generation_id = e.get("generation_id") or generation_id
    return {"task_id": task_id, "generation_id": generation_id,
            "source_event": source_event}


def verify_parent_link(child: Path, parent: Path) -> None:
    """回溯前核**父子链身份**：跨请求/跨 generation 的父子关系一律拒收.

    只看"父目录存在"是不够的（2026-10-04 复核 P1-5）：子尝试属于请求 A 时，若它的
    ``parent_attempt_id`` 指到请求 B 的目录，回溯就会把 **B 的任务**当成 A 的任务查，
    恢复等于查询了别人的任务。判据放在冻结身份里（挂链时写入），不靠命名/时间猜。
    """
    ci = eio.read_inputs(child) or {}
    pi = eio.read_inputs(parent) or {}
    cg, pg = ci.get("generation_id"), pi.get("generation_id")
    if cg and pg and str(cg) != str(pg):
        raise LifecycleError(
            "parent_chain_generation_mismatch",
            f"父子链身份不符：子 attempt {child.name} 的 generation={cg} "
            f"≠ 父 {parent.name} 的 {pg}（跨请求关系，拒绝据此回溯任务身份）",
            attempt_id=child.name, field_path="parent_attempt_id")
    cr, pr = ci.get("request_key"), pi.get("request_key")
    if cr and pr and str(cr) != str(pr):
        raise LifecycleError(
            "parent_chain_request_mismatch",
            f"父子链请求身份不符：子 {child.name} 的 request_key={str(cr)[:12]} "
            f"≠ 父 {parent.name} 的 {str(pr)[:12]}（拒绝据此回溯任务身份）",
            attempt_id=child.name, field_path="parent_attempt_id")


def task_id_of_attempt(attempt: Path, *, follow_parents: bool = True) -> dict:
    """该 attempt 所用任务的身份，必要时沿 ``parent_attempt_id`` 回溯。

    父子关系是**盘上可验证的**：父目录存在，且父子两侧冻结的 generation/request
    身份一致（:func:`verify_parent_link`）；否则拒收，不猜。
    """
    seen: set[str] = set()
    cur: Path | None = Path(attempt)
    while cur is not None and cur.name not in seen:
        seen.add(cur.name)
        found = task_id_from_events(eio.read_events_strict(cur)[0])
        if found["task_id"]:
            return {**found, "attempt": cur.name}
        inputs = eio.read_inputs(cur) or {}
        parent = inputs.get("parent_attempt_id")
        if not follow_parents or not parent:
            break
        cand = cur.parent / str(parent)
        if not cand.is_dir():
            break
        verify_parent_link(cur, cand)
        cur = cand
    return {"task_id": None, "generation_id": None, "source_event": None, "attempt": None}


def open_recovery_attempt(package_root: Path, prev_attempt: Path, *,
                          attempt_id: str | None = None) -> Path:
    """封存失败尝试后的恢复：**新 attempt_id + 显式 parent_attempt_id**，沿用同
    generation_id / task_id（不计新生成；§F2）。

    恢复前先把上一尝试的历史**修到最后确认点**并核 ``seq`` 连续性与状态链：损坏的
    历史不能当权威继续跑。冻结请求与镜头身份**整份继承**给新尝试——否则第二次恢复
    会只剩 request_key，重建请求时退化成默认参数（时长/声音开关全错）。
    """
    prev_attempt = Path(prev_attempt)
    prev_inputs = eio.read_inputs(prev_attempt) or {}
    eio.reconcile_lifecycle(prev_attempt,
                            initial_state=str(prev_inputs.get("initial_state") or STATE_DRAFT),
                            transitions=TRANSITIONS)
    found = task_id_of_attempt(prev_attempt)
    task_id = found["task_id"]
    aid = attempt_id or new_attempt_id("resume")
    carry = dict(prev_inputs)            # frozen_request / shot_index / key_slot 等原样带走
    return open_attempt(
        package_root, aid,
        request_key=str(prev_inputs.get("request_key") or ""),
        scope=prev_inputs.get("scope") or {},
        script_revision=str(prev_inputs.get("script_revision") or ""),
        provider=str(prev_inputs.get("provider") or ""),
        model_id=str(prev_inputs.get("model_id") or ""),
        generation_id=prev_inputs.get("generation_id") or found["generation_id"],
        parent_attempt_id=prev_attempt.name,
        credential_profile_ref=prev_inputs.get("credential_profile_ref"),
        initial_state=STATE_SUBMITTED if task_id else STATE_DRAFT,
        task_id=task_id,
        carry=carry,
    )
