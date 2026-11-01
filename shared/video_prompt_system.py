# -*- coding: utf-8 -*-
"""视频 prompt 的系统段组装（共用、幂等、来源可核）.

S02 规格（修订）：
- 系统段只由**结构化来源记录**（origin=system_camera）确认；文本里的“运镜：”不冒充系统段；
- 文首插入一次；无来源记录时，即使正文以“运镜：”开头，也按**用户正文**处理；
- 已有来源记录：内容相同且文首匹配 → unchanged；内容不同/重复/文本缺失 → 明确问题；
- camera_movement 缺失 → 零段，不补造；
- 超长 → 明确问题并禁止发送（unchanged 出口也要查长度）；不截断后循环追加。
"""
from __future__ import annotations

SYSTEM_ORIGIN = "system_camera"
CAMERA_PREFIX = "运镜："


class SystemCameraError(RuntimeError):
    """系统运镜段不可安全组装（禁止发送）."""

    def __init__(self, code: str, message: str, **fields):
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.fields = dict(fields)


def _head_line(prompt: str) -> str:
    lines = str(prompt or "").splitlines()
    return lines[0] if lines else ""


def _over_limit(prompt: str, max_chars: int | None) -> dict | None:
    if max_chars is not None and len(prompt) > max_chars:
        return {"code": "system_camera_over_limit",
                "message": "组装后长度 %d 超过上限 %d，禁止发送" % (len(prompt), max_chars)}
    return None


def apply_system_camera(prompt: str, camera_movement: str, *, max_chars: int | None = None,
                        existing: list[dict] | None = None,
                        provider: str = "") -> dict:
    """在文首组装系统运镜段；返回结构化结果，不静默改语义."""
    prompt = str(prompt or "")
    camera = str(camera_movement or "").strip()
    problems: list[dict] = []
    segments: list[dict] = []
    recorded = [r for r in (existing or [])
                if isinstance(r, dict) and r.get("origin") == SYSTEM_ORIGIN]

    # 1) 相机缺失：零段；只有来源记录存在时才是 orphan（文本不算）
    if not camera:
        if recorded:
            problems.append({"code": "system_camera_orphan",
                             "message": "camera_movement 为空但存在系统运镜来源记录"})
        return {"prompt": prompt, "status": "conflict" if problems else "ok",
                "applied": False, "segments": [], "problems": problems,
                "modifications": []}

    # 2) 来源记录：唯一权威
    if len(recorded) > 1:
        problems.append({"code": "system_camera_duplicate",
                         "message": "存在多个系统运镜来源记录（%d）" % len(recorded)})
        return {"prompt": prompt, "status": "conflict", "applied": False,
                "segments": [], "problems": problems, "modifications": []}

    if recorded:
        record = recorded[0]
        record_value = str(record.get("camera_movement") or "").strip()
        if record_value != camera:
            problems.append({"code": "system_camera_conflict",
                             "message": "系统运镜记录 %r 与 camera_movement %r 不一致"
                                        % (record_value, camera)})
        elif _head_line(prompt) != f"{CAMERA_PREFIX}{camera}":
            problems.append({"code": "system_camera_missing",
                             "message": "来源记录有系统运镜段，但文本文首不存在"})
        else:
            over = _over_limit(prompt, max_chars)
            if over:
                problems.append(over)
            else:
                segments.append({"origin": SYSTEM_ORIGIN, "camera_movement": camera,
                                 "position": "head", "provider": provider})
                return {"prompt": prompt, "status": "unchanged", "applied": False,
                        "segments": segments, "problems": [], "modifications": []}
        return {"prompt": prompt, "status": "conflict", "applied": False,
                "segments": [], "problems": problems, "modifications": []}

    # 3) 无来源记录：正文即用户内容（即使以“运镜：”开头也不冒充系统段）
    candidate = f"{CAMERA_PREFIX}{camera}\n{prompt}"
    over = _over_limit(candidate, max_chars)
    if over:
        problems.append(over)
        return {"prompt": prompt, "status": "over_limit", "applied": False,
                "segments": [], "problems": problems, "modifications": []}

    segments.append({"origin": SYSTEM_ORIGIN, "camera_movement": camera,
                     "position": "head", "provider": provider})
    return {"prompt": candidate, "status": "ok", "applied": True,
            "segments": segments, "problems": [],
            "modifications": [{"kind": "system_camera", "origin": SYSTEM_ORIGIN,
                               "camera_movement": camera, "position": "head",
                               "provider": provider}]}


def system_segment_count(prompt: str) -> int:
    """文首连续“运镜：”行计数（仅用于检查组装结果，**不**用于识别系统段）."""
    count = 0
    for line in str(prompt or "").splitlines():
        if line.startswith(CAMERA_PREFIX):
            count += 1
        else:
            break
    return count
