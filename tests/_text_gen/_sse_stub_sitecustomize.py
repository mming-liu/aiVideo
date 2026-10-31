import json
import os
import time

_MODE = os.environ.get("STEP01_STUB_SSE_MODE") or "sse_ok"
_LOG = os.environ.get("STEP01_STUB_HTTP_LOG") or ""
_TEXT = '{"issues": []}'


def _record(params):
    if not _LOG:
        return
    row = {"stream": params.get("stream"), "model": params.get("model"),
           "keys": sorted(k for k in params)}
    with open(_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _chunk(delta=None, finish=None, usage=None):
    obj = {"id": "stub", "object": "chat.completion.chunk",
           "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
    if usage:
        obj["usage"] = usage
    return ("data: " + json.dumps(obj, ensure_ascii=False)).encode() + b"\n\n"


def _lines(mode):
    parts = [_TEXT[i:i + 5] for i in range(0, len(_TEXT), 5)]
    if mode == "sse_ok":
        out = [b": keepalive\n\n"]
        out += [_chunk({"content": p}) for p in parts]
        out += [_chunk({"content": ""}, finish="stop")]
        out += [_chunk(usage={"prompt_tokens": 100, "completion_tokens": 10,
                              "total_tokens": 110})]
        out += [b"data: [DONE]\n\n"]
        return out
    if mode == "sse_truncated":
        return [_chunk({"content": p}) for p in parts]
    if mode == "sse_error_event":
        return [_chunk({"content": parts[0]}),
                b'data: {"error": {"message": "stub stream error"}}\n\n']
    if mode == "sse_mid_timeout":
        return [_chunk({"content": parts[0]}), "__SLEEP__"]
    if mode == "sse_head_only":
        return ["__SLEEP__"]
    if mode == "sse_heartbeat_forever":
        return ["__TICK__"] * 300
    if mode == "sse_bad_shape":
        return [_chunk({"content": parts[0]}),
                b'data: {"choices": [{"index": 0, "delta": {"content": "a"}}, '
                b'{"index": 1, "delta": {"content": "b"}}]}\n\n',
                _chunk({}, finish="stop"), b"data: [DONE]\n\n"]
    raise RuntimeError("unknown stub mode: %s" % mode)


class _Resp:
    status_code = 200
    headers = {"x-request-id": "stub-sse"}

    def __init__(self, lines, timeout=None):
        self._lines = lines
        self.closed = False
        self._timeout = timeout

    def _stall(self):
        """真正制造停滞：等到请求超时再抛 socket 级读超时（不是直接跳过）."""
        wait = float(self._timeout or 5.0)
        time.sleep(max(0.05, wait))
        raise _ReadTimeout("stub read timeout after %ss" % wait)

    def iter_lines(self):
        for item in self._lines:
            if item == "__SLEEP__":
                self._stall()
            yield item

    def iter_content(self, chunk_size=65536):
        for item in self._lines:
            if item == "__SLEEP__":
                self._stall()          # 生产读取器走 iter_content，必须在这里停住
            if item == "__TICK__":
                time.sleep(0.1)        # 持续心跳：有数据但永远没有终态
                yield b": ping\n\n"
                continue
            if isinstance(item, (bytes, bytearray)):
                yield bytes(item)

    def close(self):
        self.closed = True


import requests as _rq  # noqa: E402

_ReadTimeout = _rq.exceptions.ReadTimeout


def _post(url, **kwargs):
    _record(kwargs.get("json") or {})
    return _Resp(_lines(_MODE), timeout=kwargs.get("timeout"))


_rq.post = _post

# 登记根隔离：与 deepseek 夹具同一契约（生产解析不读环境变量，测试在导入前换事实源）
_TEST_AUTH_ROOT = os.environ.get("STEP01_TEST_AUTH_ROOT")
if _TEST_AUTH_ROOT:
    from pathlib import Path as _Path

    import shared.paths as _shared_paths

    _shared_paths.AUDIT_AUTHORIZATION_ROOT = _Path(_TEST_AUTH_ROOT)
