"""发送边界的 payload 采集（A/B 对照取证用；**默认惰性、零行为变化**）。

§7.3 要求记录"实际路径、格式、思考、输出上限、超时和重试"——本模块把**真正发出去**的
payload 落盘，供 §7.9 的可复现清单与请求一致性证据使用。

- 只有设置 `DSH_CAPTURE_PAYLOAD=<目录>` 时才写；不设即完全无副作用。
- **不写鉴权头/密钥**：`headers` / `authorization` / `api_key` 一律剔除。
- 采集失败**不静默**：写 `<目录>/_capture_errors.txt`；连目录都写不了时由调用方在报告里
  记为"证据不完整"（不能只用提示长度证明请求一致）。
"""
from __future__ import annotations

import json
import os
import pathlib
import threading

_LOCK = threading.Lock()
_SENSITIVE = ("headers", "authorization", "api_key", "apikey", "token")


def _redact(payload: dict) -> dict:
    return {k: v for k, v in (payload or {}).items()
            if str(k).lower() not in _SENSITIVE}


def maybe_capture(provider: str, payload: dict, *, where: str = "") -> str | None:
    """把发送 payload 落到 `DSH_CAPTURE_PAYLOAD` 目录；返回写入路径或 None。"""
    out = os.getenv("DSH_CAPTURE_PAYLOAD")
    if not out:
        return None
    d = pathlib.Path(out)
    try:
        d.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            seq = len(list(d.glob("payload_*.json")))
            path = d / ("payload_%03d.json" % seq)
            path.write_text(
                json.dumps({"provider": provider, "where": where,
                            "payload": _redact(payload)},
                           ensure_ascii=False, indent=1),
                encoding="utf-8")
            return str(path)
    except Exception as exc:                                    # noqa: BLE001
        try:
            with _LOCK:
                with (d / "_capture_errors.txt").open("a", encoding="utf-8") as fh:
                    fh.write("%s: %s: %s\n" % (provider, type(exc).__name__, exc))
        except Exception:                                       # noqa: BLE001
            pass
        return None
