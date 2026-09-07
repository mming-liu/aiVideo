"""F8. 高潮优先硬修复（v6.49 新增，08-28 跑批实测）.

08-28 chapter_001 跑批实测 1 处高潮优先告警：
  镜头 1 非高潮镜头（背景/过渡/日常），v6.35 规则 3.7 应为 0 镜——
  信息由旁白（narration_segment）一句带过，禁止单独成镜占用剧情预算。

v6.62.1（09-06）修法变更：不再向 narration_segment 注入
「[本镜信息已并入旁白，镜头降级为背景/过渡]」——step04 merge 从无消费代码
（grep 全零），标记只污染锚点校验/字幕。改为写运行时元数据
`shot["_merge_candidate"]=True`（下划线开头=运行时数据约定，不入下游读盘
关注面），留待未来 step04 播放合并逻辑或人工删镜时使用。

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

# v6.62.1（09-06）：fixer 与 checker（validation._check_climax_priority）口径
# 对齐——checker 用的 _CLIMAX_BG_HINTS（田埂/炊烟/媒人/家常…）此前 fixer 不读，
# 出现「checker 每轮报 4 镜非高潮、fixer 只标 1 镜」的永修不掉（09-06 实测）。
# 合并两表（checker 为主 + 内置兜底），单源：优先 import validation。
def _bg_hints() -> tuple[str, ...]:
    try:
        from step01_script_gen.validation import _CLIMAX_BG_HINTS
        return tuple(_CLIMAX_BG_HINTS) + _DAILY_HINTS
    except Exception:
        return _DAILY_HINTS

# v6.62.1 起不再注入 narration（原 _REPAIR_MARKER 已停用，保留常量供
# 历史数据剥离方引用：见 validation post_fix 剥「[本镜信息已并入旁白…]」残留）
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
            # 含日常/过渡词 → 视为非高潮（词表与 checker 同源，v6.62.1）
            if not any(w in text for w in _bg_hints()):
                continue
            # v6.62.1（09-06）：合并候选标记改写运行时元数据 `_merge_candidate`，
            # 不再注入 narration_segment——旧版把 _REPAIR_MARKER 写进 narration
            # （注释自称"让 step04 merge 知道"，但 step04 从无消费代码），标记
            # 却污染锚点校验（镜头10「实词覆盖率 22%」）+ 会进字幕。真实现
            # "信息并入旁白"需动 step04 播放逻辑，交用户决策；本约束只留痕。
            if shot.get("_merge_candidate"):
                continue  # 幂等
            shot["_merge_candidate"] = True
            shot.setdefault("_run_metadata", {})["climax_priority"] = (
                "非高潮背景/过渡镜（无台词+无核心事件）——候选合并/删除，"
                "信息应由 narration 一句带过（v6.35 规则 3.7）"
            )
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=shot.get("index") if not is_hook else None,
                    action="set_field",
                    target="_merge_candidate",
                    before="",
                    after="True",
                )
            )
        return repairs
