"""W10. 景别跳跃约束（v6.42 迁移自 _hard_fix_framing_jump）.

8-22 跑批实测：shot 1 全景 → shot 2 中近景（3 级跳跃）报"景别跳跃幅度 3 级
（>2 级）"。

2026-11-14 S06 G2 裁决：**只报不改（report-only）**。此前本约束会把前镜 framing
替换为后镜的相邻景别；实测（U6）该改写会静默篡改剧本景别/机位，甚至把修复轮
已改对的景别替换回原值。现在只记录 warning（`RepairRecord(action="warn")`），
剧本原文一个字不动；景别跨度由 `validation._check_shot_size_jump` 报 warning，
交审计/人工评阅。

豁免：场景切换 / 打斗镜（与 _check_shot_size_jump 同口径）。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.validation import _is_combat_shot, _shot_size_level
from ._base_classes import InterShotConstraint
from .types import RepairRecord, Severity, Violation


_SHOT_SIZE_ORDER = [
    "大特写", "特写", "中近景", "中景", "中远景",
    "全景", "远景", "大远景", "鸟瞰远景",
]


def _is_scene_switch(s1: dict, s2: dict) -> bool:
    loc1 = s1.get("location", "") or s1.get("scene", "")
    loc2 = s2.get("location", "") or s2.get("scene", "")
    return bool(loc1 and loc2 and loc1 != loc2)


class FramingJumpConstraint(InterShotConstraint):
    """W10. 景别跳跃：相邻景别跨度 > 2 级 → **只报 warning，不改写**.

    severity=WARN: 景别跳跃交审计/人工或后续 repair round 处理。
    """

    name = "framing_jump"
    severity = Severity.WARN
    skip_hook = True

    def _check_pair(self, prev, curr, ctx, prev_idx, curr_idx):
        if not isinstance(prev, dict) or not isinstance(curr, dict):
            return None
        # 2026-11-12 复核（投影）：最终落盘前只报不改（与 G2 裁决一致）。
        if getattr(ctx, "is_final_pass", False):
            return None
        # 场景切换豁免
        if _is_scene_switch(prev, curr):
            return None
        # 打斗镜豁免
        try:
            if _is_combat_shot(prev) or _is_combat_shot(curr):
                return None
        except Exception:
            pass
        # 取景别
        lv1 = _shot_size_level(prev)
        lv2 = _shot_size_level(curr)
        if not lv1 or not lv2:
            return None
        try:
            d1 = _SHOT_SIZE_ORDER.index(lv1)
            d2 = _SHOT_SIZE_ORDER.index(lv2)
        except ValueError:
            return None
        if abs(d1 - d2) <= 2:
            return None
        # 跨度 > 2：只报不改（G2：不静默篡改剧本景别/机位）。
        prompt = str(prev.get("prompt", "") or "")
        message = (
            f"相邻镜景别跨度 {abs(d1 - d2)} 级（{lv1} → {lv2}，>2）："
            "只报不改，交审计/人工评阅，禁止自动改写景别"
        )
        try:
            ctx.record_violation(Violation(
                constraint_name=self.name,
                severity=Severity.WARN,
                shot_index=prev.get("index", prev_idx),
                field="prompt",
                message=message,
                evidence=prompt[-120:],
                repaired=False,
            ))
        except Exception:
            pass
        return RepairRecord(
            constraint_name=self.name,
            shot_index=prev_idx,
            action="warn",
            target="prompt",
            before=prompt,
            after=prompt,
            detail=message,
        )
