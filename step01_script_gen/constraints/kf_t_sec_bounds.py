"""F. kf t_sec 越界约束（v6.42 迁移自 _hard_fix_kf_t_sec_bounds；v6.76 降为只报告）.

检测两类问题：
  1. 越界：t > dur × 0.9，或 t < 0.1 且 dur > 0.2
  2. 全镜同值塌缩：所有 kf 共享同一 t_sec（LLM 把一镜内所有 kf 写死同一时刻）

历史修法（v6.42~v6.73）：
  - 越界 → v6.47 起按 stage 语义位置推导（pre=0.10×dur … after=0.90×dur），
    更早是一刀切 clamp 到 0.9×dur
  - 塌缩 → 按 stage 顺序把 kf 摊到 [0.1, 0.9]×dur

v6.76（2026-09-19）降为**只报告**，不再改写。原因：
  1. stage 是语义标签，不是时刻证据。用 `dur × frac` 造出的时刻没有任何
     原文依据，等于把推断写成事实；越界本身说明该镜的时间归属不可信。
  2. 本约束跑在链中段，其后的 kf_timeline_normalize / _KfTimeWindowAlignmentFinal（v6.76 已删除）
     会再次移动同一批帧，修复不保持。
  3. 时刻位置只能由事件结构决定，不能由 stage 标签或列表顺序决定。
     见 docs/step01-temporal-writers-audit-2026-09-19.md。

现状：检测到即 record_violation(repaired=False) + RepairRecord(action="warn")，
保留原值交 audit / regen。
"""
from __future__ import annotations

from shared.kf_time import get_kf_t_sec
from .base import Constraint
from .types import RepairRecord, Severity, Violation

# 全镜同值判定阈值（秒）：kf t_sec 两两差 < ε 视为「塌缩」（LLM 写死同一个时间）
_COLLAPSE_EPS = 0.01


class KfTSecBoundsConstraint(Constraint):
    """F. kf t_sec 越界 / 全镜同值塌缩：只检测并上报（v6.76 起）."""

    name = "kf_t_sec_bounds"
    severity = Severity.WARN
    fields = ()

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            try:
                dur = float(shot.get("duration_sec") or shot.get("duration") or 0)
            except (TypeError, ValueError):
                dur = 0
            if dur <= 0:
                continue
            kfs = shot.get("keyframes") or []
            if not isinstance(kfs, list):
                continue

            # --- 1) 越界 ---
            oob: list[tuple[int, float]] = []
            for j, kf in enumerate(kfs):
                if not isinstance(kf, dict):
                    continue
                t = get_kf_t_sec(kf, shot)
                if t is None:
                    continue
                if t > dur * 0.9 or (t < 0.1 and dur > 0.2):
                    oob.append((j, t))
            if oob:
                _txt = ", ".join(f"keyframes[{j}]={t:.3f}s" for j, t in oob)
                self._report(
                    ctx, repairs, idx, _txt,
                    message=(
                        f"{len(oob)} 个关键帧时刻越界（{_txt}，镜长 {dur:.2f}s）；"
                        "保留原值未按 stage 推导——stage 是语义标签而非时刻证据，"
                        "越界说明该镜时间归属不可信，交 regen 重写。"
                    ),
                    detail="关键帧时刻越界，保留原值交由 audit/regen",
                )

            # --- 2) 全镜同值塌缩 ---
            self._report_collapsed(ctx, kfs, shot, dur, idx, repairs)
        return repairs

    def _report(
        self, ctx, repairs: list[RepairRecord], idx: int, evidence: str,
        *, message: str, detail: str,
    ) -> None:
        """统一的「只报告」出口：一条 violation + 一条 warn RepairRecord."""
        ctx.record_violation(Violation(
            constraint_name=self.name,
            severity=Severity.WARN,
            shot_index=idx,
            field="keyframes[*].t_sec",
            message=message,
            evidence=evidence,
            repaired=False,
        ))
        repairs.append(RepairRecord(
            constraint_name=self.name,
            shot_index=idx,
            action="warn",
            target="keyframes[*].t_sec",
            before=evidence,
            after=evidence,
            detail=detail,
        ))

    def _report_collapsed(
        self, ctx, kfs: list, shot: dict, dur: float, idx: int,
        repairs: list[RepairRecord],
    ) -> None:
        """全镜 kf 共享同一 t_sec 时上报（v6.76 起不再按 stage 摊开）."""
        ts = [get_kf_t_sec(kf, shot) for kf in kfs if isinstance(kf, dict)]
        if len(ts) < 2 or any(t is None for t in ts):
            return
        if not all(abs(t - ts[0]) < _COLLAPSE_EPS for t in ts):
            return  # 时刻已分散，无需上报
        stages = {kf.get("stage", "") for kf in kfs if isinstance(kf, dict)}
        if len(stages) < 2:
            return  # 同 stage 重复/未知 → 不视为塌缩
        self._report(
            ctx, repairs, idx,
            f"t_sec 全部={ts[0]:.3f}s（{len(ts)} 帧，镜长 {dur:.2f}s）",
            message=(
                f"全镜 {len(ts)} 个关键帧共享同一时刻 t={ts[0]:.3f}s（镜长 {dur:.2f}s），"
                f"但 stage 有 {len(stages)} 种；保留原值未按 stage 摊开——"
                "摊开位置无原文依据，交 regen 重写该镜时间结构。"
            ),
            detail="全镜关键帧时刻塌缩，保留原值交由 audit/regen",
        )
