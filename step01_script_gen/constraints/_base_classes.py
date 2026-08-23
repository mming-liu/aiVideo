"""3 个常用基类简化 Constraint 实现.

Phase 3+ 大部分 Constraint 行为是 3 种模式之一：
  1. SimpleTextAppendConstraint: 检测锚词，缺则追加固定文本（最常见）
  2. InterShotConstraint: 跨镜头比较（如景别多样性/轴线）
  3. KeyframeConstraint: 处理 keyframes 列表（不扫文本字段）

子类只重写核心 _needs_repair + _repair，框架处理幂等 + 字段扫描.
"""
from __future__ import annotations

from typing import Optional, Tuple

from .base import Constraint
from .types import RepairRecord


class SimpleTextAppendConstraint(Constraint):
    """检测锚词 + 缺则追加固定文本（最常见模式）.

    子类只重写：
      - anchor_phrases (ClassVar): 锚词 tuple
      - append_text (ClassVar): 追加的固定文本
      - 可选 _field_filter(field_name) -> bool 决定哪些字段被扫描

    幂等：anchor_phrases 任一在场 -> 跳过.
    """

    anchor_phrases: Tuple[str, ...] = ()
    append_text: str = ""

    def _is_already_repaired(self, text: str) -> bool:
        """子类可重写更复杂的判断."""
        return any(a in text for a in self.anchor_phrases)

    def _field_filter(self, field: str) -> bool:
        """子类重写决定哪些字段被处理."""
        return True

    def _append_if_needed(
        self, shot, field: str, idx
    ) -> Optional[RepairRecord]:
        cur = shot.get(field)
        if not isinstance(cur, str) or not cur:
            return None
        if not self._field_filter(field):
            return None
        if self._is_already_repaired(cur):
            return None
        before = cur
        shot[field] = cur.rstrip("。.，,;；") + "。" + self.append_text
        return RepairRecord(
            constraint_name=self.name,
            shot_index=idx,
            action="append",
            target=field,
            before=before,
            after=shot[field],
        )

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            for fld in self.fields:
                r = self._append_if_needed(shot, fld, idx)
                if r:
                    repairs.append(r)
        return repairs


class InterShotConstraint(Constraint):
    """跨镜头比较的 Constraint 基类.

    子类重写：
      - _check_pair(prev_shot, curr_shot, ctx, prev_idx, curr_idx)
        返回 None 或 RepairRecord.
    """

    def _check_pair(self, prev, curr, ctx, prev_idx, curr_idx):
        raise NotImplementedError

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        all_shots = []
        for s, is_hook, idx in self.iter_shots_and_hook(ctx):
            all_shots.append((s, idx))
        for i in range(len(all_shots) - 1):
            prev, prev_idx = all_shots[i]
            curr, curr_idx = all_shots[i + 1]
            r = self._check_pair(prev, curr, ctx, prev_idx, curr_idx)
            if r:
                repairs.append(r)
        return repairs


class KeyframeConstraint(Constraint):
    """处理 keyframes 列表的 Constraint 基类（不扫文本字段）.

    子类重写 _check_kf(kf, shot, ctx, shot_idx, kf_idx) -> Optional[RepairRecord].
    """

    fields: Tuple[str, ...] = ()

    def _check_kf(self, kf, shot, ctx, shot_idx, kf_idx):
        raise NotImplementedError

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            kfs = shot.get("keyframes") or []
            if not isinstance(kfs, list):
                continue
            for j, kf in enumerate(kfs):
                if not isinstance(kf, dict):
                    continue
                r = self._check_kf(kf, shot, ctx, idx, j)
                if r:
                    repairs.append(r)
        return repairs
