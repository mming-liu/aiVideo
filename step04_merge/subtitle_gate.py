# -*- coding: utf-8 -*-
"""字幕屏显质量门（2026-11-28 O7）.

竖屏短剧的字幕约束不是审美偏好，而是**可读性硬条件**：一屏字太多、停留太短、
跨镜越界，观众就是看不清。本模块只做三件事，全部是纯函数（离线可测）：

1. :func:`check_plan` —— 按 cue 逐条核对：单屏字符数、最小时长、时窗越界、
   跨镜窗口越界、相邻 cue 重叠；
2. :func:`wrap_plan` —— 超长 cue 按标点**均衡**拆成多条（保持文本顺序与总时长，
   按字符比例分配时窗），拆分后仍超长的退化为等长硬切；
3. :func:`enforce` —— 按 `MERGE_SUBTITLE_GATE`（`off` / `warn` / `block`）决定
   是否只记问题、还是直接拒收（`SubtitleErgonomicsError`）。

默认 `warn`：默认不改变既有产物，只把问题记日志；`block` 才是硬门（显式开启）。
阈值 `MERGE_SUBTITLE_MAX_CHARS`（默认 16，竖屏）与
`MERGE_SUBTITLE_MIN_DURATION_SEC`（默认 0.4）。
"""
from __future__ import annotations

import os

from shared.subtitle_contract import (SubtitleCue, SubtitlePlan,
                                      split_subtitle_by_punctuation)

ENV_MAX_CHARS = "MERGE_SUBTITLE_MAX_CHARS"
ENV_MIN_DURATION = "MERGE_SUBTITLE_MIN_DURATION_SEC"
ENV_POLICY = "MERGE_SUBTITLE_GATE"

POLICY_OFF = "off"
POLICY_WARN = "warn"
POLICY_BLOCK = "block"

DEFAULT_MAX_CHARS = 16
DEFAULT_MIN_DURATION_SEC = 0.4


class SubtitleErgonomicsError(ValueError):
    """`block` 策略下的字幕可读性问题（携带结构化清单）."""

    def __init__(self, problems: list[dict]):
        self.problems = tuple(problems)
        super().__init__("字幕屏显质量未通过：%d 条问题（首条：%s）"
                         % (len(self.problems),
                            self.problems[0].get("code") if self.problems else ""))


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


def gate_policy(env=None) -> str:
    source = os.environ if env is None else env
    raw = str(source.get(ENV_POLICY, "") or "").strip().lower()
    if raw in ("", POLICY_WARN):
        return POLICY_WARN
    if raw in (POLICY_OFF, POLICY_BLOCK):
        return raw
    raise ValueError("%s=%r 非法；合法值：off / warn / block" % (ENV_POLICY, raw))


def _thresholds(env=None) -> tuple[int, float]:
    source = os.environ if env is None else env
    raw_max = source.get(ENV_MAX_CHARS)
    try:
        max_chars = int(str(raw_max).strip()) if raw_max not in (None, "") else DEFAULT_MAX_CHARS
    except (TypeError, ValueError):
        max_chars = DEFAULT_MAX_CHARS
    return max(1, max_chars), max(0.0, _env_float(ENV_MIN_DURATION, DEFAULT_MIN_DURATION_SEC))


def check_plan(plan: SubtitlePlan, *, max_chars: int = DEFAULT_MAX_CHARS,
               min_duration_sec: float = DEFAULT_MIN_DURATION_SEC,
               total_duration: float | None = None,
               clip_windows: list[tuple] | None = None) -> list[dict]:
    """逐 cue 核对，返回问题清单（空 = 通过）；不修改 plan."""
    problems: list[dict] = []
    cues = list(plan.cues)
    epsilon = 1e-6
    for index, cue in enumerate(cues):
        text = str(cue.text or "")
        # 换行按最长一行算：竖屏一屏放不下两行长句
        longest = max((len(part) for part in text.split("\n")), default=0) if text else 0
        if longest > max_chars:
            problems.append({"code": "cue_too_long", "cue_id": cue.cue_id,
                             "shot_index": cue.shot_index, "chars": longest,
                             "max_chars": max_chars,
                             "message": "单屏 %d 字超过 %d 字上限" % (longest, max_chars)})
        if not text.strip():
            problems.append({"code": "cue_empty", "cue_id": cue.cue_id,
                             "shot_index": cue.shot_index,
                             "message": "空文本 cue"})
        if cue.duration_sec + epsilon < min_duration_sec:
            problems.append({"code": "cue_too_short", "cue_id": cue.cue_id,
                             "shot_index": cue.shot_index,
                             "duration_sec": round(cue.duration_sec, 4),
                             "min_duration_sec": min_duration_sec,
                             "message": "停留 %.3fs 短于 %.2fs" % (cue.duration_sec,
                                                                 min_duration_sec)})
        if cue.start_sec < -epsilon or cue.end_sec + epsilon < cue.start_sec:
            problems.append({"code": "cue_time_invalid", "cue_id": cue.cue_id,
                             "shot_index": cue.shot_index,
                             "start_sec": cue.start_sec, "end_sec": cue.end_sec,
                             "message": "时窗非法（start=%s end=%s）"
                                        % (cue.start_sec, cue.end_sec)})
        if total_duration is not None and cue.end_sec > float(total_duration) + epsilon:
            problems.append({"code": "cue_out_of_range", "cue_id": cue.cue_id,
                             "shot_index": cue.shot_index,
                             "end_sec": cue.end_sec, "total_duration": total_duration,
                             "message": "cue 结束 %.3fs 超出成片总时长 %.3fs"
                                        % (cue.end_sec, float(total_duration))})
        if clip_windows and cue.shot_index is not None:
            idx = int(cue.shot_index)
            if 0 <= idx < len(clip_windows):
                lo, hi = clip_windows[idx]
                if cue.start_sec < float(lo) - epsilon or cue.end_sec > float(hi) + epsilon:
                    problems.append({"code": "cue_crosses_shot", "cue_id": cue.cue_id,
                                     "shot_index": idx,
                                     "window": [float(lo), float(hi)],
                                     "start_sec": cue.start_sec, "end_sec": cue.end_sec,
                                     "message": "cue 越过镜头 %d 的窗口 [%.3f, %.3f]"
                                                % (idx, float(lo), float(hi))})
        if index > 0:
            prev = cues[index - 1]
            if cue.start_sec + epsilon < prev.end_sec:
                problems.append({"code": "cue_overlap", "cue_id": cue.cue_id,
                                 "previous_cue_id": prev.cue_id,
                                 "overlap_sec": round(prev.end_sec - cue.start_sec, 4),
                                 "message": "与前一条 cue 重叠 %.3fs"
                                            % (prev.end_sec - cue.start_sec)})
    return problems


