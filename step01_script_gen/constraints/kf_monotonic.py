"""F1. kf t_sec 单调性约束（v6.43 新增，08-23 审计）.

8-23 跑批实测：
  - shot4 两 kf 同 t=2.0（duration 4s）→ 后段动作不展开
  - shot5 climax t=3.5 > after t=3.0 → 物理时刻倒装

修法：检测关键帧序列 t_sec 是否升序，若有降序对 → 重新均匀分布在
(0.1s, dur×0.9) 区间内。N=1 跳过；duration_sec ≤ 0 跳过；已单调跳过。

幂等：均匀分布后再次检测仍单调，不破坏。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.kf_time import get_kf_t_sec, set_kf_t_sec
from .base import Constraint
from .types import RepairRecord, Severity


class KfMonotonicConstraint(Constraint):
    """F1. kf t_sec 升序：检测降序对 → 重新均匀分布.

    severity=WARN: 单调性可由后续 repair round 修.
    无 repair_marker（按 t 实际值检测）.
    """

    name = "kf_monotonic"
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
            if not isinstance(kfs, list) or len(kfs) < 2:
                continue
            # 读取每个 kf 的 t_sec（get_kf_t_sec 已做惰性重标定）
            ts: list[float] = []
            valid: list[int] = []
            for j, kf in enumerate(kfs):
                if not isinstance(kf, dict):
                    continue
                t = get_kf_t_sec(kf, shot)
                if t is None:
                    continue
                ts.append(t)
                valid.append(j)
            if len(ts) < 2:
                continue
            # 检测是否有降序
            descending = any(ts[i] > ts[i + 1] for i in range(len(ts) - 1))
            if not descending:
                continue
            # 重新均匀分布：N 帧 → 第 i 帧放 (0.1, 0.9) 区间的 1/(N+1) 等距点
            n = len(ts)
            lo = min(0.1, dur * 0.1)
            hi = dur * 0.9
            if hi <= lo:
                hi = dur * 0.8
            for rank, kf_j in enumerate(valid):
                ratio = (rank + 1) / (n + 1)  # 1/(N+1), 2/(N+1), ..., N/(N+1)
                new_t = lo + (hi - lo) * ratio
                old_t = ts[rank]
                if abs(new_t - old_t) > 0.001:
                    set_kf_t_sec(kfs[kf_j], new_t)
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx,
                            action="clamp",
                            target=f"keyframes[{kf_j}].t_sec",
                            before=f"{old_t:.3f}",
                            after=f"{new_t:.3f}",
                        )
                    )
        return repairs
