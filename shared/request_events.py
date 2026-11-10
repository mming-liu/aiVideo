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
    "request_sent", "response_received", "account_refused", "transport_failed",
    "image_saved", "image_download_failed",
    "qc_passed", "qc_rejected", "qc_call_failed", "qc_not_run", "qc_stale",
)

_LOCK = threading.Lock()
_CURRENT = contextvars.ContextVar("request_events_current", default="")


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


def emit(event: str, *, request_id: str = "", kind: str = "", **fields) -> None:
    """追加一条事件；未知事件名/写盘失败都不抛给主流程."""
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
    line = json.dumps(record, ensure_ascii=False) + "\n"
    try:
        path = events_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            with open(path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
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


def summarize(path: str | Path | None = None) -> dict:
    """按事件/结果计数；用于报告与恢复核对（不推断缺失的终局）."""
    counts: dict[str, int] = {}
    requests: set[str] = set()
    for event in read_events(path):
        counts[event["event"]] = counts.get(event["event"], 0) + 1
        if event.get("request_id"):
            requests.add(event["request_id"])
    return {"events": counts, "requests": len(requests)}


__all__ = ["REQUEST_EVENTS", "begin_request", "clear_request", "current_request_id",
           "emit", "events_for", "events_path", "new_request_id", "read_events",
           "summarize"]
