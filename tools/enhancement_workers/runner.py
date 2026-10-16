# -*- coding: utf-8 -*-
"""tools.enhancement_workers.runner - worker 侧的协议执行壳（计划 §F5）.

worker 脚本只做三件事：

1. 读 request JSON（核对 schema / worker 名）；
2. 调 handler（真实工作，如 PySceneDetect 检测）；
3. 写 response JSON（**无论成功失败都写**：结构化 error 也是证据，
   「import 失败就崩」或「静默无响应」都不允许）。

退出码只表达"worker 壳"的状态（见 :data:`protocol.WORKER_EXIT_*`）：
写出 ``ok`` / ``unavailable`` / ``error`` 响应都算"壳成功"（rc 分别 0/3/1），
只有**读不动请求或写不出响应**才让宿主按无响应处理。宿主另记 ``tool_exit_code``。
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from pathlib import Path
from typing import Callable

# --- 让脚本既能 `python -m enhancement_workers.xxx` 也能按绝对路径直接运行
_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from enhancement_workers.protocol import (  # noqa: E402
    REQUEST_SCHEMA,
    RESPONSE_SCHEMA,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_UNAVAILABLE,
    WORKER_EXIT_HANDLER_ERROR,
    WORKER_EXIT_INVALID_REQUEST,
    WORKER_EXIT_OK,
    WORKER_EXIT_UNAVAILABLE,
    WorkerError,
    canonical_digest,
    read_json_file,
    runtime_payload,
    write_json_file,
)


class WorkerUnavailable(Exception):
    """worker 能力不可用（缺环境/缺权重/缺包）——如实上报，不降级、不下载."""

    def __init__(self, code: str, message: str, **detail):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


def require_import(module_name: str, dist_name: str, *, hint: str = ""):
    """import 隔离环境里的第三方包；缺失 ⇒ ``unavailable``/``package_missing``.

    这条路径**只在 worker 进程内**执行：宿主解释器永远不会 import 这些包。
    """
    try:
        return __import__(module_name)
    except Exception as exc:  # ImportError 及其子类（缺依赖时常是别的异常）
        raise WorkerUnavailable(
            "package_missing",
            f"隔离环境未安装 {dist_name}（import {module_name} 失败: {exc}）" + (f"；{hint}" if hint else ""),
            module=module_name, dist=dist_name,
        ) from exc


def main(worker_name: str, handler: Callable[[dict], dict], argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"enhancement_workers.{worker_name}")
    parser.add_argument("--request", required=True, help="request JSON 路径")
    parser.add_argument("--response", required=True, help="response JSON 路径（由本进程写）")
    args = parser.parse_args(argv)

    response_path = Path(args.response)
    started = time.time()
    response = {
        "schema_version": RESPONSE_SCHEMA,
        "worker": worker_name,
        "request_id": None,
        "request_sha256": None,
        "status": STATUS_ERROR,
        "result": None,
        "error": None,
        "runtime": runtime_payload(),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "finished_at": None,
        "duration_sec": None,
    }

    def finish(rc: int) -> int:
        response["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        response["duration_sec"] = round(time.time() - started, 3)
        try:
            write_json_file(response_path, response)
        except Exception as exc:  # 写不出响应 ⇒ 宿主按无响应失败处理（这里如实说明）
            sys.stderr.write(f"[{worker_name}] 无法写出响应文件: {exc}\n")
            return WORKER_EXIT_INVALID_REQUEST
        return rc

    try:
        request = read_json_file(args.request)
    except WorkerError as exc:
        response["error"] = {**exc.payload(), "code": "worker_invalid_request"}
        return finish(WORKER_EXIT_INVALID_REQUEST)

    if request.get("schema_version") != REQUEST_SCHEMA:
        response["error"] = WorkerError(
            "worker_invalid_request",
            f"request schema 不符: {request.get('schema_version')!r} != {REQUEST_SCHEMA!r}",
        ).payload()
        return finish(WORKER_EXIT_INVALID_REQUEST)
    if request.get("worker") != worker_name:
        response["error"] = WorkerError(
            "worker_invalid_request",
            f"request 指定的 worker 不是本进程: {request.get('worker')!r} != {worker_name!r}",
        ).payload()
        return finish(WORKER_EXIT_INVALID_REQUEST)

    payload = request.get("payload")
    if not isinstance(payload, dict):
        response["error"] = WorkerError("worker_invalid_request",
                                        "request.payload 不是对象").payload()
        return finish(WORKER_EXIT_INVALID_REQUEST)

    response["request_id"] = request.get("request_id")
    response["request_sha256"] = canonical_digest(request)

    try:
        result = handler(payload)
        if not isinstance(result, dict):
            raise WorkerError("worker_bad_result", "handler 未返回对象")
    except WorkerUnavailable as exc:
        response["status"] = STATUS_UNAVAILABLE
        response["error"] = WorkerError(exc.code, exc.message, **exc.detail).payload()
        return finish(WORKER_EXIT_UNAVAILABLE)
    except WorkerError as exc:
        response["status"] = STATUS_ERROR
        response["error"] = exc.payload()
        return finish(WORKER_EXIT_HANDLER_ERROR)
    except Exception as exc:  # noqa: BLE001 - 结构化上报，不静默
        response["status"] = STATUS_ERROR
        response["error"] = WorkerError(
            "worker_exception", f"{type(exc).__name__}: {exc}",
            traceback="".join(traceback.format_exc())[-4000:]).payload()
        return finish(WORKER_EXIT_HANDLER_ERROR)

    response["status"] = STATUS_OK
    response["result"] = result
    return finish(WORKER_EXIT_OK)


__all__ = ["WorkerUnavailable", "main", "require_import"]
