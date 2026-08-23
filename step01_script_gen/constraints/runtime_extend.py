"""W6. 红果时长约束（v6.42 迁移自 _hard_fix_runtime_extend；08-23 加单镜上限）.

08-23 用户规则（本版核心）：
  - **正片分镜总时长（不含钩子）≥ 60s**——钩子时长另算，不计入 60s 下限
  - **单镜时长 ≤ 8s**（MAX_SHOT_DURATION，与视频模型 cap 取严）
  - 钩子时长另算：不受 8s 上限/60s 下限约束（hook 有自己的下限规则，见
    constraints/hook_duration.py）

历史背景：8-22 跑批实测 8 镜总时长 42.25s < 60s 红果下限，末镜 3.04s < 4s 红果下限。

修法：
  0. 单镜 > per_shot_cap（min(模型 cap, 8s)）→ 夹到 cap
  1. 末镜 ≤ 4s → 强制夹到 5-6s
  2. 正片分镜总时长（**不含钩子**）< 65s（红果下限 60 + 5s 安全裕度）
     → 循环按比例放大每镜 duration，直到达标或全部顶到 cap
     - 单镜上限不超 per_shot_cap
     - 单轮 per_add 上限 3.0s
  3. 全部顶到 cap 仍 < 60s（镜头数不够：8s × N < 60 即 N < 8）
     → 记 HARD Violation，走 regen 反馈要求**加镜头**（而非继续拉单镜时长）
  4. 不论是否拉伸，timecode 永远和 shot["duration"] 同步（关键修复）

幂等：单镜 ≤ cap 且总时长 ≥ 65s（或 need < 2 的贴线态）且末镜 > 4s → 跳过所有修改。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity, Violation

# 08-23 用户规则常量从 timecode.py 单一事实源取（失败时回退同值常量）
try:
    from step01_script_gen.timecode import (
        MAX_SHOT_DURATION as _MAX_SHOT_SEC,
        MIN_TOTAL_SHOTS_DURATION as _REDGUO_MIN_SEC,
    )
except Exception:  # pragma: no cover - 导入失败回退
    _MAX_SHOT_SEC = 8.0
    _REDGUO_MIN_SEC = 60.0

_REDGUO_TARGET_SEC = 65.0   # 拉伸目标 = 红果下限 60 + 5s 安全裕度
_LAST_SHOT_MIN_SEC = 4.0
_LAST_SHOT_TARGET_SEC = 5.0
_PER_ADD_CAP = 3.0
_MAX_STRETCH_PASSES = 8     # 循环拉伸安全阀（正常 1-2 轮收敛）


def _align_frame_duration(raw: float, fps: float = 24.0) -> float:
    """24fps 整数帧对齐 + ≤3 位小数. 失败时回退 round(2)."""
    try:
        from step01_script_gen.timecode import _align_frame_duration as _afd
        return _afd(raw, fps)
    except Exception:
        return round(float(raw), 2)


def _video_duration_cap() -> float:
    try:
        from shared.model_caps import active_video_duration_cap
        return float(active_video_duration_cap() or 12)
    except Exception:
        return 12.0


def _shot_dur(s: dict) -> float:
    try:
        return float(s.get("duration_sec") or s.get("duration") or 0)
    except (TypeError, ValueError):
        return 0.0


def _sum_duration(items: list[dict]) -> float:
    total = 0.0
    for s in items:
        if not isinstance(s, dict):
            continue
        total += _shot_dur(s)
    return total


def _sync_timecode(shots: list, hook: dict | None, script: dict | None, fps: float) -> None:
    """timecode 永远和 shot["duration"] 同步（不论是否拉伸都跑）."""
    if script is None:
        return
    cumulative = 0.0
    for s in shots:
        if not isinstance(s, dict):
            continue
        d = _shot_dur(s)
        s["timecode"] = {
            "start": round(cumulative, 2),
            "end": round(cumulative + d, 2),
        }
        cumulative += d
    if hook and isinstance(hook, dict):
        hd = _shot_dur(hook)
        hook["timecode"] = {
            "start": round(cumulative, 2),
            "end": round(cumulative + hd, 2),
        }
        cumulative += hd
    script["total_duration"] = round(cumulative, 2)
    if "total_effective_duration" in script:
        script["total_effective_duration"] = round(cumulative, 2)
    # 重建 timeline
    if "timeline" in script and isinstance(script["timeline"], list):
        new_timeline: list[dict] = []
        for idx, s in enumerate(shots):
            if not isinstance(s, dict):
                continue
            tc = s.get("timecode") or {}
            new_timeline.append({
                "shot_id": s.get("shot_id") or s.get("id") or idx,
                "index": idx,
                "start": tc.get("start", 0),
                "end": tc.get("end", 0),
                "duration": _shot_dur(s),
            })
        if hook and isinstance(hook, dict):
            tc = hook.get("timecode") or {}
            new_timeline.append({
                "shot_id": "hook",
                "index": len(shots),
                "start": tc.get("start", 0),
                "end": tc.get("end", 0),
                "duration": _shot_dur(hook),
                "is_hook": True,
            })
        script["timeline"] = new_timeline


class RuntimeExtendConstraint(Constraint):
    """W6. 红果时长约束：正片分镜（不含钩子）≥ 60s / 单镜 ≤ 8s / 末镜 > 4s.

    severity=HARD: 60s 红果标准是硬约束（structural）。
    skip_hook=True: 钩子时长另算——不进总时长统计、不受 8s 上限约束。
    """

    name = "runtime_extend"
    severity = Severity.HARD
    skip_hook = True

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        if not isinstance(ctx.shots, list) or not ctx.shots:
            return repairs

        # 单镜上限 = min(视频模型 cap, 8s 硬限)（08-23 用户规则）
        per_shot_cap = min(_video_duration_cap(), _MAX_SHOT_SEC)
        fps = 24.0
        if isinstance(ctx.script, dict):
            try:
                fps = float(ctx.script.get("fps", 24) or 24)
            except (TypeError, ValueError):
                fps = 24.0

        # 0) 单镜硬上限夹取：> per_shot_cap → 夹到 cap
        for s in ctx.shots:
            if not isinstance(s, dict):
                continue
            d = _shot_dur(s)
            if d > per_shot_cap:
                new_d = _align_frame_duration(per_shot_cap, fps)
                before_dur = s.get("duration_sec")
                s["duration_sec"] = new_d
                s["duration"] = new_d
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=s.get("index"),
                        action="clamp",
                        target="duration_sec",
                        before=str(before_dur),
                        after=str(new_d),
                    )
                )

        # 1) 末镜时长夹取
        last = ctx.shots[-1]
        if isinstance(last, dict):
            last_dur = _shot_dur(last)
            if 0 < last_dur <= _LAST_SHOT_MIN_SEC:
                new_last = _align_frame_duration(
                    min(_LAST_SHOT_TARGET_SEC, per_shot_cap), fps,
                )
                before_dur = last.get("duration_sec")
                last["duration_sec"] = new_last
                last["duration"] = new_last
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=last.get("index", len(ctx.shots) - 1),
                        action="clamp",
                        target="duration_sec",
                        before=str(before_dur),
                        after=str(new_last),
                    )
                )

        # 2) 总时长计算——只算正片分镜，钩子时长另算（08-23 用户规则）
        total = _sum_duration(ctx.shots)

        # 3) 总时长不足：循环按比例拉伸，直到达标（65s）或全部顶到 cap
        for _pass in range(_MAX_STRETCH_PASSES):
            if total >= _REDGUO_TARGET_SEC:
                break
            need = _REDGUO_TARGET_SEC - total
            if need < 2:
                break  # 贴线态（如 8 镜×8s=64s）：已 ≥60s 硬下限，不再微调
            extensible: list[dict] = []
            for s in ctx.shots:
                if not isinstance(s, dict):
                    continue
                d = _shot_dur(s)
                if d > 0 and d < per_shot_cap - 1.0:
                    extensible.append(s)
            if not extensible:
                break  # 全部顶到 cap，无余量
            per_add = min(_PER_ADD_CAP, need / len(extensible))
            added_any = False
            for s in extensible:
                cur = _shot_dur(s)
                new_d = min(per_shot_cap, cur + per_add)
                if new_d - cur <= 0.1:
                    continue
                aligned = min(_align_frame_duration(new_d, fps), per_shot_cap)
                if aligned <= cur:
                    continue
                before_dur = s.get("duration_sec")
                s["duration_sec"] = aligned
                s["duration"] = aligned
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=s.get("index"),
                        action="clamp",
                        target="duration_sec",
                        before=str(before_dur),
                        after=str(aligned),
                    )
                )
                added_any = True
            total = _sum_duration(ctx.shots)
            if not added_any:
                break

        # 4) 全部顶到 cap 仍 < 60s 硬下限 → 镜头数不够，记 HARD Violation 走 regen
        #    （8s × N < 60 即 N < 8：加镜头才能解决，继续拉单镜时长违反 8s 上限）
        if total < _REDGUO_MIN_SEC:
            max_reach = len(ctx.shots) * per_shot_cap
            ctx.record_violation(
                Violation(
                    constraint_name=self.name,
                    severity=Severity.HARD,
                    shot_index=None,
                    field=None,
                    message=(
                        f"正片分镜总时长 {total:.1f}s < {_REDGUO_MIN_SEC:.0f}s 硬下限（不含钩子，钩子另算）。"
                        f"当前 {len(ctx.shots)} 镜即使全部顶到 {per_shot_cap:.0f}s/镜 也只有 {max_reach:.0f}s——"
                        f"必须**增加镜头数**（拆分关键动作/补反应镜，单镜 ≤ {per_shot_cap:.0f}s），"
                        f"禁止靠拉长单镜时长凑数。"
                    ),
                )
            )

        # 5) timecode 同步（不论是否拉伸）
        _sync_timecode(ctx.shots, ctx.hook, ctx.script if isinstance(ctx.script, dict) else None, fps)
        return repairs
