"""K. 开篇冲突评阅提示（2026-10-28 修订）.

前 1/3 镜（=max(1, len//3)）都不含 `_PUNCH_WORDS` 词 → 只产生一条**非阻塞**评阅提示。

**不再改写 prompt**：旧实现检测不到爆点词就往第一镜 prompt 追加"危机一触即发，
暗流涌动，惊觉对峙"，会把用户输入的视觉意图改成固定情绪（实测把黄昏吹箫的闲静
画面改成紧张对峙）。评阅能力保留，措辞注入删除；用户原本写入的情绪表达原样保留。

幂等：只读不改，重复执行不会累积文字；提示记录可重复出现（它不改变稿件）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.validation import _PUNCH_WORDS
from .base import Constraint
from .types import RepairRecord, Severity, Violation


class OpeningHookConstraint(Constraint):
    """K. 开篇冲突：前 1/3 段缺爆点词 → **只提示，不改写**.

    severity=WARN：非阻塞评阅信号，不触发 regen，也不进入硬门。
    不再设置 repair_marker：没有"已修复"状态，因为这里不修改任何字段。
    """

    name = "opening_hook"
    severity = Severity.WARN
    repair_marker = None
    fields = ("prompt",)

    def apply(self, ctx) -> list[RepairRecord]:
        shots = ctx.shots
        if not shots or not isinstance(shots, list):
            return []
        head = shots[: max(1, len(shots) // 3)]
        head_text = " ".join(
            s.get("prompt", "") + s.get("main_subject", "") + s.get("motion_desc", "")
            for s in head if isinstance(s, dict)
        )
        if any(p in head_text for p in _PUNCH_WORDS):
            return []
        s = shots[0]
        if not isinstance(s, dict):
            return []
        prompt = s.get("prompt", "") or ""
        index = s.get("index", 0)
        ctx.record_violation(Violation(
            constraint_name=self.name,
            severity=self.severity,
            shot_index=index,
            field="prompt",
            message="开篇前 1/3 镜未出现爆点词（非阻塞评阅提示；不改写 prompt）",
            evidence=str(prompt)[:120],
        ))
        # action="warn" 是既有约定：run_hard_fix 会把它打到日志，且不进入修复事务。
        return [RepairRecord(
            constraint_name=self.name,
            shot_index=index,
            action="warn",
            target="prompt",
            before=prompt,
            after=prompt,
            detail="前 1/3 镜缺少爆点词：仅提示，不注入固定情绪措辞",
        )]
