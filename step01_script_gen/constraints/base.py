"""Constraint 抽象基类.

设计要点：
  1. 子类只重写 apply(ctx) 一个方法；其余工具方法走基类
  2. 幂等性由框架保证（_already_repaired 钩子 + repair_marker 类属性）
  3. severity 是类属性，子类无需重写 __init__
  4. 限速：fields 类属性决定要扫描的字段（默认 prompt/ff_desc/lf_desc）
  5. per-Constraint feature flag: CONSTRAINT_<NAME>_ENABLED=0 关闭单个
  6. 全局回退: HARD_FIX_USE_OLD=1 走旧 _hard_fix_* 路径

新缺陷 = 加 1 个 Constraint 子类，不写新函数。
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import ClassVar, Iterator, Optional, Tuple

from .types import RepairRecord, Severity


# 默认要扫描的文本字段
DEFAULT_FIELDS: Tuple[str, ...] = (
    "prompt",
    "ff_desc",
    "lf_desc",
    "motion_desc",
    "narration_segment",
    "subtitle",
)


class Constraint(ABC):
    """硬修复约束的抽象基类.

    使用方式：
        class MyConstraint(Constraint):
            name = "my_defect"
            severity = Severity.WARN
            repair_marker = "特定锚词"   # 字段中含此词视为已修（幂等）

            def apply(self, ctx):
                for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
                    if self._already_repaired(shot):
                        continue
                    if self._needs_repair(shot):
                        self._do_repair(shot, ctx)
                return ctx.drain_repairs()

    Attributes:
        name: 约束名（全局唯一，对应旧 _hard_fix_* 函数的小写名）
        severity: 严重度（HARD 未修触发 regen）
        fields: 要扫描的文本字段（默认 DEFAULT_FIELDS）
        repair_marker: 幂等锚词——字段含此词视为已修（默认 None）
        skip_hook: True=hook 分镜跳过此约束（默认 False）
    """

    name: ClassVar[str] = ""
    severity: ClassVar[Severity] = Severity.WARN
    fields: ClassVar[Tuple[str, ...]] = DEFAULT_FIELDS
    repair_marker: ClassVar[Optional[str]] = None
    skip_hook: ClassVar[bool] = False

    def is_enabled(self) -> bool:
        """per-Constraint feature flag: CONSTRAINT_<NAME>_ENABLED=0 关闭.

        默认启用。Phase 3 加 surgical rollback 能力。
        """
        flag = os.getenv(f"CONSTRAINT_{self.name.upper()}_ENABLED", "1")
        return flag.strip() != "0"

    @abstractmethod
    def apply(self, ctx) -> list[RepairRecord]:
        """检查 + 修复. 返回本次调用的修复记录列表.

        实现要求：
          1. 幂等：第二次调用应返回空列表（用 self._already_repaired 跳过）
          2. 不抛异常：吞掉所有异常，logger.warn 后继续
          3. 跨镜头逻辑：用 self.iter_shots_and_hook(ctx) 统一迭代
          4. 修复后调 ctx.record_repair(...) 上报
        """
        ...

    # ------------------------------------------------------------------
    # 幂等性：默认实现扫描 fields 检查 repair_marker
    # ------------------------------------------------------------------

    def _already_repaired(self, shot: dict) -> bool:
        """幂等检查：shot 是否已含修复标记.

        默认用 repair_marker 类属性扫描 fields. 子类可重写做更复杂的判断.
        """
        if not self.repair_marker:
            return False
        marker = self.repair_marker
        for f in self.fields:
            v = shot.get(f)
            if isinstance(v, str) and marker in v:
                return True
            elif isinstance(v, list):
                # keyframes / char_layout 等 list 字段也扫
                for item in v:
                    if isinstance(item, dict):
                        for sub in item.values():
                            if isinstance(sub, str) and marker in sub:
                                return True
        return False

    # ------------------------------------------------------------------
    # 迭代器：统一 shots + hook 遍历
    # ------------------------------------------------------------------

    def iter_shots_and_hook(self, ctx) -> Iterator[Tuple[dict, bool, Optional[int]]]:
        """统一迭代 shots + hook（如 hook 不 skip）.

        Yield: (shot_dict, is_hook: bool, shot_index: int | None)
            - shot_index: shot["index"]（hook 用 None 表示非数字）
        """
        if isinstance(ctx.shots, list):
            for i, s in enumerate(ctx.shots):
                if isinstance(s, dict):
                    yield s, False, s.get("index", i)
        if ctx.hook and not self.skip_hook:
            yield ctx.hook, True, None

    # ------------------------------------------------------------------
    # 字段操作辅助
    # ------------------------------------------------------------------

    def _append_to_field(self, shot: dict, field: str, text: str, sep: str = "。") -> bool:
        """向字段追加文本（如果还没含 text）.

        Returns:
            True=实际修改, False=已含或字段不存在
        """
        cur = shot.get(field)
        if not isinstance(cur, str):
            return False
        if text in cur:
            return False
        # 用 sep 拼接（默认句号）
        stripped = cur.rstrip("。.，,;；")
        shot[field] = stripped + sep + text
        return True

    def _rewrite_field(self, shot: dict, field: str, new_text: str) -> bool:
        """整体替换字段.

        Returns:
            True=实际修改, False=已相同
        """
        if shot.get(field) == new_text:
            return False
        shot[field] = new_text
        return True

    def _field_text(self, shot: dict, field: Optional[str] = None) -> str:
        """拼接 shot 的所有 fields 文本（用于跨字段检测）.

        Args:
            shot: shot dict
            field: 指定单个字段；None=扫所有 fields
        """
        if field:
            v = shot.get(field)
            return v if isinstance(v, str) else ""
        parts: list[str] = []
        for f in self.fields:
            v = shot.get(f)
            if isinstance(v, str):
                parts.append(v)
        return "\n".join(parts)
