"""prompt_semantic.fixer — 把 Violation 应用到 raw_text 上.

幂等：第二次跑应无新 violation（因为违规已被修复）。
按 priority 降序应用（高优先级先改），避免低优先级 find_pattern 被高优先级改坏。
"""
from __future__ import annotations

from .entities import EntityGraph
from .physics import Violation


def apply_fixes(graph: EntityGraph, violations: list[Violation]) -> str:
    """按 priority 降序把 violations 应用到 raw_text.

    每条 Violation 一次 find/replace（replace 为空 = 删除）.
    """
    text = graph.raw_text
    if not violations:
        return text
    sorted_viols = sorted(violations, key=lambda v: v.priority, reverse=True)
    seen_find: set[str] = set()
    for v in sorted_viols:
        if not v.find_pattern:
            continue
        if v.find_pattern in seen_find:
            continue  # 同 find_pattern 已处理过（多 violation 命中同一段）
        seen_find.add(v.find_pattern)
        # 1 次替换（避免误伤）
        if v.find_pattern in text:
            text = text.replace(v.find_pattern, v.replace, 1)
    return text


__all__ = ["apply_fixes"]
