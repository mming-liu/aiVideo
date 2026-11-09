# -*- coding: utf-8 -*-
"""TTS 音色绑定校验与镜头窗口自适应变速（M8.3，2026-11-09）.

两个纯函数层，都不调用 provider：

1. **音色绑定**：`binding_issues(units)` 检查「同一 voice_profile_id ↔ 同一
   speaker」的一一绑定。同一音色档案被两个声线复用、或同一 speaker 被两个档案
   复用，都是跨角色串音/音色漂移的确定性信号。
2. **窗口自适应变速**：`adaptive_rate(required_sec, window_sec)` 在
   ``[1.0, 1.20]`` 内按「需要时长 / 可用窗口」确定性地提速；超过 1.20 返回
   ``overflow=True``，**不把语速推到 1.20 以上**（那会变成加速朗读掩盖超时，
   项目口径禁止）。`fit_unit()` 只改 `SpeechUnit.rate`，不改文本、不改身份。

边界：
- 2% 容差内视为放得下（rate=1.0），避免估算噪声导致无意义变速；
- 显式 rate ≠ 1.0（旧请求/人工指定）不覆盖；
- provider 不支持 rate 参数时不变速（不假装生效，由 `resolve_applied` 记账）。
"""
from __future__ import annotations

from dataclasses import replace

RATE_MIN = 1.0
RATE_MAX = 1.20
WINDOW_TOLERANCE = 0.02


def adaptive_rate(required_sec, window_sec, *,
                  tolerance: float = WINDOW_TOLERANCE) -> dict:
    """需要时长 vs 可用窗口 → 建议语速（1.0–1.20）."""
    try:
        required = float(required_sec)
        window = float(window_sec)
    except (TypeError, ValueError):
        return {"rate": RATE_MIN, "status": "invalid", "overflow": False,
                "reason": "时长不是数字"}
    if required <= 0 or window <= 0:
        return {"rate": RATE_MIN, "status": "invalid", "overflow": False,
                "reason": "时长必须为正数"}
    if required <= window * (1.0 + max(0.0, float(tolerance))):
        return {"rate": RATE_MIN, "status": "fits", "overflow": False,
                "required_sec": round(required, 3),
                "window_sec": round(window, 3),
                "ratio": round(required / window, 4), "reason": "窗口够用"}
    ratio = required / window
    if ratio > RATE_MAX + 1e-9:
        return {"rate": RATE_MAX, "status": "overflow", "overflow": True,
                "required_sec": round(required, 3),
                "window_sec": round(window, 3),
                "ratio": round(ratio, 4),
                "reason": ("需要 %.2fs / 窗口 %.2fs = %.3fx > %.2fx；"
                           "不允许继续加速，应交回内容修订/拆镜"
                           % (required, window, ratio, RATE_MAX))}
    rate = round(min(RATE_MAX, max(RATE_MIN, ratio)), 3)
    return {"rate": rate, "status": "adjusted", "overflow": False,
            "required_sec": round(required, 3),
            "window_sec": round(window, 3),
            "ratio": round(ratio, 4),
            "reason": "在 1.0–1.20x 内按窗口自适应"}


def fit_unit(unit, *, caps=None, measured_sec=None) -> tuple:
    """按 `unit.planned_window` 返回 `(unit_or_new, plan)`；不调用 provider."""
    plan = {"status": "no_window", "rate": float(getattr(unit, "rate", 1.0) or 1.0),
            "overflow": False, "unit_id": getattr(unit, "unit_id", ""),
            "reason": "没有 planned_window，不做窗口变速"}
    window = getattr(unit, "planned_window", None)
    if not window or len(window) < 2:
        return unit, plan
    if caps is not None and not bool(getattr(caps, "supports_rate", False)):
        plan.update({"status": "unsupported",
                     "reason": "provider 未声明 supports_rate，不变速"})
        return unit, plan
    current = float(getattr(unit, "rate", 1.0) or 1.0)
    if abs(current - 1.0) > 1e-9:
        plan.update({"status": "explicit_rate", "rate": current,
                     "reason": "显式 rate 非 1.0，保留调用方设定"})
        return unit, plan
    required = measured_sec
    if required is None:
        from step01_script_gen.timecode import _estimate_duration
        required = _estimate_duration(str(getattr(unit, "text", "") or ""))
    result = adaptive_rate(required, float(window[1]) - float(window[0]))
    plan.update(result)
    if result["status"] != "adjusted":
        return unit, plan
    return replace(unit, rate=float(result["rate"])), plan


def binding_table(units) -> dict:
    """`voice_profile_id → {speaker, unit_ids}`（只收两个字段都非空的单元）."""
    table: dict = {}
    for unit in units or ():
        profile = str(getattr(unit, "voice_profile_id", "") or "").strip()
        speaker = str(getattr(unit, "speaker", "") or "").strip()
        if not profile or not speaker:
            continue
        entry = table.setdefault(profile, {"speaker": speaker, "unit_ids": []})
        entry["unit_ids"].append(str(getattr(unit, "unit_id", "") or ""))
    return table


def binding_issues(units) -> list[dict]:
    """检查音色绑定的一一关系；返回结构化问题（空=通过）."""
    issues: list[dict] = []
    table = binding_table(units)
    by_speaker: dict[str, list[str]] = {}
    for profile, entry in table.items():
        by_speaker.setdefault(entry["speaker"], []).append(profile)
    for profile, entry in table.items():
        if len({str(getattr(u, "speaker", "") or "").strip()
                for u in (units or ())
                if str(getattr(u, "voice_profile_id", "") or "").strip() == profile
                and str(getattr(u, "speaker", "") or "").strip()}) > 1:
            issues.append({
                "code": "voice_profile_speaker_conflict",
                "voice_profile_id": profile,
                "speaker": entry["speaker"],
                "unit_ids": sorted(entry["unit_ids"]),
                "message": "同一音色档案 %r 绑定了多个 speaker" % profile,
            })
    for speaker, profiles in by_speaker.items():
        if len(profiles) > 1:
            issues.append({
                "code": "speaker_profile_conflict",
                "speaker": speaker,
                "voice_profile_ids": sorted(profiles),
                "message": "同一 speaker %r 被多个音色档案复用: %s"
                           % (speaker, sorted(profiles)),
            })
    return issues


__all__ = ["RATE_MAX", "RATE_MIN", "WINDOW_TOLERANCE", "adaptive_rate",
           "binding_issues", "binding_table", "fit_unit"]
