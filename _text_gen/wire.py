# -*- coding: utf-8 -*-
"""**线上字节**的唯一编码处（S02，2026-11-07）.

为什么要有这一层：

  · requests.post(url, json=payload) 内部走 json.dumps(payload) 的默认参数
    （ensure_ascii=True），把每个中文字符写成 6 字节的 \\uXXXX 转义。
    实测同一条修复请求：UTF-8 97,335 B → 转义后 168,662 B（1.73×）。
  · 真实发出去的字节必须**可复核**：日志/台账要知道"这次到底发了多大"，而不是
    事后按字符数猜。所以编码、指纹、计量都从这一个函数取。

⚠️ 这里**只**解决"发出去的字节与解码后的 JSON 相等"，**不**声称因此能改变限流
结果——429 的准入语义由上游决定，实测记录见工作包 S02 的验收项。

不变量：json.loads(encode_json_body(x)) == x（浮点 NaN/Inf 直接拒绝，不静默写
NaN 这种非法 JSON）。
"""
from __future__ import annotations

import hashlib
import json


class WireEncodingError(ValueError):
    """请求体无法编码成合法 JSON（NaN/Inf、不可序列化对象）。"""


def encode_json_body(payload: dict) -> bytes:
    """把 payload 编成**实际发送**的 UTF-8 JSON 字节（确定性：键序按插入顺序）."""
    try:
        text = json.dumps(payload, ensure_ascii=False, allow_nan=False,
                          separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise WireEncodingError("请求体无法编码为 JSON: %s" % exc) from exc
    return text.encode("utf-8")


def body_digest(body: bytes) -> str:
    """实际传输字节的摘要（脱敏：只留哈希，正文不进日志/台账）."""
    return hashlib.sha256(body or b"").hexdigest()


def body_stats(body: bytes) -> dict:
    """计量三元组里"实际传输"那一项：字节数 + 摘要."""
    _b = body or b""
    return {"request_bytes": len(_b), "request_sha256": body_digest(_b)}


__all__ = ["WireEncodingError", "body_digest", "body_stats", "encode_json_body"]
