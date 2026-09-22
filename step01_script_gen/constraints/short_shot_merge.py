"""F9. 短镜检测（v6.49 新增，08-28 跑批实测）.

08-28 chapter_001 跑批实测 1 处短镜告警：
  镜头 4 dur=3.0s 太短（红果末镜情绪渲染下限 4s + W6 已强制末镜 ≥ 4s，
  但中间转场镜 3.0s 仍存在）。

## v6.79（2026-09-20）：停止「拉长到 4s」的写入

原实现在 `apply()` 里直接把 `duration_sec` 拉长到 3.5s，并记 `repaired=True`。
问题是**加秒数不是修复**：

  - 它发生在 KF 时间检查（`kf_time_window_alignment` / `kf_t_sec_bounds` /
    `kf_monotonic`，注册序号 8-11）**之后**（本约束序号 57），于是「审计通过的
    时长」不再等于「最终时长」；同一 run 里同一个不变量被检查了一次就不再成立。
  - 短镜的成因是**内容不足**（动作/台词撑不满），把时长填到 3.5s 只会得到
    一个更慢的空镜，而不是更完整的一镜。
  - 拉长还会撤销 `runtime_extend` 之前的单镜 cap 夹取。

现在只报告：HARD 违例 + 未改写记录，交完整镜头 regen 合并到相邻镜或补内容。
blocking 判定在 `quality_issues._shot_duration_issues`（gate 侧），两处共用
`_MIN_SHOT_DURATION` 阈值。

幂等：纯检测，无副作用。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity, Violation

MIN_SHOT_DURATION_SEC = 3.5  # 短镜阈值（v6.49 红果下限 4s - 0.5s 安全裕度）
_MIN_SHOT_DURATION = MIN_SHOT_DURATION_SEC  # 向后兼容的历史名字


class ShortShotMergeConstraint(Constraint):
    """F9. 短镜检测：duration_sec < 3.5s 且非末镜 → HARD 报告（不改写时长）."""

    name = "short_shot_merge"
    severity = Severity.HARD
    fields = ()

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        if not isinstance(ctx.shots, list) or len(ctx.shots) < 2:
            return repairs

        for shot in ctx.shots[:-1]:  # 跳过末镜（W6 单独保证末镜 ≥ 4s）
            if not isinstance(shot, dict):
                continue
            try:
                dur = float(shot.get("duration_sec", 0) or 0)
            except (TypeError, ValueError):
                continue
            if dur >= _MIN_SHOT_DURATION:
                continue
            idx = shot.get("index")
            ctx.record_violation(
                Violation(
                    constraint_name=self.name,
                    severity=Severity.HARD,
                    shot_index=idx,
                    field="duration_sec",
                    repaired=False,
                    message=(
                        f"短镜 {dur:.1f}s < {_MIN_SHOT_DURATION}s 红果下限。"
                        "本约束**不再拉长时长**（加秒数只得到更慢的空镜，且发生在 "
                        "KF 时间检查之后，会让已审计的时长失效）——"
                        "请合并到相邻镜或补充 motion_desc/台词，由完整镜头 regen 处理。"
                    ),
                    evidence=f"duration_sec={dur}",
                )
            )
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=idx,
                    action="detect",
                    target="duration_sec",
                    before=str(dur),
                    after="(HARD, 未改写；需合并或补内容后完整 regen)",
                )
            )
        return repairs
