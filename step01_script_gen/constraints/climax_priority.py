"""F8. 高潮优先硬修复（v6.49 新增，08-28 跑批实测）.

08-28 chapter_001 跑批实测 1 处高潮优先告警：
  镜头 1 非高潮镜头（背景/过渡/日常），v6.35 规则 3.7 应为 0 镜——
  信息由旁白（narration_segment）一句带过，禁止单独成镜占用剧情预算。

修法：扫每镜 prompt/motion/ff/lf，若无任何「核心事件」词（打斗/刺杀/
追逐/情感爆发/揭秘/反转）且无台词（subtitle 空）→ 给 narration_segment
追加「(本镜信息已并入旁白，本镜删除或合并)」标记，让 step04 merge 知道
这是待合并的镜头。

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

# 核心事件词（命中即视为高潮镜头，不强制合并）
_CLIMAX_EVENT_WORDS = (
    "打斗", "战斗", "刺杀", "刺入", "中刀", "中剑",
    "追逐", "追击", "奔驰", "疾驰", "狂奔", "冲出",
    "揭秘", "反转", "揭穿", "冲突", "对峙", "质问",
    "情感爆发", "怒喝", "哭喊", "尖叫", "暴怒",
    "惊觉", "发现", "认出", "识破", "对峙", "决裂",
    "入水", "破水", "跃上", "翻身", "仰倒", "倒下", "倒地",
    "亲吻", "拥抱", "诀别", "拒斥",
)

# 非高潮背景/过渡/日常词
_DAILY_HINTS = (
    "建立", "环境", "场景", "日落", "日出", "晨光", "晚霞", "夜色",
    "静坐", "喝茶", "垂钓", "闲聊", "散步", "远眺", "观星", "观景",
    "日常", "背景", "过渡", "空镜", "山水", "远山",
)

_REPAIR_MARKER = "[本镜信息已并入旁白，镜头降级为背景/过渡]"


def _has_climax_event(text: str) -> bool:
    return any(w in text for w in _CLIMAX_EVENT_WORDS)


def _has_dialogue(shot: dict) -> bool:
    sub = str(shot.get("subtitle", "") or "").strip()
    narr = str(shot.get("narration_segment", "") or "").strip()
    return bool(sub) or bool(narr)


class ClimaxPriorityConstraint(Constraint):
    """F8. 高潮优先：非高潮+无台词 → 追加合并标记."""

    name = "climax_priority"
    severity = Severity.WARN
    skip_hook = True

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            if _has_dialogue(shot):
                continue  # 有台词 → 不可合并
            text = " ".join(
                str(shot.get(f) or "") for f in ("prompt", "motion_desc", "ff_desc", "lf_desc")
            )
            if _has_climax_event(text):
                continue  # 有核心事件 → 不可合并
            # 含日常/过渡词 → 视为非高潮
            if not any(w in text for w in _DAILY_HINTS):
                continue
            narr = str(shot.get("narration_segment", "") or "")
            if _REPAIR_MARKER in narr:
                continue  # 幂等
            new_narr = (
                narr.rstrip("。.，,;；") + "。" + _REPAIR_MARKER
                if narr.strip()
                else _REPAIR_MARKER
            )
            shot["narration_segment"] = new_narr
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=shot.get("index") if not is_hook else None,
                    action="append",
                    target="narration_segment",
                    before=narr[:60] + ("..." if len(narr) > 60 else ""),
                    after=new_narr[:60] + ("..." if len(new_narr) > 60 else ""),
                )
            )
        return repairs
