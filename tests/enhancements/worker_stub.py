# -*- coding: utf-8 -*-
"""协议测试桩 worker（**不是**媒体 worker）：只验证 request→response 壳行为.

覆盖 §F5 要求的四类明确失败：退出（非零/无响应）/ 超时 / 坏 JSON / 错摘要，
外加"worker 不得继承模型凭据"的环境探针。所有分支都是**另一个真实进程**，
不是进程内 monkeypatch——测的就是跨进程协议本身。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_TOOLS = _HERE.parent.parent / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from enhancement_workers import protocol as wp  # noqa: E402
from enhancement_workers.protocol import (  # noqa: E402
    RESPONSE_SCHEMA,
    WorkerError,
    canonical_digest,
    read_json_file,
)
from enhancement_workers.runner import WorkerUnavailable, main  # noqa: E402

WORKER_NAME = "media_cuts"


def handler(payload: dict) -> dict:
    mode = payload.get("mode")
    if mode == "echo":
        offline_keys = set(wp.OFFLINE_ENV)   # 宿主自己设的离线开关不算"继承来的凭据"
        return {"echo": payload, "python": sys.version.split()[0],
                # 与宿主同一套凭据判据（协议单源），排除本协议自己设的离线键
                "credential_env_keys": sorted(
                    k for k in os.environ
                    if k not in offline_keys and wp._CREDENTIAL_RE.search(k)),
                "explicit_probe": {k: os.environ.get(k) for k in
                                   ("DASHSCOPE_API_KEY", "MY_SERVICE_TOKEN")},
                "offline_env": {k: os.environ.get(k) for k in
                                ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE",
                                 "PYTHONNOUSERSITE")},
                "pythonpath": os.environ.get("PYTHONPATH")}
    if mode == "unavailable":
        raise WorkerUnavailable("stub_unavailable", "桩：能力不可用（缺包/缺权重）",
                                detail_key="detail-value")
    if mode == "error":
        raise WorkerError("stub_error", "桩：处理失败")
    if mode == "boom":
        raise RuntimeError("桩：未预期异常")
    if mode == "hang":
        time.sleep(60)
        return {"never": True}
    return {"mode": mode}


def _special(mode: str, response_path: Path, request: dict) -> int | None:
    """不走 runner 的分支：坏 JSON / 无响应 / 错摘要 / 错身份."""
    if mode == "no_response":
        return 7
    if mode == "bad_json":
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_text("{ this is not json", encoding="utf-8")
        return 0
    if mode in ("tamper_digest", "wrong_worker"):
        payload = {
            "schema_version": RESPONSE_SCHEMA,
            "worker": ("alignment_worker" if mode == "wrong_worker" else WORKER_NAME),
            "request_id": request.get("request_id"),
            "request_sha256": ("0" * 64 if mode == "tamper_digest"
                               else canonical_digest(request)),
            "status": "ok",
            "result": {"memo": "桩"},
            "error": None,
            "runtime": {"python": sys.version.split()[0], "executable": sys.executable,
                        "platform": sys.platform, "packages": {}},
        }
        response_path.parent.mkdir(parents=True, exist_ok=True)
        response_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return 0
    return None


def main_stub(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    response_path = Path(argv[argv.index("--response") + 1])
    request = read_json_file(argv[argv.index("--request") + 1])
    mode = str((request.get("payload") or {}).get("mode"))
    special = _special(mode, response_path, request)
    if special is not None:
        return special
    return main(WORKER_NAME, handler, argv)


if __name__ == "__main__":
    sys.exit(main_stub())
