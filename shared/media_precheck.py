# -*- coding: utf-8 -*-
"""本地视频预筛：静止 / 闪帧 / 时长偏差（M8.2，2026-11-09）.

**纯 FFmpeg 帧采样，不调用任何视觉大模型**。用途是在付费复检/人工评阅之前，
用确定性信号把明显有问题的片段挑出来：

- `static`：所有采样帧哈希相同（画面完全不动）；
- `near_static`：相邻采样帧相同比例过高（≥``FROZEN_RATIO``）；
- `flash_frame`：单帧与前后都不同、而前后相同（孤立闪帧）；
- `duration_deviation`：实测时长与计划时长偏差超过容差。

这些是**预筛信号**，不是内容质量结论：合法静镜/定格/闪白也可能命中。verdict
只在无法探测视频时才是 ``blocked``；其余均为 ``ok`` + warnings，交评审决定。
"""
from __future__ import annotations

import shutil
import subprocess

SCHEMA = "media_precheck.v1"
FROZEN_RATIO = 0.9
DURATION_TOLERANCE_SEC = 0.5
DURATION_TOLERANCE_RATIO = 0.10


def available() -> bool:
    return shutil.which("ffmpeg") is not None


def sample_frame_hashes(path, *, fps: float = 1.0, max_frames: int = 120) -> list[str]:
    """用 FFmpeg 逐帧取 32×32 灰度帧的 SHA-256；返回按时间排序的哈希列表."""
    if not available():
        raise RuntimeError("ffmpeg 不可用")
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(path),
        "-vf", f"fps={float(fps):g},scale=32:32,format=gray",
        "-frames:v", str(int(max_frames)),
        "-f", "framehash", "-hash", "sha256", "pipe:1",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError("ffmpeg 帧采样失败: %s" % (proc.stderr or "").strip()[:300])
    hashes: list[str] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        token = line.rsplit(",", 1)[-1].strip()
        if len(token) == 64 and all(c in "0123456789abcdef" for c in token.lower()):
            hashes.append(token.lower())
    return hashes


def analyze_hashes(hashes) -> dict:
    """帧哈希序列 → 静止/近静止/闪帧结论（纯函数，可离线单测）."""
    seq = [str(h).lower() for h in (hashes or []) if h]
    warnings: list[dict] = []
    if not seq:
        return {"schema": SCHEMA, "frame_count": 0, "verdict": "blocked",
                "code": "no_frames", "warnings": [
                    {"code": "no_frames", "message": "未采到任何视频帧"}]}
    if len(seq) == 1:
        return {"schema": SCHEMA, "frame_count": 1, "verdict": "ok",
                "code": "single_frame", "warnings": [
                    {"code": "insufficient_samples",
                     "message": "只采到 1 帧，无法判断静止/闪帧"}]}
    adjacent = list(zip(seq, seq[1:]))
    frozen_ratio = sum(1 for a, b in adjacent if a == b) / float(len(adjacent))
    flash_indices = [i for i in range(1, len(seq) - 1)
                     if seq[i - 1] == seq[i + 1] and seq[i] != seq[i - 1]]
    repeated = [{"from": i, "to": i + 1} for i, (a, b) in enumerate(adjacent) if a == b]
    if frozen_ratio >= 1.0:
        warnings.append({
            "code": "static_video",
            "message": "所有 %d 个采样帧哈希相同（画面完全静止）" % len(seq),
        })
    elif frozen_ratio >= FROZEN_RATIO:
        warnings.append({
            "code": "near_static_video",
            "message": "相邻采样帧相同比例 %.0f%%（≥%.0f%%，接近静止）"
                       % (frozen_ratio * 100, FROZEN_RATIO * 100),
        })
    if flash_indices:
        warnings.append({
            "code": "flash_frame",
            "message": "检测到 %d 个孤立闪帧（采样序号 %s）"
                       % (len(flash_indices), flash_indices[:8]),
        })
    if warnings and any(w["code"] in ("static_video", "near_static_video")
                        for w in warnings):
        code = "static"
    elif flash_indices:
        code = "flash"
    else:
        code = "ok"
    return {
        "schema": SCHEMA,
        "frame_count": len(seq),
        "frozen_ratio": round(frozen_ratio, 4),
        "repeated_pairs": len(repeated),
        "flash_frame_indices": flash_indices,
        "verdict": "ok",
        "code": code,
        "warnings": warnings,
    }


