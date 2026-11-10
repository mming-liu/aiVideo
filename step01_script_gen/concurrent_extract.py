"""step01_script_gen.concurrent_extract - 并发预提取 helper.

v6.43（08-25）优化：3 个 LLM 预提取（角色/场景物体/大纲）无依赖关系，
原本按顺序执行 = 3 × LLM 延迟（~16s 总耗时）；改用并发后 = max(3) ≈ 5s。

R4-2：旧实现用 `ThreadPoolExecutor` + 无 timeout 的 `as_completed`。任一 worker
卡在导入锁/网络/回调时，整个 pytest/生产进程会永久等待；`with` 退出还会
`shutdown(wait=True)` 再等一次。现在改成 **daemon 线程 + 有界 join**：
- 每个任务一个 daemon 线程，超时后主线程返回，不阻塞解释器退出；
- `outline` 超时返回结构化 `worker_timeout`（不被 None 吞掉）；
- 未给 deadline 时仍有 300s 上限，避免无限等待。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

VERSION = "concurrent_extract.v2"
DEFAULT_WAIT_TIMEOUT_SEC = 300.0
OUTLINE_TIMEOUT_GRACE_SEC = 10.0


def _outline_timeout_result() -> dict:
    from step01_script_gen.outline import _outline_result_skeleton
    _res = _outline_result_skeleton()
    _res["failure"] = {
        "code": "worker_timeout",
        "detail": "大纲预提取超过等待上限（worker 未在截止时刻内返回）",
        "source": "initial",
    }
    return _res


def _run_tasks(tasks, *, timeout_sec, log) -> dict:
    """并发跑 `[(key, fn), ...]`；超时返回部分结果，不等待卡住的 daemon 线程."""
    results: dict[str, Any] = {key: None for key, _fn in tasks}
    errors: dict[str, BaseException] = {}
    threads: dict[str, threading.Thread] = {}
    lock = threading.Lock()

    def _runner(key, fn):
        try:
            value = fn()
        except BaseException as exc:  # noqa: BLE001 - 失败要保真到调用方
            with lock:
                errors[key] = exc
            return
        with lock:
            results[key] = value

    for key, fn in tasks:
        th = threading.Thread(target=_runner, args=(key, fn),
                              name="pre_extract_%s" % key, daemon=True)
        th.start()
        threads[key] = th

    deadline = (time.monotonic() + max(0.0, float(timeout_sec))
                if timeout_sec is not None else None)
    for key, th in threads.items():
        remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
        th.join(remaining)

    for key, th in threads.items():
        if th.is_alive():
            log("[WARN] 预提取 %s 超时（%.1fs），不等待卡住的 worker" % (
                key, float(timeout_sec) if timeout_sec is not None else -1.0))
            if key == "outline":
                results[key] = _outline_timeout_result()
            continue
        with lock:
            exc = errors.get(key)
        if exc is not None:
            log("[WARN] 预提取 %s 失败: %s" % (key, exc))
            if key == "outline":
                from step01_script_gen.outline import _outline_result_skeleton
                _res = _outline_result_skeleton()
                _res["failure"] = {
                    "code": "call_failed",
                    "detail": "%s: %s" % (type(exc).__name__, exc),
                    "source": "initial",
                }
                results[key] = _res
            else:
                results[key] = None
    return results


def extract_all_concurrent(
    novel_text: str,
    character_bank: Any,
    scene_objects_bank: Any,
    *,
    enable_characters: bool = True,
    enable_scene_objects: bool = True,
    enable_outline: bool = True,
    model: str | None = None,
    outline_cache_path: Path | None = None,
    outline_deadline_monotonic: float | None = None,
    log_func=None,
    wait_timeout_sec: float | None = None,
) -> dict[str, Any]:
    """并发跑 3 个 LLM 预提取（角色/场景物体/大纲），返回 dict 各项.

    超时上限：
    - 显式 `wait_timeout_sec` 优先；
    - 否则若有 `outline_deadline_monotonic`，用「剩余时间 + 10s 宽限」；
    - 否则用 `DEFAULT_WAIT_TIMEOUT_SEC`。
    """
    _log = log_func or (lambda msg: print(msg))

    def _do_characters() -> list[dict] | None:
        from step01_script_gen.character_extractor import extract_characters
        return extract_characters(novel_text, character_bank)

    def _do_scene_objects() -> list[dict] | None:
        from step01_script_gen.object_extractor import extract_scene_objects
        return extract_scene_objects(novel_text, scene_objects_bank)

    def _do_outline() -> dict:
        """**结构化**大纲结果（工作包 §4 D）：生产链路消费它，不再拿裸计划."""
        from step01_script_gen.outline import (generate_outline_result,
                                               _outline_result_skeleton)
        try:
            return generate_outline_result(
                novel_text, model=model, cache_path=outline_cache_path,
                deadline_monotonic=outline_deadline_monotonic,
                run_deadline_at=outline_deadline_monotonic)
        except Exception as exc:                       # noqa: BLE001
            _log("[ERROR] 大纲预提取异常: %s: %s" % (type(exc).__name__, exc))
            _res = _outline_result_skeleton()
            _res["failure"] = {"code": "call_failed",
                               "detail": "%s: %s" % (type(exc).__name__, exc),
                               "source": "initial"}
            return _res

    if wait_timeout_sec is None:
        if outline_deadline_monotonic is not None:
            wait_timeout_sec = max(
                0.0,
                float(outline_deadline_monotonic) - time.monotonic(),
            ) + OUTLINE_TIMEOUT_GRACE_SEC
        else:
            wait_timeout_sec = DEFAULT_WAIT_TIMEOUT_SEC

    tasks = []
    if enable_characters:
        tasks.append(("characters", _do_characters))
    if enable_scene_objects:
        tasks.append(("scene_objects", _do_scene_objects))
    if enable_outline:
        tasks.append(("outline", _do_outline))
    return _run_tasks(tasks, timeout_sec=float(wait_timeout_sec), log=_log)


def extract_assets_concurrent(
    script: dict,
    scene_assets_bank: Any,
    prop_assets_bank: Any,
    *,
    enable_scene_assets: bool = True,
    enable_props: bool = True,
    log_func=None,
    wait_timeout_sec: float | None = None,
) -> dict[str, Any]:
    """并发跑 2 个 LLM 资产提取（场景/道具）；同样有界等待."""
    _log = log_func or (lambda msg: print(msg))

    def _do_scene_assets() -> list[dict] | None:
        from step01_script_gen.asset_extractor import extract_scene_assets
        return extract_scene_assets(script, scene_assets_bank)

    def _do_props() -> list[dict] | None:
        from step01_script_gen.asset_extractor import extract_props
        return extract_props(script, prop_assets_bank)

    tasks = []
    if enable_scene_assets:
        tasks.append(("scene_assets", _do_scene_assets))
    if enable_props:
        tasks.append(("props", _do_props))
    return _run_tasks(tasks, timeout_sec=float(
        wait_timeout_sec if wait_timeout_sec is not None else DEFAULT_WAIT_TIMEOUT_SEC),
        log=_log)


__all__ = ["VERSION", "DEFAULT_WAIT_TIMEOUT_SEC", "extract_all_concurrent",
           "extract_assets_concurrent"]
