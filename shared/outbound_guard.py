# -*- coding: utf-8 -*-
"""统一出站发送守卫（M2.1）：**双层账本 + 三指标 + 连接池自愈**.

## 为什么要有它

此前文本、钩子、视觉 QC、生图、生视频、TTS 各自维护一套计数/限流/重试：

* `resume` / 固定输入运行器把 `requests.post` 换成一个只数**尝试**的闭包，
  钩子的「逻辑次数」由 `main._hook_max_sends()` 在另一处管——两层账本分居两个
  模块，`HOOK_MAX_SENDS=1` 管住逻辑调用却漏放 4 次底层重试（2026-11-16 实测）。
* 报告里叫 `transmitted`：既像"已发出字节"又像"已收到响应"，两种读法都成立，
  于是同一份 run_log 被解读出「发送 2 次」与「尝试 6 次」两个结论。

本模块把两层放在**同一个发送边界**上：

| 层 | 计数 | 上限 | 阻断时机 |
|---|---|---|---|
| 逻辑调用 | `logical_calls[phase]` | 分类 + 全局 | `begin()` 时（还没构造请求） |
| 网络尝试 | `http_attempts[phase]` | 分类 + 全局 | `__call__` 时（**发出字节之前**） |

三指标固定为 `logical_calls` / `http_attempts` / `responses_received`，
`transmitted` 不再出现。

## 与重试的关系（不叠加）

重试的**判定**仍归 `_text_gen.retry_policy`（单一来源，见该模块「上层不得叠加」）。
本模块只做两件事：把每次尝试记账；遇到瞬态 SSL/连接错误时把连接池标记为脏，
**下一次**发送前重建，避免继续复用已半开的代理隧道。所以本模块不自己 sleep、
不自己重发。

## 未安装时零行为变化

`install()` 之前 `current()` 返回 `None`，`begin_for_schema()` 直接返回，
不建会话、不计数——测试与其它阶段不受影响。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

#: 阶段名（与计划的分类口径一致；新增通道照此扩展）
PHASES = ("verify", "generate", "hook", "audit", "repair",
          "image_gen", "vision_qc", "video_gen", "tts", "other")

#: 请求 schema 名 → 阶段。未列出的归 `other`（不猜）
_SCHEMA_PHASE = {
    "storyboard_script": "generate",
    "hook_shot": "hook",
    "script_audit": "audit",
    "audit_repair_targets": "repair",
    "repair_shot_replacements": "repair",
    "plan_verify": "verify",
    "outline": "verify",
}


class SendBlocked(BaseException):
    """发送上限阻断（**不是** RuntimeError）.

    候选/修复循环里有大量 `except RuntimeError`（把它们当作"生成失败可重试"），
    预算耗尽必须直接终止并保留检查点，不能被那些分支吞掉。
    """


def phase_of_schema(schema_name) -> str:
    """请求 schema 名 → 阶段名（未知归 other，不靠顺序或 URL 猜）."""
    return _SCHEMA_PHASE.get(str(schema_name or "").strip(), "other")


def _request_messages(kw) -> tuple[str, str]:
    """从 provider POST 参数里取出 system/user 文本（`json=` 与 `data=` 两种形态）."""
    raw = kw.get("json")
    if isinstance(raw, dict):
        msgs = raw.get("messages") or []
    else:
        data = kw.get("data")
        if isinstance(data, (bytes, bytearray)):
            data = data.decode("utf-8", "replace")
        try:
            obj = json.loads(data) if isinstance(data, str) else {}
        except (TypeError, ValueError):
            obj = {}
        msgs = obj.get("messages") if isinstance(obj, dict) else []
    sys_parts, user_parts = [], []
    for m in msgs or []:
        if not isinstance(m, dict):
            continue
        content = m.get("content")
        if isinstance(content, list):
            content = " ".join(str(c.get("text") or "") if isinstance(c, dict)
                               else str(c) for c in content)
        content = str(content or "")
        if m.get("role") == "system":
            sys_parts.append(content)
        elif m.get("role") == "user":
            user_parts.append(content)
    return "\n".join(sys_parts), "\n".join(user_parts)


def is_hook_request(kw) -> bool:
    """钩子请求分类：系统/用户提示词里的稳定标记（历史入口，保留给既有调用方）."""
    sys_text, user_text = _request_messages(kw)
    return ("短视频脚本大师" in sys_text) or ("钩子分镜" in user_text)


class OutboundGuard:
    """发送边界守卫：两层上限 + 三指标 + 连接池自愈.

    Args:
        transport: 实际发送函数（默认用内部 `requests.Session().post`）。
            测试注入桩，生产留空即获得连接池复用。
        total_logical / total_attempts: 全局上限（None/0 = 不限）。
        phase_logical / phase_attempts: 分类上限 dict（缺项 = 该阶段不限）。
        log_dir: 阻断留痕目录（每次阻断写 `blocked_*.json`）。
    """

    def __init__(self, *, transport=None, total_logical=None, total_attempts=None,
                 phase_logical=None, phase_attempts=None, log_dir=None,
                 session=None):
        self._transport = transport
        self._session = session
        self.total_logical = None if total_logical is None else int(total_logical)
        self.total_attempts = None if total_attempts is None else int(total_attempts)
        self.phase_logical = dict(phase_logical or {})
        self.phase_attempts = dict(phase_attempts or {})
        self.log_dir = Path(log_dir) if log_dir else None
        self.logical_calls: dict = {}
        self.http_attempts: dict = {}
        self.responses_received: dict = {}
        self.blocked = 0
        self.blocked_by_phase: dict = {}
        self.calls: list = []
        self._session_dirty = False
        self._lock = threading.Lock()

    # ---- 内部：会话与阻断 ------------------------------------------------
    def _get_session(self):
        import requests
        if self._session is None or self._session_dirty:
            try:
                if self._session is not None:
                    self._session.close()
            except Exception:
                pass
            self._session = requests.Session()
            self._session_dirty = False
        return self._session

    def _mark_dirty(self) -> None:
        """瞬态传输错误 → 连接池已脏，下次发送前重建（不在此处重试）."""
        self._session_dirty = True

    def _block(self, reason: str, phase: str) -> None:
        self.blocked += 1
        self.blocked_by_phase[phase] = int(self.blocked_by_phase.get(phase) or 0) + 1
        if self.log_dir is not None:
            try:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                (self.log_dir / ("blocked_%02d.json" % self.blocked)).write_text(
                    json.dumps({"reason": reason, "phase": phase},
                               ensure_ascii=False, indent=1), encoding="utf-8")
            except Exception:
                pass
        raise SendBlocked(reason)

    # ---- 第 1 层：逻辑调用 ----------------------------------------------
    def begin(self, phase: str = "other") -> str:
        """登记一次**逻辑调用**（一个请求意图，重试不重复登记）."""
        phase = str(phase or "other")
        with self._lock:
            used = int(self.logical_calls.get(phase) or 0)
            cap = self.phase_logical.get(phase)
            if cap is not None and used >= int(cap):
                self._block("逻辑调用上限阻断：%s 已用 %d/%s" % (phase, used, cap), phase)
            total_used = sum(int(v or 0) for v in self.logical_calls.values())
            if self.total_logical and total_used >= self.total_logical:
                self._block("逻辑调用总量上限阻断：%d/%d"
                            % (total_used, self.total_logical), phase)
            self.logical_calls[phase] = used + 1
        return phase

    # ---- 第 2 层：网络尝试 ----------------------------------------------
    def __call__(self, url, **kw):
        phase = str(kw.pop("phase", "") or "other")
        if phase == "other" and is_hook_request(kw):
            phase = "hook"
        with self._lock:
            used = int(self.http_attempts.get(phase) or 0)
            cap = self.phase_attempts.get(phase)
            if cap is not None and used >= int(cap):
                self._block("网络尝试上限阻断：%s 已用 %d/%s" % (phase, used, cap), phase)
            total_used = sum(int(v or 0) for v in self.http_attempts.values())
            if self.total_attempts and total_used >= self.total_attempts:
                self._block("网络尝试总量上限阻断：%d/%d"
                            % (total_used, self.total_attempts), phase)
            self.http_attempts[phase] = used + 1
            n = total_used + 1
        t0 = time.monotonic()
        try:
            if self._transport is not None:
                resp = self._transport(url, **kw)
            else:
                resp = self._get_session().post(url, **kw)
        except BaseException as exc:
            if _is_transient(exc):
                self._mark_dirty()
            self.calls.append({"n": n, "kind": phase, "phase": phase,
                               "response_received": False,
                               "error": "%s: %s" % (type(exc).__name__, exc),
                               "elapsed_sec": round(time.monotonic() - t0, 1)})
            raise
        with self._lock:
            self.responses_received[phase] = int(self.responses_received.get(phase) or 0) + 1
        payload = kw.get("json") if isinstance(kw.get("json"), dict) else {}
        self.calls.append({"n": n, "kind": phase, "phase": phase,
                           "response_received": True,
                           "model": payload.get("model"),
                           "max": payload.get("max_completion_tokens"),
                           "status": getattr(resp, "status_code", None),
                           "elapsed_sec": round(time.monotonic() - t0, 1)})
        return resp

    # ---- 三指标 ----------------------------------------------------------
    def snapshot(self) -> dict:
        """标准三指标（`transmitted` 不再出现）."""
        return {
            "logical_calls": dict(self.logical_calls),
            "logical_total": sum(int(v or 0) for v in self.logical_calls.values()),
            "http_attempts": dict(self.http_attempts),
            "http_attempts_total": sum(int(v or 0) for v in self.http_attempts.values()),
            "responses_received": dict(self.responses_received),
            "responses_total": sum(int(v or 0) for v in self.responses_received.values()),
            "blocked": self.blocked,
        }

    def remaining_logical(self, phase: str | None = None):
        """剩余逻辑额度（None = 不限）。供「成对额度预检」在发起前判断能否闭环."""
        if phase is not None:
            cap = self.phase_logical.get(str(phase))
            if cap is None:
                return None if not self.total_logical else max(
                    0, self.total_logical - sum(int(v or 0) for v in self.logical_calls.values()))
            return max(0, int(cap) - int(self.logical_calls.get(str(phase)) or 0))
        if not self.total_logical:
            return None
        return max(0, self.total_logical - sum(int(v or 0) for v in self.logical_calls.values()))

    def remaining_attempts(self, phase: str | None = None):
        """剩余网络尝试额度（None = 不限）.

        给了 `phase` 时取「该阶段上限余额」与「全局余额」的**较小值**：两层都在
        发前判定，任一层先耗尽就会拦，所以余额必须按较紧的那层算。
        """
        _total = None
        if self.total_attempts:
            _total = max(0, self.total_attempts
                         - sum(int(v or 0) for v in self.http_attempts.values()))
        if phase is None:
            return _total
        cap = self.phase_attempts.get(str(phase))
        if cap is None:
            return _total
        _phase = max(0, int(cap) - int(self.http_attempts.get(str(phase)) or 0))
        return _phase if _total is None else min(_phase, _total)


def _is_transient(exc: BaseException) -> bool:
    """瞬态传输错误（SSL 握手/连接重置/超时）→ 连接池该换一条."""
    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if any(k in name for k in ("ssl", "connection", "connect", "timeout")):
        return True
    return any(k in text for k in ("ssl", "connection reset", "connection aborted",
                                   "remote end closed", "max retries exceeded"))


# ---------------------------------------------------------------------------
# 进程内安装点（未安装 = 零行为变化）
# ---------------------------------------------------------------------------
_CURRENT: "OutboundGuard | None" = None


def install(guard: "OutboundGuard | None") -> "OutboundGuard | None":
    """安装/卸载当前守卫，返回被替换的旧值（便于测试还原）."""
    global _CURRENT
    old = _CURRENT
    _CURRENT = guard
    return old


def current() -> "OutboundGuard | None":
    return _CURRENT


def begin_for_schema(schema_name) -> str:
    """按请求 schema 登记一次逻辑调用；未安装守卫时**直接返回**（零行为变化）."""
    guard = _CURRENT
    if guard is None:
        return ""
    return guard.begin(phase_of_schema(schema_name))


def remaining_logical(phase: str | None = None):
    guard = _CURRENT
    return None if guard is None else guard.remaining_logical(phase)


def remaining_attempts(phase: str | None = None):
    """剩余网络尝试额度（模块级；未安装守卫时 None = 不限）."""
    guard = _CURRENT
    return None if guard is None else guard.remaining_attempts(phase)


def snapshot() -> dict:
    guard = _CURRENT
    return {} if guard is None else guard.snapshot()


__all__ = ["PHASES", "OutboundGuard", "SendBlocked", "begin_for_schema", "current",
           "install", "is_hook_request", "phase_of_schema", "remaining_logical",
           "snapshot"]
