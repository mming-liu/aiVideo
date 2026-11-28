# -*- coding: utf-8 -*-
"""S07: `step02_video_gen.submit_poll` 的 failures 职责（2026-11-28 拆分）.

父模块保留提交/轮询主编排与运行状态；本模块在调用点读取父模块当前值，
导入期不回导父模块，保证父模块上的 monkeypatch 仍然生效。
"""
from pathlib import Path
import shutil
from _video_gen.models import (
    SUBMIT_RATE_LIMITED,
    SUBMIT_REJECTED,
    SUBMIT_UNKNOWN,
)

_PARENT = "step02_video_gen.submit_poll"


def _pv(name):
    import importlib
    return getattr(importlib.import_module(_PARENT), name)


def _pc(name, *args, **kwargs):
    return _pv(name)(*args, **kwargs)


def _classify_poll_failure(err: str) -> tuple[str, str]:
    """把轮询终局失败分类成 (attempt 状态, remote_status)。

    远端明确失败 ⇒ provider_failed（模型端没产出，重抽是**新费用**）；
    下载失败 ⇒ io_failed + remote_status=succeeded（远端其实成功了，只是没取回）；
    其余未知 ⇒ io_failed + remote_status=unverified（保持原行为，不猜）。
    """
    from shared import video_attempts as _va

    text = str(err or "")
    if any(marker in text for marker in _PROVIDER_FAILURE_MARKERS):
        return _va.STATE_PROVIDER_FAILED, "failed"
    if "下载失败" in text:
        return _va.STATE_IO_FAILED, "succeeded"
    return _va.STATE_IO_FAILED, "unverified"

def repair_missing_clip(shot_dir: Path, video_path: Path, clip_path: Path) -> bool:
    """仅 clips/clip_<idx>.mp4 缺失时，从已通过校验的 shots/<idx>/video.mp4 本地补拷。

    前置由 cache_decision 保证：视频存在且 >20KB、prompt_hash 与本次输入一致。
    因此这里只做本地 IO，**不发任何远端请求**——避免「缺个副本就重烧一次生成」。
    """
    try:
        video_path = Path(video_path)
        clip_path = Path(clip_path)
        if not (video_path.exists() and video_path.stat().st_size > 20 * 1024):
            return False
        clip_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(video_path), str(clip_path))
        return clip_path.exists() and clip_path.stat().st_size > 20 * 1024
    except OSError:
        return False

def submit_failure_code(classification: str) -> str:
    """一次生成 POST 的失败分类 → 失败记录的 `code`（批处理据此定退出码）.

    缺了这一步，批处理只能记默认的 `candidate_submit_failed`，`submission_unknown`
    这类需要人工处置的结局会被报成普通工具失败（2026-10-04 复核 P2-8）。
    """
    return {
        SUBMIT_UNKNOWN: "submission_unknown",
        SUBMIT_RATE_LIMITED: "rate_limited",
        SUBMIT_REJECTED: "provider_failed",
    }.get(str(classification or ""), "candidate_submit_failed")

async def _poll_video_production(*args, trace_attempt, **kwargs):
    from shared import video_attempts as va
    task_id, idx = args[3], args[4]
    before = len(_pv("_failed_videos").get(idx, []))
    va.transition(trace_attempt, va.STATE_RUNNING, task_id=task_id)
    try:
        ok = await _pc("_poll_video", *args, trace_attempt=trace_attempt, **kwargs)
        _pc("_trace_video_event", trace_attempt, "production_completed", task_id=task_id,
                           success=ok, failures=_pv("_failed_videos").get(idx, [])[before:])
        if not ok and va.current_state(trace_attempt) == va.STATE_RUNNING:
            va.transition(trace_attempt, va.STATE_IO_FAILED, kind="fetch_incomplete",
                          task_id=task_id, remote_status="unverified")
        return ok
    except BaseException as exc:
        _pv("_failed_videos")[idx].append({"task_id": task_id, "code": "poll_interrupted",
                                    "error": str(exc), "attempt": str(trace_attempt)})
        _pc("_trace_video_event", trace_attempt, "poll_interrupted", task_id=task_id, error=str(exc))
        raise

_PROVIDER_FAILURE_MARKERS = ("生成失败", "轮询超时", "未找到 video_url")