def duration_check(actual_sec, expected_sec, *,
                   tolerance_sec: float = DURATION_TOLERANCE_SEC,
                   tolerance_ratio: float = DURATION_TOLERANCE_RATIO) -> dict:
    """实测/计划时长偏差；无法比较时返回 `checked=False`，不伪造结论."""
    try:
        actual = float(actual_sec)
        expected = float(expected_sec)
    except (TypeError, ValueError):
        return {"checked": False, "code": "duration_unknown",
                "message": "时长不可比较"}
    if actual <= 0 or expected <= 0:
        return {"checked": False, "code": "duration_unknown",
                "message": "时长非正数，拒绝比较"}
    deviation = actual - expected
    tolerance = max(float(tolerance_sec), abs(expected) * float(tolerance_ratio))
    ok = abs(deviation) <= tolerance
    return {
        "checked": True,
        "code": "ok" if ok else "duration_deviation",
        "actual_sec": round(actual, 3),
        "expected_sec": round(expected, 3),
        "deviation_sec": round(deviation, 3),
        "tolerance_sec": round(tolerance, 3),
        "verdict": "ok" if ok else "warning",
        "message": ("时长一致（偏差 %+.2fs，容差 %.2fs）" % (deviation, tolerance)
                    if ok else
                    "时长偏差 %+.2fs 超过容差 %.2fs（实际 %.2fs / 计划 %.2fs）"
                    % (deviation, tolerance, actual, expected)),
    }


def precheck_video(path, *, expected_duration_sec=None, fps: float = 1.0,
                   max_frames: int = 120) -> dict:
    """单视频预筛：时长 + 帧采样分析；不调用视觉模型."""
    from step04_merge.stretch import get_media_duration
    result = {"schema": SCHEMA, "path": str(path), "verdict": "ok",
              "problems": [], "warnings": [], "network_calls": 0,
              "vision_model_calls": 0}
    try:
        actual = float(get_media_duration(path) or 0)
    except Exception as exc:
        result.update({"verdict": "blocked", "code": "duration_probe_failed",
                       "problems": [{"code": "duration_probe_failed",
                                     "message": f"时长探测失败: {exc}"}]})
        return result
    result["duration_sec"] = round(actual, 3)
    result["duration"] = duration_check(actual, expected_duration_sec)
    if result["duration"].get("checked") and result["duration"]["verdict"] == "warning":
        result["warnings"].append({"code": "duration_deviation",
                                   "message": result["duration"]["message"]})
    try:
        hashes = sample_frame_hashes(path, fps=fps, max_frames=max_frames)
    except Exception as exc:
        result["warnings"].append({"code": "frame_sampling_failed",
                                   "message": f"帧采样失败（不影响时长结论）: {exc}"})
        result["code"] = result.get("duration", {}).get("code") or "sampling_failed"
        return result
    analysis = analyze_hashes(hashes)
    result["frames"] = analysis
    result["warnings"].extend(analysis.get("warnings") or [])
    if analysis.get("verdict") == "blocked":
        result["verdict"] = "blocked"
        result["problems"].extend(analysis.get("warnings") or [])
    result["code"] = result.get("code") or analysis.get("code") or "ok"
    return result


def precheck_shot(shot: dict, video_path) -> dict:
    shot = shot if isinstance(shot, dict) else {}
    return precheck_video(video_path,
                          expected_duration_sec=shot.get("duration_sec"))


__all__ = ["DURATION_TOLERANCE_RATIO", "DURATION_TOLERANCE_SEC", "FROZEN_RATIO",
           "SCHEMA", "analyze_hashes", "available", "duration_check",
           "precheck_shot", "precheck_video", "sample_frame_hashes"]
