# -*- coding: utf-8 -*-
"""发送边界预算（Step01 固定输入运行器 / 冻结恢复共用）.

两层计数：
* 总请求尝试 `attempts` / 收到 HTTP 响应 `responses_received`；
* 钩子分类尝试 `hook_attempts` / 钩子响应 `hook_responses`。

异常（SSL/连接/超时）不能证明请求未发出，所以只把"拿到 HTTP 响应"记为
`responses_received`；预算一律按**请求尝试**扣减。
"""
from __future__ import annotations

import json
from pathlib import Path
import time


class SendBudgetExceeded(BaseException):
    """发送上限阻断（**不是** RuntimeError：候选循环的 `except RuntimeError` 会把它
    当成"生成失败可重试"吞掉，预算耗尽必须直接终止并保留检查点）."""


def _request_messages(kw) -> tuple[str, str]:
    """从 provider POST 参数里取出 system/user 文本（json= 或 data= 两种形态）."""
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
    """钩子请求分类：系统提示词 / 用户提示词的稳定标记（不靠 URL 或顺序猜）."""
    sys_text, user_text = _request_messages(kw)
    return ("短视频脚本大师" in sys_text) or ("钩子分镜" in user_text)


class CallCounter:
    """Count every provider POST at the **send boundary**; block after caps."""

    def __init__(self, transport, *, cap, log_dir, hook_cap=None):
        self.transport = transport
        self.cap = int(cap)
        self.hook_cap = None if hook_cap is None else int(hook_cap)
        self.log_dir = Path(log_dir)
        self.attempts = 0
        self.responses_received = 0
        self.hook_attempts = 0
        self.hook_responses = 0
        self.blocked = 0
        self.hook_blocked = 0
        self.calls = []

    def _block(self, reason: str, name: str):
        self.log_dir.mkdir(parents=True, exist_ok=True)
        (self.log_dir / name).write_text(
            json.dumps({"reason": reason}, ensure_ascii=False, indent=1),
            encoding="utf-8")
        raise SendBudgetExceeded(reason)

    def __call__(self, url, **kw):
        hook = is_hook_request(kw)
        if self.attempts >= self.cap:
            self.blocked += 1
            self._block("调用上限阻断：cap %d" % self.cap,
                        "blocked_%02d.json" % self.blocked)
        if hook and self.hook_cap is not None and self.hook_attempts >= self.hook_cap:
            self.hook_blocked += 1
            self._block("钩子请求尝试上限阻断：hook_cap %d" % self.hook_cap,
                        "blocked_hook_%02d.json" % self.hook_blocked)
        self.attempts += 1
        if hook:
            self.hook_attempts += 1
        t0 = time.monotonic()
        try:
            resp = self.transport(url, **kw)
        except BaseException as exc:
            self.calls.append({"n": self.attempts,
                               "kind": "hook" if hook else "other",
                               "response_received": False,
                               "error": "%s: %s" % (type(exc).__name__, exc),
                               "elapsed_sec": round(time.monotonic() - t0, 1)})
            raise
        self.responses_received += 1
        if hook:
            self.hook_responses += 1
        payload = kw.get("json") if isinstance(kw.get("json"), dict) else {}
        self.calls.append({"n": self.attempts,
                           "kind": "hook" if hook else "other",
                           "response_received": True,
                           "model": payload.get("model"),
                           "max": payload.get("max_completion_tokens"),
                           "status": getattr(resp, "status_code", None),
                           "elapsed_sec": round(time.monotonic() - t0, 1)})
        return resp
