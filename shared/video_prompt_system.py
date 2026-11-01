# -*- coding: utf-8 -*-
"""视频 prompt 的系统段组装（共用、幂等、来源可核）.

S02 规格：
- 系统运镜段固定放在**文首**，全链路只注入一次；
- 来源记录（origin=system_camera）与文本分离：用户正文里的“运镜：”不冒充系统段；
- 幂等：已有同内容系统段 → 不变；内容不同 / 重复段 → 明确问题，不静默跳过；
- camera_movement 缺失 → 零段，不补造；
- 超长 → 明确问题并**禁止发送**；不截断后循环追加；
- 压缩/清洗在系统段之前完成，系统段组装后再检查最终长度。
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


def _leading_camera_lines(prompt: str) -> list[str]:
    """返回文首连续的系统运镜行（只认第一行起的连续行）."""
    lines = prompt.splitlines()
    values: list[str] = []
    for line in lines:
        if line.startswith(CAMERA_PREFIX):
            values.append(line[len(CAMERA_PREFIX):].strip())
        else:
            break
    return values


def apply_system_camera(prompt: str, camera_movement: str, *, max_chars: int | None = None,
                        existing: list[dict] | None = None,
                        provider: str = "") -> dict:
    """在文首组装系统运镜段；返回结构化结果，不静默改语义."""
    prompt = str(prompt or "")
    camera = str(camera_movement or "").strip()
    problems: list[dict] = []
    segments: list[dict] = []
    applied = False

    # 1) 结构化来源优先：existing 是 prepared/frozen 记录，不是文本猜测
    recorded = [r for r in (existing or [])
                if isinstance(r, dict) and r.get("origin") == SYSTEM_ORIGIN]
    leading = _leading_camera_lines(prompt)

    if not camera:
        if recorded or leading:
            problems.append({"code": "system_camera_orphan",
                             "message": "camera_movement 为空但存在系统运镜段/记录"})
        return {"prompt": prompt, "status": "ok" if not problems else "conflict",
                "applied": False, "segments": segments, "problems": problems,
                "modifications": []}

    # 2) 来源记录与文本都要一致
    recorded_values = [str(r.get("camera_movement") or "").strip() for r in recorded]
    if len(recorded_values) > 1 or len(leading) > 1:
        problems.append({"code": "system_camera_duplicate",
                         "message": "存在多个系统运镜段（记录 %d / 文本 %d）"
                                    % (len(recorded_values), len(leading))})
    elif recorded_values and leading and recorded_values[0] != leading[0]:
        problems.append({"code": "system_camera_conflict",
                         "message": "系统运镜记录 %r 与文首段 %r 不一致"
                                    % (recorded_values[0], leading[0])})
    elif recorded_values and recorded_values[0] != camera:
        problems.append({"code": "system_camera_conflict",
                         "message": "系统运镜记录 %r 与 camera_movement %r 不一致"
                                    % (recorded_values[0], camera)})
    elif recorded_values and not leading:
        problems.append({"code": "system_camera_missing",
                         "message": "来源记录有系统运镜段，但文本文首不存在"})
    elif leading and leading[0] != camera:
        problems.append({"code": "system_camera_conflict",
                         "message": "文首系统段 %r 与 camera_movement %r 不一致"
                                    % (leading[0], camera)})

    if problems:
        return {"prompt": prompt, "status": "conflict", "applied": False,
                "segments": segments, "problems": problems, "modifications": []}

    if leading:
        segments.append({"origin": SYSTEM_ORIGIN, "camera_movement": camera,
                         "position": "head", "provider": provider})
        return {"prompt": prompt, "status": "unchanged", "applied": False,
                "segments": segments, "problems": [], "modifications": []}

    # 3) 文首插入；先组装、后检查总长度（不截断后循环追加）
    candidate = f"{CAMERA_PREFIX}{camera}\n{prompt}"
    if max_chars is not None and len(candidate) > max_chars:
        problems.append({"code": "system_camera_over_limit",
                         "message": "系统段组装后长度 %d 超过上限 %d，禁止发送"
                                    % (len(candidate), max_chars)})
        return {"prompt": prompt, "status": "over_limit", "applied": False,
                "segments": segments, "problems": problems, "modifications": []}

    segments.append({"origin": SYSTEM_ORIGIN, "camera_movement": camera,
                     "position": "head", "provider": provider})
    return {"prompt": candidate, "status": "ok", "applied": True,
            "segments": segments, "problems": [],
            "modifications": [{"kind": "system_camera", "camera_movement": camera,
                               "position": "head", "provider": provider}]}


def system_segment_count(prompt: str) -> int:
    """文首连续系统段计数（供验收：应为 0 或 1）."""
    return len(_leading_camera_lines(prompt))
