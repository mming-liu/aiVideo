"""约束冲突检测（v6.42 PoC）.

目标：发现"A 加锚词 / B 剥离锚词"的循环依赖和其他简单冲突模式。

检测范围（PoC）：
  1. duplicate_append: 两个约束的 append_text 完全相同
  2. marker_in_append: 某约束的 repair_marker 出现在另一约束的 append_text 里
  3. known_conflicts: 手工注册的已知冲突对（修复后清理）

设计原则：
  - 只检测 + 报告，不自动 fix
  - 一次扫描，结果可缓存（ALL_CONSTRAINTS 启动时定）
  - 输出 Conflict dataclass 列表
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


@dataclass(frozen=True)
class Conflict:
    """一对冲突约束的描述.

    Attributes:
        kind: 冲突类型（'duplicate_append' / 'marker_in_append' / 'known'）
        constraint_a: 约束 A 名（先执行）
        constraint_b: 约束 B 名（后执行）
        description: 人类可读的描述
    """

    kind: str
    constraint_a: str
    constraint_b: str
    description: str


# 手工注册的已知冲突（按 v6.41+ 跑批观测）
# 元组: (A名, B名, 描述)
_KNOWN_CONFLICTS: List[Tuple[str, str, str]] = [
    # 举例：narrative_strip 可能剥掉 boat_crop 留下的"船头"——已知无冲突，记为空
    # 真实冲突示例（按需添加）：
    # ("gaze_direction", "injury_continuity",
    #  "gaze_direction 追加'目光紧盯'，injury_continuity 在结尾追加'伤处白袍暗红洇痕'，次序应 gaze 先 / injury 后，否则 gaze 写入的位置会被 injury 锚词覆盖"),
]


def _get_append_text(c) -> Optional[str]:
    """从 constraint 提取 append_text（兼容 SimpleTextAppendConstraint + 自定义）."""
    # SimpleTextAppendConstraint
    t = getattr(c, "append_text", None)
    if t:
        return str(t)
    # 一些 constraint 在 apply 里硬编码追加，可通过已知名查
    return None


def _get_repair_marker(c) -> Optional[str]:
    m = getattr(c, "repair_marker", None)
    return str(m) if m else None


def detect_conflicts(constraints: list) -> List[Conflict]:
    """扫所有约束，输出冲突列表.

    Args:
        constraints: Constraint 实例列表（通常是 ALL_CONSTRAINTS）

    Returns:
        Conflict 列表（可能为空）
    """
    conflicts: List[Conflict] = []
    if not constraints:
        return conflicts

    # 索引：append_text → [(name, append_text)]
    by_append: dict = {}
    for c in constraints:
        name = getattr(c, "name", "") or ""
        if not name:
            continue
        t = _get_append_text(c)
        if t:
            by_append.setdefault(t, []).append(name)

    # 索引：repair_marker → [name]
    by_marker: dict = {}
    for c in constraints:
        name = getattr(c, "name", "") or ""
        if not name:
            continue
        m = _get_repair_marker(c)
        if m:
            by_marker.setdefault(m, []).append(name)

    # 检测 1: duplicate_append
    for txt, names in by_append.items():
        if len(names) > 1:
            # 多个约束追加相同文本 → 重复执行
            for i in range(len(names)):
                for j in range(i + 1, len(names)):
                    conflicts.append(Conflict(
                        kind="duplicate_append",
                        constraint_a=names[i],
                        constraint_b=names[j],
                        description=f"两个约束都追加相同文本 {txt!r}，会重复添加",
                    ))

    # 检测 2: marker_in_append（A 的 repair_marker 是 B 的 append_text 子串）
    for marker, m_names in by_marker.items():
        for txt, a_names in by_append.items():
            if marker in txt:
                for a in a_names:
                    for m in m_names:
                        if a != m:
                            conflicts.append(Conflict(
                                kind="marker_in_append",
                                constraint_a=m,
                                constraint_b=a,
                                description=f"{m} 的幂等锚词 {marker!r} 是 {a} 追加文本 {txt!r} 的子串；B 执行后 A 会判定'已修'跳过",
                            ))

    # 检测 3: known_conflicts
    for a, b, desc in _KNOWN_CONFLICTS:
        names = {getattr(c, "name", "") for c in constraints}
        if a in names and b in names:
            conflicts.append(Conflict(
                kind="known",
                constraint_a=a,
                constraint_b=b,
                description=desc,
            ))

    return conflicts


# ============================================================================
# 与 engine 集成
# ============================================================================

def detect_and_log(constraints: list) -> List[Conflict]:
    """检测 + 写日志. 返回冲突列表（不阻断）."""
    conflicts = detect_conflicts(constraints)
    if not conflicts:
        return conflicts
    try:
        from shared.tee_logger import get_logger
        log = get_logger()
        log.warn(
            f"[CONFLICT] 检测到 {len(conflicts)} 对约束冲突（PoC，只警告不阻断）:"
        )
        for c in conflicts[:10]:  # 最多 10 条，避免日志爆炸
            log.warn(
                f"  - {c.kind}: {c.constraint_a} ↔ {c.constraint_b}: {c.description}"
            )
        if len(conflicts) > 10:
            log.warn(f"  ... 还有 {len(conflicts) - 10} 条未列出")
    except Exception:
        pass
    return conflicts
