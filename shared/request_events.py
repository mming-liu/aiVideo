# -*- coding: utf-8 -*-
"""请求 / 图片 / QC 的关联事件账本（2026-11-09，U4）.

预算账本（shared.post_budget）负责**发送前扣减**，但它只记录计数，无法回答
「这一次 POST 到底换回了哪张图、那张图后来 QC 成了什么」。本模块补一条
**追加写**的事件流，用 request_id 把三层串起来：

    请求：request_sent / response_received / account_refused / transport_failed
    图片：image_saved（含 sha256、字节、路径）/ image_download_failed
    QC  ：qc_passed / qc_rejected / qc_call_failed / qc_not_run / qc_stale

约束：
  - **只追加**，不改写历史（旧账本保持可读，used 语义不变）；
  - 未知结果保持未知：调用方没拿到终局就不写终局事件，不用"重发"补齐；
  - 写失败不影响主流程（事件是证据，不是生成前提）；
  - 落盘位置默认 chapter_dir()/step02_events.jsonl，可用 POST_EVENTS_FILE 覆盖
    （测试/隔离用）。
"""
from __future__ import annotations

import contextvars
import json
import os
import threading
import time
import uuid
from pathlib import Path

REQUEST_EVENTS = (
    "request_sent", "http_attempt", "response_received", "account_refused", "transport_failed",
    "image_saved", "image_download_failed",
    "qc_http_attempt",
    "qc_passed", "qc_rejected", "qc_call_failed", "qc_not_run", "qc_stale",
    "decision_ledger",
)

_LOCK = threading.Lock()
_CURRENT = contextvars.ContextVar("request_events_current", default="")
_STEP02B_PATH = contextvars.ContextVar("step02b_events_path", default="")


def _append_jsonl(path: Path, record: dict) -> None:
    line = json.dumps(record, ensure_ascii=False) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            with open(path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
    except Exception:
        return


def set_step02b_events_path(path: str | Path | None) -> contextvars.Token:
    return _STEP02B_PATH.set(str(path) if path else "")


def reset_step02b_events_path(token: contextvars.Token) -> None:
    _STEP02B_PATH.reset(token)


def step02b_events_path(context=None) -> Path:
    raw = (os.getenv("STEP02B_EVENTS_FILE") or "").strip()
    if raw:
        return Path(raw)
    if context is not None and getattr(context, "experiment_id", None) and getattr(context, "package_root", None):
        return Path(context.package_root) / "logs" / "step02b_events.jsonl"
    ctx_raw = (_STEP02B_PATH.get() or "").strip()
    if ctx_raw:
        return Path(ctx_raw)
    from shared.paths import chapter_dir
    return Path(chapter_dir()) / "step02b_events.jsonl"


def new_request_id(prefix: str = "req") -> str:
    return "%s-%s-%s" % (prefix, time.strftime("%Y%m%dT%H%M%S"), uuid.uuid4().hex[:8])


def begin_request(kind: str, prefix: str = "req") -> str:
    """开启一次逻辑请求并把 request_id 放进当前上下文（线程/协程内可见）."""
    rid = new_request_id("%s-%s" % (prefix, kind))
    _CURRENT.set(rid)
    return rid


def current_request_id() -> str:
    return _CURRENT.get() or ""


def clear_request() -> None:
    _CURRENT.set("")


def events_path() -> Path:
    raw = (os.getenv("POST_EVENTS_FILE") or "").strip()
    if raw:
        return Path(raw)
    from shared.paths import chapter_dir
    return Path(chapter_dir()) / "step02_events.jsonl"


def _build_record(event: str, *, request_id: str = "", kind: str = "", **fields) -> dict:
    if event not in REQUEST_EVENTS:
        raise ValueError("未知请求事件: %r" % (event,))
    record = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "event": event,
        "request_id": request_id or current_request_id(),
        "kind": kind,
        "pid": os.getpid(),
    }
    for key, value in fields.items():
        if value is not None:
            record[key] = value
    return record


def emit(event: str, *, request_id: str = "", kind: str = "", **fields) -> None:
    """追加一条事件；未知事件名/写盘失败都不抛给主流程."""
    record = _build_record(event, request_id=request_id, kind=kind, **fields)
    try:
        _append_jsonl(events_path(), record)
    except Exception:
        return


def emit_step02b(
    event: str,
    *,
    context=None,
    events_file: str | Path | None = None,
    request_id: str = "",
    kind: str = "",
    **fields,
) -> None:
    """追加一条 Step02b 视频提交/恢复/终局事件（写隔离候选包日志或章级 step02b_events.jsonl）."""
    record = _build_record(event, request_id=request_id, kind=kind, **fields)
    try:
        target = Path(events_file) if events_file else step02b_events_path(context)
        _append_jsonl(target, record)
    except Exception:
        return