def _split_text(text: str, max_chars: int) -> list[str]:
    """按标点均衡拆句；拆不动时退化为等长硬切（保证每段 ≤ max_chars）."""
    parts = split_subtitle_by_punctuation(text, max_chars)
    if not parts:
        return [text]
    out: list[str] = []
    for part in parts:
        if len(part) <= max_chars:
            out.append(part)
            continue
        for start in range(0, len(part), max_chars):
            out.append(part[start:start + max_chars])
    return out


def wrap_plan(plan: SubtitlePlan, *, max_chars: int = DEFAULT_MAX_CHARS) -> SubtitlePlan:
    """把超长 cue 拆成多条（文本顺序不变、时窗按字符比例分配）；无需拆时原样返回."""
    if all(len(str(cue.text or "")) <= max_chars for cue in plan.cues):
        return plan
    wrapped: list[SubtitleCue] = []
    for cue in plan.cues:
        text = str(cue.text or "")
        if len(text) <= max_chars or not text:
            wrapped.append(cue)
            continue
        parts = _split_text(text, max_chars)
        total_chars = max(1, sum(len(p) for p in parts))
        cursor = float(cue.start_sec)
        span = float(cue.end_sec) - float(cue.start_sec)
        for index, part in enumerate(parts):
            share = span * (len(part) / total_chars)
            start = cursor
            end = float(cue.end_sec) if index == len(parts) - 1 else cursor + share
            wrapped.append(SubtitleCue(
                cue_id="%s-%d" % (cue.cue_id, index + 1), text=part,
                start_sec=start, end_sec=end, shot_index=cue.shot_index,
                source=cue.source, part_index=index + 1,
                char_start=None, char_end=None))
            cursor = end
    return SubtitlePlan(cues=tuple(wrapped), source=plan.source,
                        fallbacks=plan.fallbacks)


def enforce(plan: SubtitlePlan, *, warn=None, total_duration: float | None = None,
            clip_windows: list[tuple] | None = None, env=None) -> dict:
    """按策略核对：返回 `{policy, ok, problems, wrapped, plan}`；`block` 时抛错.

    **只有 `block` 策略会改写 plan**（把超长 cue 拆开）：`warn` 只报告，保证默认
    配置下 SRT 产物与既有行为逐字节一致。
    """
    policy = gate_policy(env)
    max_chars, min_duration = _thresholds(env)
    if policy == POLICY_OFF:
        return {"policy": policy, "ok": True, "problems": [], "wrapped": False,
                "max_chars": max_chars, "min_duration_sec": min_duration,
                "plan": plan}
    problems = check_plan(plan, max_chars=max_chars,
                          min_duration_sec=min_duration,
                          total_duration=total_duration,
                          clip_windows=clip_windows)
    wrapped = False
    if policy == POLICY_BLOCK and any(p["code"] == "cue_too_long" for p in problems):
        fixed = wrap_plan(plan, max_chars=max_chars)
        remaining = check_plan(fixed, max_chars=max_chars,
                               min_duration_sec=min_duration,
                               total_duration=total_duration,
                               clip_windows=clip_windows)
        if len(remaining) < len(problems):
            plan, problems, wrapped = fixed, remaining, True
    if problems and policy == POLICY_BLOCK:
        raise SubtitleErgonomicsError(problems)
    if problems and warn is not None:
        warn("[字幕门] %d 条可读性问题（策略 %s，已自动拆分=%s）：%s"
             % (len(problems), policy, wrapped,
                "; ".join("%s@%s" % (p["code"], p.get("cue_id")) for p in problems[:5])))
    return {"policy": policy, "ok": not problems, "problems": problems,
            "wrapped": wrapped, "max_chars": max_chars,
            "min_duration_sec": min_duration, "plan": plan}


__all__ = ["DEFAULT_MAX_CHARS", "DEFAULT_MIN_DURATION_SEC", "ENV_MAX_CHARS",
           "ENV_MIN_DURATION", "ENV_POLICY", "POLICY_BLOCK", "POLICY_OFF",
           "POLICY_WARN", "SubtitleErgonomicsError", "check_plan", "enforce",
           "gate_policy", "wrap_plan"]
