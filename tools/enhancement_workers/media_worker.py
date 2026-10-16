# -*- coding: utf-8 -*-
"""tools.enhancement_workers.media_worker - PySceneDetect 切点检测 worker（计划 §B1/§F5）.

为什么必须隔进程：PySceneDetect 及其 OpenCV/numpy 依赖只装在 Python 3.12 隔离
工作区里，宿主（3.14 主项目）**不 import** 它（§F5）。

worker 契约：
- payload: ``{video_path, detector: {adaptive_threshold, min_scene_len_frames,
  window_width}, start_sec?, end_sec?}``；
- result: ``{cuts: [{start_sec, start_frame, kind}], scene_count, method,
  detector_parameters, media: {fps, duration_sec, frame_count},
  tool_versions: {scenedetect}}``；
- **没有 scenedetect 就 unavailable/package_missing**（不下载、不降级、不伪造空结果）。

运行（宿主自动按此 argv 拉起，参数列表，无 shell）::

    <py312>/python.exe tools/enhancement_workers/media_worker.py \
        --request R.json --response W.json
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))

from enhancement_workers.protocol import (  # noqa: E402
    WorkerError,
    package_version,
)
from enhancement_workers.runner import WorkerUnavailable, main, require_import  # noqa: E402

WORKER_NAME = "media_cuts"


def _as_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _open_video(scenedetect, path: str):
    """打开视频（0.6 ``open_video`` 优先；0.5 ``VideoManager`` 兼容）."""
    opener = getattr(scenedetect, "open_video", None)
    if callable(opener):
        return opener(path)
    vm_cls = getattr(scenedetect, "VideoManager", None)
    if vm_cls is None:
        raise WorkerUnavailable("scenedetect_api_unsupported",
                                "scenedetect 既无 open_video 也无 VideoManager，"
                                "本 worker 不支持该版本")
    vm = vm_cls([path])
    vm.set_downscale_factor()
    vm.start()
    return vm


def _media_info(video) -> dict:
    """从 PySceneDetect 的 VideoStream 取媒体事实；取不到就留 null（不编造）."""
    info = {"fps": None, "duration_sec": None, "frame_count": None}
    frame_rate = getattr(video, "frame_rate", None)
    if frame_rate is None:
        getter = getattr(video, "get_fps", None)
        frame_rate = getter() if callable(getter) else None
    info["fps"] = _as_float(frame_rate)
    duration = getattr(video, "duration", None)
    if duration is not None:
        getter = getattr(duration, "get_seconds", None)
        info["duration_sec"] = _as_float(getter() if callable(getter) else duration)
    else:
        getter = getattr(video, "get_duration", None)
        info["duration_sec"] = _as_float(getter()) if callable(getter) else None
    return info


def _build_detector(adaptive_cls, params: dict):
    """按可用签名传参（不同 scenedetect 版本参数集不同）——**不**静默丢关键参数."""
    import inspect

    kwargs = {
        "adaptive_threshold": _as_float(params.get("adaptive_threshold"), 3.0),
        "min_scene_len": int(params.get("min_scene_len_frames", 15)),
        "window_width": int(params.get("window_width", 2)),
        "luma_only": bool(params.get("luma_only", False)),
    }
    try:
        allowed = set(inspect.signature(adaptive_cls.__init__).parameters)
    except (TypeError, ValueError):
        allowed = set(kwargs)
    unsupported = [k for k in ("window_width", "luma_only") if k not in allowed]
    accepted = {k: v for k, v in kwargs.items() if k in allowed}
    detector = adaptive_cls(**accepted)
    return detector, unsupported


def detect_cuts(payload: dict) -> dict:
    scenedetect = require_import(
        "scenedetect", "scenedetect",
        hint="安装进 .runtime/enhancements-py312 隔离工作区（宿主不装）")

    video_path = payload.get("video_path")
    if not video_path:
        raise WorkerError("bad_payload", "payload 缺少 video_path", field_path="video_path")
    if not Path(str(video_path)).is_file():
        raise WorkerError("input_missing", f"视频不存在: {video_path}",
                          field_path="video_path")

    params = dict(payload.get("detector") or {})
    try:
        from scenedetect import SceneManager
        from scenedetect.detectors import AdaptiveDetector
    except Exception as exc:  # noqa: BLE001
        raise WorkerUnavailable("scenedetect_api_unsupported",
                                f"scenedetect 导入部件失败: {exc}") from exc

    detector, unsupported = _build_detector(AdaptiveDetector, params)
    video = _open_video(scenedetect, str(video_path))
    manager = SceneManager()
    manager.add_detector(detector)
    try:
        manager.detect_scenes(video)
    except TypeError:
        # 0.5 API：detect_scenes(video_manager=...)
        manager.detect_scenes(video_manager=video)
    scenes = manager.get_scene_list()

    cuts = []
    for start, _end in list(scenes)[1:]:  # 首段起点不是"切点"
        start_sec = _as_float(start.get_seconds())
        start_frame = int(start.get_frames()) if hasattr(start, "get_frames") else None
        cuts.append({"start_sec": start_sec, "start_frame": start_frame, "kind": "cut"})
    return {
        "cuts": cuts,
        "scene_count": len(scenes),
        "method": "PySceneDetect.AdaptiveDetector",
        "detector_parameters": {
            "adaptive_threshold": _as_float(params.get("adaptive_threshold"), 3.0),
            "min_scene_len_frames": int(params.get("min_scene_len_frames", 15)),
            "window_width": int(params.get("window_width", 2)),
            "unsupported_parameters": unsupported,
        },
        "media": _media_info(video),
        "tool_versions": {"scenedetect": package_version("scenedetect")},
    }


if __name__ == "__main__":
    sys.exit(main(WORKER_NAME, detect_cuts))
