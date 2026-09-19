"""F1. kf t_sec 单调性约束（v6.43 新增，08-23 审计；v6.76 降为只报告）.

8-23 跑批实测：
  - shot4 两 kf 同 t=2.0（duration 4s）→ 后段动作不展开
  - shot5 climax t=3.5 > after t=3.0 → 物理时刻倒装

历史修法（v6.43~v6.73）：检测降序对 → 把全部 kf 重新均匀分布在 (0.1s, dur×0.9)。

v6.76（2026-09-19）降为**只报告**，不再重排。原因：
  1. 「升序」不是充分条件——按列表顺序均匀铺开，无法证明重排后每个 kf
     仍对应用它 desc 描述的那个事件（描述与时刻脱钩）。
  2. 本约束跑在链中段，其后的 kf_t_sec_bounds / kf_timeline_normalize /
     _KfTimeWindowAlignmentFinal（v6.76 已删除） 会再次移动同一批帧，使本约束的修复不保持。
     实测：同一脚本连跑两次 run_hard_fix 结果不同
     （tests/.../test_full_hardfix_kf_idempotency.py 当前失败）。
  3. 时间归属只能由事件结构决定，不能由「列表顺序」这一与语义无关的量决定。

现状：检测降序对 → record_violation(repaired=False) + RepairRecord(action="warn")，
保留原值交 audit / regen。N=1 跳过；duration_sec ≤ 0 跳过；已单调跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.kf_time import get_kf_t_sec
from .base import Constraint
from .types import RepairRecord, Severity, Violation


class KfMonotonicConstraint(Constraint):
    """F1. kf t_sec 升序：只检测并上报，不重排（v6.76 起）.

    severity=WARN: 单调性问题交 repair round / regen 处理.
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
            for kf in kfs:
                if not isinstance(kf, dict):
                    continue
                t = get_kf_t_sec(kf, shot)
                if t is None:
                    continue
                ts.append(t)
            if len(ts) < 2:
                continue
            # 检测是否有降序
            descending = any(ts[i] > ts[i + 1] for i in range(len(ts) - 1))
            if not descending:
                continue
            # v6.76（09-19）：不再重排。「升序」无法证明重排后 kf 仍归属于
            # 其 desc 所述事件；且本约束跑在链中段，后续 kf_t_sec_bounds /
            # kf_timeline_normalize / _KfTimeWindowAlignmentFinal（v6.76 已删除） 会再次移动
            # 同一批帧，使修复不保持（连跑两次结果不同）。保留原值，交 regen。
            _ts_text = ", ".join(f"{t:.3f}" for t in ts)
            _desc_pairs = ", ".join(
                f"kf{i}={ts[i]:.3f}s > kf{i + 1}={ts[i + 1]:.3f}s"
                for i in range(len(ts) - 1)
                if ts[i] > ts[i + 1]
            )
            ctx.record_violation(Violation(
                constraint_name=self.name,
                severity=Severity.WARN,
                shot_index=idx,
                field="keyframes[*].t_sec",
                message=(
                    f"关键帧时间未严格递增（{_desc_pairs}）；保留原值未重排——"
                    "按列表顺序铺开无法证明 kf 与其 desc 所述事件的对应关系，"
                    "交 regen 按事件结构重写该镜。"
                ),
                evidence=_ts_text,
                repaired=False,
            ))
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=idx,
                    action="warn",
                    target="keyframes[*].t_sec",
                    before=_ts_text,
                    after=_ts_text,
                    detail="存在降序关键帧，保留原值交由 audit/regen",
                )
            )
        return repairs
