# -*- coding: utf-8 -*-
"""TLS preflight for the U6 audit endpoint (no HTTP request, no model call)."""
from __future__ import annotations

import datetime as dt
import json
import socket
import ssl
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOST = "opencode.ai"
PORT = 443
ctx = ssl.create_default_context()
with socket.create_connection((HOST, PORT), timeout=15) as sock:
    with ctx.wrap_socket(sock, server_hostname=HOST) as tls:
        cert = tls.getpeercert()
        version = tls.version()
        cipher = tls.cipher()

now = dt.datetime.now(dt.timezone.utc)
row = {
    "host": HOST,
    "port": PORT,
    "checked_at_utc": now.isoformat(timespec="seconds"),
    "tls_version": version,
    "cipher": cipher[0] if cipher else "",
    "not_before": cert.get("notBefore"),
    "not_after": cert.get("notAfter"),
    "valid_now": True,
    "previous_failure": {
        "at": "2026-11-10T23:16:16+08:00",
        "error": "SSLCertVerificationError: certificate is not yet valid",
        "reason": "证书 notBefore=2026-11-11 04:38:08 UTC，失败时本机时间早于该时刻",
    },
    "note": ("只做 TLS 握手与证书读取，不发送 HTTP 请求、不调用模型。"
             "当前证书已进入有效期；新授权审计前仍建议复跑本预检。"),
}
out = ROOT / ".pytest_tmp" / "u1_u5_closeout_20261110" / "u6_package" / "tls_preflight.json"
out.write_text(json.dumps(row, ensure_ascii=False, indent=1), encoding="utf-8")
print(json.dumps(row, ensure_ascii=False, indent=1))
