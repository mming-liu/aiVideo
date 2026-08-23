"""O. 首镜头 AI 生成标注约束（v6.42 迁移自 _hard_fix_platform_compliance）.

index==1 的 shot.prompt 末尾追加"（AI生成）"标注（仅追加 1 次）。

幂等：已含任一 AI 关键词（AI生成/AI创作/人工智能/AIGC）则跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


_AI_KEYWORDS = ("AI生成", "AI创作", "人工智能", "AIGC")
_TAIL = "。（AI生成）"


class PlatformComplianceConstraint(Constraint):
    """O. 首镜头 AI 生成标注：index==1 缺标注 → 追加'（AI生成）'.

    severity=WARN: 平台合规可由后续 repair round 修.
    repair_marker="AI生成".
    """

    name = "platform_compliance"
    severity = Severity.WARN
    repair_marker = "AI生成"
    fields = ("prompt",)
    skip_hook = True  # 仅作用于 shot 1

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = ctx.shots
        if not shots or not isinstance(shots, list):
            return repairs
        s = shots[0]
        if not isinstance(s, dict):
            return repairs
        if s.get("index", 0) != 1:
            return repairs
        prompt = s.get("prompt", "") or ""
        if any(kw in prompt for kw in _AI_KEYWORDS):
            return repairs
        before = prompt
        s["prompt"] = prompt.rstrip("。") + _TAIL
        repairs.append(
            RepairRecord(
                constraint_name=self.name,
                shot_index=s.get("index", 0),
                action="append",
                target="prompt",
                before=before,
                after=s["prompt"],
            )
        )
        return repairs
