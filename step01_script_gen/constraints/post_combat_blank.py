"""F6. 打斗战后空镜收尾硬修复（v6.49 新增，08-28 跑批实测）.

08-28 chapter_001 跑批实测 1 处打斗节奏告警：
  镜头 5 打斗结束后，连续镜头无空镜/大远景收尾，建议战后插 1 个
  5-10s 空镜叠化/大远景（极光/月夜/山水），突出平静与成长。

修法：扫每镜 prompt/motion/ff/lf，若含打斗词（_COMBAT_KEYWORDS）且
后 1 镜无空镜/远景词 → 在后 1 镜 prompt 追加「远景/极光/月夜/空镜
叠化收尾」模板。

severity=WARN: 已有确定性改写.
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity

# 打斗/刺杀/追逐词（复用 _COMBAT_KEYWORDS 缩短版；与 combat_impact 同源）
_COMBAT_HINTS = (
    "打斗", "战斗", "格斗", "刺杀", "刺入", "中刀", "中剑",
    "挥刀", "挥剑", "挥砍", "砍杀", "挥拳", "搏斗", "交手",
    "追逐", "追击", "奔驰", "奔逃", "疾奔", "疾驰", "狂奔", "飞驰", "冲出",
    "破水", "跃上", "猛刺", "扑出", "翻身跃",
)

# 空镜/远景收尾词（命中任一即视为已含收尾）
_BLANK_SHOT_HINTS = (
    "空镜", "空无一人", "大远景", "极光", "月夜", "远山", "远景", "水墨", "山水",
    "残阳", "夕照", "落日", "星夜", "夜色", "叠化", "空无", "无人", "无人物",
    "无边的", "天地间", "苍穹", "云海",
)

_BLANK_SHOT_TAIL = "大远景月夜空镜叠化收尾"  # 追加模板


def _has_combat(text: str) -> bool:
    return any(w in text for w in _COMBAT_HINTS)


def _has_blank_shot(text: str) -> bool:
    return any(w in text for w in _BLANK_SHOT_HINTS)


class PostCombatBlankConstraint(Constraint):
    """F6. 打斗战后空镜收尾：后 1 镜无空镜 → 追加大远景月夜空镜叠化."""

    name = "post_combat_blank"
    severity = Severity.WARN
    skip_hook = True
    fields = ()  # 显式空：扫描 prompt/motion/ff/lf 多字段

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        if not isinstance(ctx.shots, list):
            return repairs

        for i, shot in enumerate(ctx.shots):
            if not isinstance(shot, dict):
                continue
            # 当前镜含打斗词
            text = " ".join(
                str(shot.get(f) or "") for f in ("prompt", "motion_desc", "ff_desc", "lf_desc")
            )
            if not _has_combat(text):
                continue
            # 找后 1 镜（同 location 或 hook 前的最后一镜）
            if i + 1 >= len(ctx.shots):
                continue  # 已是末镜，无后镜
            next_shot = ctx.shots[i + 1]
            if not isinstance(next_shot, dict):
                continue
            next_text = " ".join(
                str(next_shot.get(f) or "") for f in ("prompt", "motion_desc", "ff_desc", "lf_desc")
            )
            # 已有空镜/远景收尾 → 跳过
            if _has_blank_shot(next_text):
                continue
            # 追加大远景月夜空镜叠化到 prompt 末尾
            prompt = next_shot.get("prompt", "") or ""
            if not isinstance(prompt, str):
                continue
            # 幂等：已含本模板视为已修
            if "大远景月夜空镜叠化收尾" in prompt:
                continue
            new_prompt = prompt.rstrip("。.，,;；") + "。" + _BLANK_SHOT_TAIL
            next_shot["prompt"] = new_prompt
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=next_shot.get("index"),
                    action="append",
                    target="prompt",
                    before=prompt[:60] + ("..." if len(prompt) > 60 else ""),
                    after=new_prompt[:60] + ("..." if len(new_prompt) > 60 else ""),
                )
            )
        return repairs