def read_events(path: str | Path | None = None) -> list[dict]:
    target = Path(path) if path else events_path()
    out: list[dict] = []
    try:
        with open(target, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if isinstance(item, dict):
                    out.append(item)
    except FileNotFoundError:
        return []
    except OSError:
        return []
    return out


def events_for(request_id: str, path: str | Path | None = None) -> list[dict]:
    return [e for e in read_events(path) if e.get("request_id") == request_id]


def emit_decision_ledger(
    *,
    work_id: str = "",
    chapter_scope: str = "",
    script_revision: str = "",
    shot_index: int | None = None,
    frame_role: str = "",
    kf_index: int | None = None,
    shot_complexity: str = "",
    provider: str = "",
    model_id: str = "",
    prompt_template_version: str = "",
    request_digest: str = "",
    prompt_version: str = "",
    ref_strategy: str = "",
    ref_identities: list[str] | None = None,
    attempt_index: int | None = None,
    operation_id: str = "",
    image_sha256: str = "",
    action_type: str = "generate",
    defect_class: str = "none",
    qc_verdict: str = "",
    human_usability: str | None = None,
    non_target_damage: bool | None = None,
    estimated_cost_cny: float | None = None,
    request_id: str = "",
    detail: str = "",
) -> None:
    """Append a structured Stage 4 decision ledger record into step02_events.jsonl."""
    emit(
        "decision_ledger",
        request_id=request_id,
        kind="image_decision",
        work_id=work_id,
        chapter_scope=chapter_scope,
        script_revision=script_revision,
        shot_index=shot_index,
        frame_role=frame_role,
        kf_index=kf_index,
        shot_complexity=shot_complexity or "unknown",
        provider=provider,
        model_id=model_id,
        prompt_template_version=prompt_template_version,
        request_digest=request_digest,
        prompt_version=prompt_version,
        ref_strategy=ref_strategy,
        ref_identities=list(ref_identities) if ref_identities is not None else [],
        attempt_index=attempt_index,
        operation_id=operation_id,
        image_sha256=image_sha256,
        action_type=action_type,
        defect_class=defect_class,
        qc_verdict=qc_verdict,
        human_usability=human_usability if human_usability is not None else "unknown",
        non_target_damage=non_target_damage if non_target_damage is not None else "unknown",
        estimated_cost_cny=estimated_cost_cny,
        detail=detail[:240] if detail else None,
    )


def summarize(path: str | Path | None = None) -> dict:
    """按事件/结果计数；用于报告与恢复核对（不推断缺失的终局）."""
    counts: dict[str, int] = {}
    requests: set[str] = set()
    for event in read_events(path):
        counts[event["event"]] = counts.get(event["event"], 0) + 1
        if event.get("request_id"):
            requests.add(event["request_id"])
    return {"events": counts, "requests": len(requests)}


def emit_video_decision_ledger(
    *,
    work_id: str = "",
    chapter_scope: str = "",
    script_revision: str = "",
    shot_index: int | None = None,
    provider: str = "",
    model_id: str = "",
    media_policy: str = "",
    request_key: str = "",
    content_key: str = "",
    operation_id: str = "",
    generation_id: str = "",
    task_id: str = "",
    attempt_id: str = "",
    action_type: str = "generate",
    outcome: str = "",
    error_code: str = "",
    recovered: bool = False,
    submit_posts: int | None = None,
    context=None,
    request_id: str = "",
    detail: str = "",
) -> None:
    """Append a structured Step02b decision_ledger record keyed by 4-field shot tuple."""
    emit_step02b(
        "decision_ledger",
        context=context,
        request_id=request_id,
        kind="video_decision",
        work_id=work_id,
        chapter_scope=chapter_scope,
        script_revision=script_revision,
        shot_index=shot_index,
        provider=provider,
        model_id=model_id,
        media_policy=media_policy or "unknown",
        request_key=request_key,
        content_key=content_key,
        operation_id=operation_id or generation_id,
        generation_id=generation_id,
        task_id=task_id,
        attempt_id=attempt_id,
        action_type=action_type,
        outcome=outcome,
        error_code=error_code,
        recovered=bool(recovered),
        submit_posts=submit_posts,
        detail=detail[:240] if detail else None,
    )


__all__ = ["REQUEST_EVENTS", "begin_request", "clear_request", "current_request_id",
           "emit", "emit_decision_ledger", "emit_step02b", "emit_video_decision_ledger",
           "events_for", "events_path", "new_request_id", "read_events",
           "reset_step02b_events_path", "set_step02b_events_path",
           "step02b_events_path", "summarize"]
