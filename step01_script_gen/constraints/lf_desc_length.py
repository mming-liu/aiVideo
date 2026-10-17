"""Report short result descriptions without fabricating content or copying ff state."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity, Violation


_EMPTY_SUBJECTS = (
    "空镜", "空景", "远景", "环境", "山峦", "山色", "夕阳", "月光",
    "荷塘", "夜幕", "雨", "雪", "风", "云", "日落", "日出", "月色",
)


class LfDescLengthConstraint(Constraint):
    """Report short result prose; never pad it with another frame's state."""

    name = "lf_desc_length"
    severity = Severity.WARN
    repair_marker = "位于"
    fields = ("lf_desc",)

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            # 幂等优先：已含 marker → 跳过（防止长 lf_desc 仍被反复 append）
            if self._already_repaired(shot):
                continue
            lf = shot.get("lf_desc", "") or ""
            if "所有角色定锚" in lf:
                continue
            threshold = 60 if is_hook else 40
            if len(lf) >= threshold:
                continue
            if not is_hook:
                ms = str(shot.get("main_subject", "") or "").strip()
                if any(es in ms for es in _EMPTY_SUBJECTS):
                    continue
            # Length alone cannot establish missing content or a later pose.
            # In particular char_layout is an ff-derived legacy projection.
            # Preserve the authored result state and let existing completeness
            # feedback request a content revision when necessary.
            ctx.record_violation(Violation(
                constraint_name=self.name, severity=Severity.WARN,
                shot_index=idx, field="lf_desc", repaired=False,
                message="尾帧描述较短；保留结果态，由内容修订补充必要信息，禁止复制其它帧姿态凑字数。",
                evidence=lf,
            ))
        return repairs
