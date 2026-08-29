"""F9. 短镜合并硬修复（v6.49 新增，08-28 跑批实测）.

08-28 chapter_001 跑批实测 1 处短镜告警：
  镜头 4 dur=3.0s 太短（红果末镜情绪渲染下限 4s + W6 已强制末镜 ≥ 4s，
  但中间转场镜 3.0s 仍存在）。

修法：扫每镜 duration_sec < 3.5s 且非末镜 → 拉长到 4s。
（实际合并留给 LLM regen 反馈；本约束只标记 + 拉长。）

severity=HARD: 短镜是结构性缺陷，违反触发 regen.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity, Violation

_MIN_SHOT_DURATION = 3.5  # 短镜阈值（v6.49 红果下限 4s - 0.5s 安全裕度）


class ShortShotMergeConstraint(Constraint):
    """F9. 短镜合并：duration_sec < 3.5s → 标记合并 + 拉长到 4s."""

    name = "short_shot_merge"
    severity = Severity.HARD

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        if not isinstance(ctx.shots, list) or len(ctx.shots) < 2:
            return repairs

        for i, shot in enumerate(ctx.shots[:-1]):  # 跳过末镜
            if not isinstance(shot, dict):
                continue
            try:
                dur = float(shot.get("duration_sec", 0) or 0)
            except (TypeError, ValueError):
                continue
            if dur >= _MIN_SHOT_DURATION:
                continue
            # 短镜：拉长到 _MIN_SHOT_DURATION（保留 motion 内容）
            # 末镜已是 W6 强制 ≥ 4s；中间转场镜拉长到 4s
            new_dur = _MIN_SHOT_DURATION
            if abs(new_dur - dur) > 0.05:
                shot["duration_sec"] = new_dur
                shot["duration"] = new_dur
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=shot.get("index"),
                        action="clamp",
                        target="duration_sec",
                        before=str(dur),
                        after=str(new_dur),
                    )
                )
            # 报告 HARD 违例（让 LLM 知道这是结构性问题）
            ctx.record_violation(
                Violation(
                    constraint_name=self.name,
                    severity=Severity.HARD,
                    shot_index=shot.get("index"),
                    field="duration_sec",
                    message=(
                        f"短镜 {dur:.1f}s < {_MIN_SHOT_DURATION}s 红果下限——"
                        f"已拉长到 {new_dur:.1f}s；建议 LLM 合并到下 1 镜或补充 motion_desc。"
                    ),
                )
            )
        return repairs
