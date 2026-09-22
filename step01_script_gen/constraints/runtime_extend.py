"""W6. 红果时长约束（v6.42 迁移自 _hard_fix_runtime_extend；v6.76 只报告）.

08-23 用户规则（本版核心）：
  - **正片分镜总时长（不含钩子）≥ 60s**——钩子时长另算，不计入 60s 下限
  - **单镜时长 ≤ 8s**（MAX_SHOT_DURATION，与视频模型 cap 取严）
  - 钩子时长另算：不受 8s 上限/60s 下限约束（hook 有自己的下限规则）

历史背景：8-22 跑批实测 8 镜总时长 42.25s < 60s 红果下限，末镜 3.04s < 4s 红果下限。

v6.76（2026-09-19）降为**只报告**，不再以任何方式**增加**时长。原因：
  1. 拉伸只改 `duration_sec`，不增加任何有原文证据的事件——新增的秒数没有内容。
     而系统里唯一设计来「时长变了同步 KF 时刻」的机制（`shared/kf_time.py:95-103`
     的惰性重标定）实测**从不触发**（`duration` 与 `duration_sec` 恒等，ratio≡1.0），
     于是 kf 仍停在原绝对秒数，尾部成为「无 kf 覆盖的时段」，
     由 kf_time_window_alignment 补出「（画面延续，保持该状态）」伪 hold 帧。
  2. 实测证据：`chapter_001/index 12` duration_sec=7.88，kf4 t=7.088 即此类伪帧。
  3. 该文件自身 60-62 行的旧注释已承认根因：「拉伸比高的根因几乎总是**内容不足**
     （镜头没覆盖原文高潮），正确解法是补镜头而非调拉伸算法」——
     本版把这条判断从注释变成行为。

**保留**的动作：单镜 > cap 的夹取（步骤 0）。那是「减时长」防 provider 截断内容，
不是造时长；且是模型能力约束。**移出**的动作：末镜夹取（步骤 1）与按比例拉伸
（步骤 2/3）——两者都在无内容支撑的情况下增加时长，改为报告。

现状：
  - 单镜 > cap → 夹到 cap（保留，action="clamp"）
  - 末镜 ≤ 4s → 报告（不再夹到 5s）
  - 正片总时长 < 60s → 报告 + HARD Violation（建议**增加镜头数**，禁止拉单镜凑数）
  - timecode 永远与 `duration_sec` 同步（保留）

幂等：只报告 + 夹取（夹后不再 >cap），连跑两次结果一致。
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

_LAST_SHOT_MIN_SEC = 4.0    # 红果末镜下限（现仅作报告判据）


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
    """timecode 永远和 shot["duration"] 同步（不论是否修改都跑）.

    钩子在播放顺序头部：hook.timecode = 0..hook_duration，shots 从 hook_duration
    开始累加。timeline[] 数组**不再重建**——_compute_timecodes 已按 hook-first
    建好正确顺序，下游（step04/03/02b）不读 timeline。
    """
    if script is None:
        return
    cumulative = 0.0
    hook_dur = 0.0
    if hook and isinstance(hook, dict):
        hook_dur = _shot_dur(hook)
        hook["timecode"] = {
            "start": 0.0,
            "end": round(hook_dur, 2),
        }
        cumulative = hook_dur
    for s in shots:
        if not isinstance(s, dict):
            continue
        d = _shot_dur(s)
        s["timecode"] = {
            "start": round(cumulative, 2),
            "end": round(cumulative + d, 2),
        }
        cumulative += d
    script["total_duration"] = round(cumulative, 2)
    if "total_effective_duration" in script:
        script["total_effective_duration"] = round(cumulative, 2)


class RuntimeExtendConstraint(Constraint):
    """W6. 红果时长约束：单镜 ≤ 8s（夹取）/ 正片 ≥ 60s 与末镜 > 4s（只报告）.

    severity=HARD: 60s 红果标准是硬约束（structural）。
    skip_hook=True: 钩子时长另算——不进总时长统计、不受 8s 上限约束。
    """

    name = "runtime_extend"
    severity = Severity.HARD
    skip_hook = True

    def _report(
        self, ctx, repairs: list[RepairRecord], shot_index, *, target: str,
        message: str, evidence: str, detail: str, severity=None,
    ) -> None:
        ctx.record_violation(Violation(
            constraint_name=self.name,
            severity=severity or Severity.WARN,
            shot_index=shot_index,
            field=target,
            message=message,
            evidence=evidence[:200],
            repaired=False,
        ))
        repairs.append(RepairRecord(
            constraint_name=self.name,
            shot_index=shot_index,
            action="warn",
            target=target,
            before=evidence[:80],
            after=evidence[:80],
            detail=detail,
        ))

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        if not isinstance(ctx.shots, list) or not ctx.shots:
            return repairs

        # 单镜上限 = min(视频模型 cap, 8s 硬限)（08-23 用户规则）
        try:
            from step01_script_gen.timecode import active_shot_cap as _asc
            per_shot_cap = _asc()
        except Exception:
            per_shot_cap = min(_video_duration_cap(), _MAX_SHOT_SEC)
        fps = 24.0
        if isinstance(ctx.script, dict):
            try:
                fps = float(ctx.script.get("fps", 24) or 24)
            except (TypeError, ValueError):
                fps = 24.0

        # 0) 单镜硬上限：**只报告，不夹取**（v6.83，二次审核发现 1）
        #
        # 原实现把超 cap 的 `duration` / `duration_sec` 直接缩短，理由是「减时长
        # 防 provider 截断内容，不是造时长」。但**缩短时长会改变所有时间坐标的
        # 合法域**：本约束位于 `kf_time_window_alignment` / `kf_t_sec_bounds` /
        # `kf_monotonic` **之后**，夹取一旦发生，链前那些检查的结论对最终对象
        # 就不再成立——实测 12s 镜头夹成 8s 后，`t_sec=10` 的 KF 留在最终对象里，
        # 且当时的 quality gate 不检查 `t_sec <= duration_sec`，越界帧可以静默
        # 落盘。这正是本审计的原始根因（链后写入使链前结论失效）。
        #
        # 正确处置：cap 夹取属**时长派生**，只能发生在所有 KF 生成与检查之前。
        # `timecode._compute_timecodes` 已在权威派生点按 `min(模型 cap, 8s)` 夹取
        # 并打 WARN；此处若仍发现超 cap，说明有写入者在本约束之前改过时长——
        # 报 HARD 交完整镜头 regen（拆镜/重写），不再由链尾静默压缩。
        for s in ctx.shots:
            if not isinstance(s, dict):
                continue
            d = _shot_dur(s)
            if d > per_shot_cap:
                self._report(
                    ctx, repairs, s.get("index"),
                    target="duration_sec",
                    message=(
                        f"镜长 {d:.2f}s > 单镜上限 {per_shot_cap:.2f}s"
                        f"（min(模型 {_video_duration_cap():g}s, {_MAX_SHOT_SEC:g}s)）；"
                        "本约束不再夹取——夹取会改变 KF 时间坐标的合法域，必须在"
                        "KF 生成/检查之前完成（拆镜或整镜 regen）"
                    ),
                    evidence=f"duration_sec={d:.3f} cap={per_shot_cap:.3f}",
                    detail="链尾夹取已停用（v6.83，二次审核发现 1）：cap 夹取属时长派生",
                    severity=Severity.HARD,
                )

        # 1) 末镜时长下限 —— 只报告（v6.76：不再夹到 5s，那会凭空加 1~2s 无内容时长）
        last = ctx.shots[-1]
        if isinstance(last, dict):
            last_dur = _shot_dur(last)
            if 0 < last_dur <= _LAST_SHOT_MIN_SEC:
                self._report(
                    ctx, repairs, last.get("index", len(ctx.shots) - 1),
                    target="duration_sec",
                    message=(
                        f"末镜时长 {last_dur:.2f}s ≤ 红果下限 {_LAST_SHOT_MIN_SEC:.0f}s；"
                        "保留原值未夹取——补齐末镜应补充有原文依据的收束内容，"
                        "而不是给现有画面加无内容时长。"
                    ),
                    evidence=f"末镜 duration_sec={last_dur:.2f}",
                    detail="末镜时长不足红果下限，保留原值交由 audit/regen",
                )

        # 2) 正片总时长（只算分镜，钩子另算）
        total = _sum_duration(ctx.shots)

        # 3) 总时长不足 —— 只报告（v6.76：不再按比例拉伸；拉伸＝造无内容时长）
        #    HARD：总时长是平台硬约束，未解决需走补镜 regen。
        #    注意只记这一条——历史上步骤 3 与 4 会各记一条，同一条件下重复。
        if total < _REDGUO_MIN_SEC:
            max_reach = len(ctx.shots) * per_shot_cap
            if max_reach >= _REDGUO_MIN_SEC:
                _hint = (
                    f"当前 {len(ctx.shots)} 镜即使全部顶到 {per_shot_cap:.0f}s/镜 "
                    f"也只有 {max_reach:.0f}s，可通过**补镜头/补内容**达标"
                    "（拆分关键动作，或补有原文依据的反应镜/建立镜）"
                )
            else:
                _hint = (
                    f"当前 {len(ctx.shots)} 镜即使全部顶到 {per_shot_cap:.0f}s/镜 也只有 "
                    f"{max_reach:.0f}s——必须**增加镜头数**（拆分关键动作/补反应镜，"
                    f"单镜 ≤ {per_shot_cap:.0f}s），禁止靠拉长单镜时长凑数"
                )
            self._report(
                ctx, repairs, None,
                target="duration_sec",
                message=(
                    f"正片分镜总时长 {total:.1f}s < {_REDGUO_MIN_SEC:.0f}s 硬下限"
                    f"（不含钩子，钩子另算）；保留原值未拉伸——"
                    "拉伸只增时长不增事件，会让尾部变成无 KF 覆盖的伪时段"
                    "（被下游补成「画面延续」假帧）。" + _hint + "。"
                ),
                evidence=f"shots_total={total:.2f}s, {len(ctx.shots)} 镜",
                detail="正片总时长不足红果下限，保留原值交由 audit/regen",
                severity=Severity.HARD,
            )

        # 4) timecode 同步（不论是否修改）
        _sync_timecode(ctx.shots, ctx.hook, ctx.script if isinstance(ctx.script, dict) else None, fps)
        return repairs
