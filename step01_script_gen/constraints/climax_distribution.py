"""W7. 高潮分布约束（v6.42 迁移自 _hard_fix_climax_distribution）.

8-22 跑批实测：8 镜中 2 个 climax 镜（shot 3 刺杀 + shot 5 张十三质问）都集中在
前 1/2 段（0-20s），后半段 20-37s 平淡无爆点。

修法：检测 _CLIMAX_CONFLICT_HINTS 镜集中位置，若 < 1/3 时长在 climax 段 →
在后半段（index >= last_third）挑一镜，main_subject 追加反转/揭穿/悬念词，
prompt 末尾追加 _CLIMAX_TAIL_TEMPLATES 之一。

幂等：已含"反转/揭露/揭穿/局势骤变/真相"跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.validation import _CLIMAX_CONFLICT_HINTS
from .base import Constraint
from .types import RepairRecord, Severity


_CLIMAX_TAIL_TEMPLATES = (
    "。局势骤变，真相大白",
    "。气氛逆转，危机四伏",
    "。身份暴露，矛盾激化",
    "。反转揭露，悬念陡升",
    "。冲突到达顶点，局势不可挽回",
)

# 与 validator (_check_narrative_arc_distribution) 同源
_NARRATIVE_ARC_CLIMAX_KEYWORDS = (
    "刺杀", "战斗", "对决", "反转", "死亡", "诀别", "暴露", "发现",
    "冲突", "搏杀", "跃出", "挥刀", "拔剑",
)

# 幂等检测锚词
_IDEMPOTENT_ANCHORS = ("反转", "揭露", "揭穿", "局势骤变", "真相")


def _is_climax_shot(shot: dict) -> bool:
    """prompt/main_subject/motion_desc/narration 任一含 _CLIMAX_CONFLICT_HINTS."""
    text = " ".join(
        [
            shot.get("prompt", "") or "",
            shot.get("main_subject", "") or "",
            shot.get("motion_desc", "") or "",
            shot.get("narration_segment", "") or "",
        ]
    )
    return any(kw in text for kw in _CLIMAX_CONFLICT_HINTS)


def _pick_unused_keyword(used: set[str]) -> str:
    """挑一个未使用的 climax 关键词."""
    for w in _NARRATIVE_ARC_CLIMAX_KEYWORDS:
        if w not in used:
            return w
    return "反转"


class ClimaxDistributionConstraint(Constraint):
    """W7. 高潮分布：高潮全在前段 → 后半段中间一镜追加反转/揭穿/悬念.

    severity=WARN: 高潮分布可由后续 repair round 修.
    repair_marker=None: 幂等由"再次扫描 climax_in_last >= 1"保证.
    """

    name = "climax_distribution"
    severity = Severity.WARN
    skip_hook = True

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        if not isinstance(ctx.shots, list) or not ctx.shots:
            return repairs

        # 1) 找所有 climax 镜
        climax_idx = [
            i for i, s in enumerate(ctx.shots)
            if isinstance(s, dict) and _is_climax_shot(s)
        ]

        # 全无 climax → 末镜追加反转
        if not climax_idx:
            last = ctx.shots[-1]
            if not isinstance(last, dict):
                return repairs
            prompt = last.get("prompt", "") or ""
            if any(w in prompt for w in _IDEMPOTENT_ANCHORS):
                return repairs
            before = prompt
            last["prompt"] = prompt.rstrip("。") + _CLIMAX_TAIL_TEMPLATES[0]
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=last.get("index", len(ctx.shots) - 1),
                    action="append",
                    target="prompt",
                    before=before,
                    after=last["prompt"],
                )
            )
            return repairs

        # 2) 分布判定：climax 在前/后 1/3 段
        total = len(ctx.shots)
        last_third = total - total // 3
        climax_in_first = sum(1 for i in climax_idx if i < last_third)
        climax_in_last = sum(1 for i in climax_idx if i >= last_third)
        if climax_in_last >= 1:
            return repairs  # 后 1/3 段已有 climax
        if climax_in_first < 1:
            return repairs  # 全部都不算 climax

        # 3) 后 1/3 段缺 climax：挑后 1/3 段中点
        if total < 4:
            return repairs
        target_idx = last_third + (total - last_third) // 2
        if target_idx >= total:
            target_idx = total - 1
        target = ctx.shots[target_idx]
        if not isinstance(target, dict):
            return repairs

        # 4) 改 main_subject 加 climax 关键词
        main_subject = target.get("main_subject", "") or ""
        used_kw = {
            w
            for w in _NARRATIVE_ARC_CLIMAX_KEYWORDS
            if w in main_subject or w in (target.get("prompt", "") or "")
        }
        target_kw = _pick_unused_keyword(used_kw)
        before_subject = main_subject
        target["main_subject"] = main_subject.rstrip("，,。") + f"，{target_kw}揭示"
        repairs.append(
            RepairRecord(
                constraint_name=self.name,
                shot_index=target.get("index", target_idx),
                action="rewrite",
                target="main_subject",
                before=before_subject,
                after=target["main_subject"],
            )
        )
        return repairs
