"""约束系统的核心数据类型.

三个核心 dataclass + 1 个 Severity enum:
  - Violation: 检测到的违例（不一定被修复）
  - RepairRecord: 实际执行的修复记录（用于日志 + 回归测试断言）
  - RepairReport: 一次 apply_constraints 调用的总结果

Severity 决定违例的"严重度"——HARD 未修复则触发 regen（替代旧的
hard_fail_checks 白名单 line 3882）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Severity(Enum):
    """约束违例严重度.

    INFO  = 仅记录，不上报
    WARN  = 报告给 repair feedback（LLM 下一轮可修复）
    HARD  = 报告 + 未修复则触发 regen
    """

    INFO = 1
    WARN = 2
    HARD = 3


@dataclass(frozen=True)
class Violation:
    """检测到的违例.

    Attributes:
        constraint_name: 哪个约束检测出来的（对应 Constraint.name）
        severity: 严重度
        shot_index: 镜头 index（None = script-level 或 hook）
        field: 违例字段名（'prompt' / 'ff_desc' / 'keyframes[2].desc' / None）
        message: 人/给 LLM 看的描述
        evidence: 触发违例的原文片段（用于日志）
    """

    constraint_name: str
    severity: Severity
    shot_index: Optional[int] = None
    field: Optional[str] = None
    message: str = ""
    evidence: str = ""


@dataclass(frozen=True)
class RepairRecord:
    """实际执行的修复记录.

    Attributes:
        constraint_name: 哪个约束做了修复
        shot_index: 镜头 index（None = script-level 或 hook）
        action: 动作类型（'append' / 'rewrite' / 'set_field' / 'split_shot' / 'clamp'）
        target: 修复目标（'prompt' / 'ff_desc' / 'lf_desc' / 'keyframes[N].desc'）
        before: 修改前文本（便于回归测试断言）
        after: 修改后文本
        detail: 修复说明（2026-09-03 现场压测补：约束代码侧普遍传 detail=... 报
            "__init__() got an unexpected keyword argument 'detail'"；与 violation.detail
            同语义，供回放/日志使用）
    """

    constraint_name: str
    shot_index: Optional[int]
    action: str
    target: str
    before: str = ""
    after: str = ""
    detail: str = ""


@dataclass
class RepairReport:
    """一次 apply_constraints 调用的总结果.

    Attributes:
        repairs: 实际执行的修复列表
        violations_remaining: 检测到但未修复的违例（含修复失败）
        duration_ms: 耗时（毫秒）
    """

    repairs: list[RepairRecord] = field(default_factory=list)
    violations_remaining: list[Violation] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def hard_unresolved(self) -> list[Violation]:
        """HARD severity 且未修复的违例（触发 regen）."""
        return [v for v in self.violations_remaining if v.severity == Severity.HARD]

    @property
    def repairs_by_constraint(self) -> dict[str, int]:
        """每个约束的修复次数（用于日志汇总）."""
        out: dict[str, int] = {}
        for r in self.repairs:
            out[r.constraint_name] = out.get(r.constraint_name, 0) + 1
        return out
