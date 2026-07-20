"""shared.error_fmt - 错误信息打印格式化（供各步骤统一使用）

规则：
- 优先提取 JSON 错误体中的 message 字段（API 错误最有用的部分），并保留前缀
- 压掉换行保持单行打印
- 默认上限 500 字符，仅防止超大 HTML 错误页刷屏，正常 API 错误体可完整显示
- ffmpeg 类错误（有效信息在末尾）用 fmt_err_tail 取尾部
"""
import re


def fmt_err(err, limit: int = 500) -> str:
    """格式化错误信息：提取 JSON message 字段，否则截断到 limit 字符."""
    err = " ".join(str(err).split())  # 压掉换行，保持单行打印
    # 提取 {"error":{"code":429,"message":"..."}} 中的 message 内容
    m = re.search(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)"', err)
    if m and m.group(1).strip():
        msg = m.group(1).strip()
        # 保留前缀（如 "Agnes 查询失败 [429]"），拼上完整 message
        prefix = err.split("{", 1)[0].strip().rstrip(":").strip()
        out = f"{prefix}: {msg}" if prefix else msg
        return out[:limit]
    return err[:limit]


def fmt_err_tail(err, limit: int = 500) -> str:
    """取错误信息尾部（ffmpeg 等工具的 stderr 有效内容在末尾）."""
    err = str(err or "").strip()
    return err[-limit:]


def parse_retry_after(err) -> float | None:
    """从 429 限流错误信息中解析服务端返回的剩余等待秒数.

    支持常见格式：
    - "Please retry after 32 seconds" / "retry after 32s"
    - "Please try again in 20s" / "try again in 500ms"
    - '"retryDelay": "32s"'（Google 风格错误体）
    - "Retry-After: 32"（HTTP 头部文本）
    - "32秒后重试" / "请等待32秒"
    解析不到返回 None（调用方回退到默认退避策略）。
    结果限制在 0~600s，防止误解析导致超长等待。
    """
    text = " ".join(str(err).split())
    low = text.lower()

    def _clamp(v: float) -> float:
        return max(0.0, min(v, 600.0))

    # 毫秒格式优先（避免 "in 500ms" 被秒模式误配）
    m = re.search(r"(?:in|after)\s+(\d+(?:\.\d+)?)\s*ms\b", low)
    if m:
        return _clamp(float(m.group(1)) / 1000.0)

    for pat in (
        r'"retrydelay"\s*:\s*"(\d+(?:\.\d+)?)s"',
        r"retry\s+after\s+(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|s)\b",
        r"try\s+again\s+in\s+(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|s)\b",
        r"wait\s+(?:for\s+)?(\d+(?:\.\d+)?)\s*(?:seconds?|secs?|s)\b",
        r"retry-?after\s*[:=]\s*(\d+(?:\.\d+)?)",
    ):
        m = re.search(pat, low)
        if m:
            return _clamp(float(m.group(1)))

    m = re.search(r"(\d+(?:\.\d+)?)\s*秒(?:后|之后)|等待\s*(\d+(?:\.\d+)?)\s*秒", text)
    if m:
        return _clamp(float(m.group(1) or m.group(2)))
    return None
