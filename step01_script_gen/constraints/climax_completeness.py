"""F17. 高潮完整性校验——高潮必须有结果，不能"到威胁为止"。

问题：doubao 经常把高潮拍成"举刀逼近→结束"，那一刀始终没落下。
当前 climax_words 只检测"有没有冲突"，不检测"冲突有没有结果"。

方案：检测高潮镜序列的完整性——
1. 找出所有高潮镜（按 climax_keywords / 动作词）
2. 检查高潮镜中是否含"结果词"（刺中/倒地/得手/败露/揭晓/倒下/命中/受伤等）
3. 没有结果 → 标记为高潮不完整（WARN），进入 GAP 反馈

注意：这是**校验**约束，只检测+标记，不硬改写视觉内容
（高潮怎么结束是剧情问题，硬写会崩）。交给 GAP 反馈 + 重试来修。

severity=WARN: 触发 GAP 重试但不硬修。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity, Violation


# 结果词——表示高潮动作已经完成/有了结局
_RESULT_WORDS = (
    # 武斗结果
    "刺中", "刺进", "刺入", "命中", "砍中", "砍倒", "劈中",
    "倒地", "倒下", "栽倒", "跌倒", "晕倒", "昏倒",
    "受伤", "负伤", "流血", "洇红", "吐血", "喷血",
    "得手", "得逞", "拿下", "擒住", "抓住", "绑住",
    "杀死", "击杀", "毙命", "身亡", "断气",
    "抽刀", "拔刃", "刃入", "刀入",
    # 文斗/计谋结果
    "揭晓", "揭露", "败露", "揭穿", "识破", "认出",
    "认罪", "招供", "坦白", "跪下", "臣服", "认输",
    "震惊", "失色", "变色", "骇然", "惊愕",
    "反转", "逆转", "突变", "异变",
    "点头", "答应", "同意", "应允", "承诺",
)

# 高潮启动词——表示冲突开始升级（有这个但没结果=不完整）
_CLIMAX_START_WORDS = (
    "举刀", "举剑", "举刃", "逼近", "扑向", "冲向", "冲来",
    "扬起", "举起", "高举", "挥起", "劈下", "刺向",
    "威胁", "呵斥", "怒喝", "质问", "怒斥", "厉声",
    "拔出", "抽出", "亮出", "出鞘",
    "弓上弦", "箭在弦", "瞄准", "对准",
)


def _get_climax_shots(shots):
    """返回高潮镜索引列表（与 gap_analysis 同一判定逻辑简化版）."""
    from step01_script_gen.climax_words import climax_words_for_gap
    from step01_script_gen.gap_analysis import normalize_core_keywords

    # 构造 mock script
    script = {"shots": shots, "meta": {}}
    try:
        keywords = climax_words_for_gap(script)
    except Exception:
        keywords = []

    if not keywords:
        keywords = _CLIMAX_START_WORDS  # fallback

    climax_indices = []
    for i, shot in enumerate(shots):
        if not isinstance(shot, dict):
            continue
        text = " ".join([
            str(shot.get("narration_segment", "") or ""),
            str(shot.get("subtitle", "") or ""),
            str(shot.get("prompt", "") or ""),
            str(shot.get("motion_desc", "") or ""),
            str(shot.get("main_subject", "") or ""),
            " ".join(normalize_core_keywords(shot.get("core_keywords", []))),
        ])
        if any(kw in text for kw in keywords):
            climax_indices.append(i)
    return climax_indices


def _has_result(shot):
    """一镜中是否含结果词."""
    if not isinstance(shot, dict):
        return False
    text = " ".join([
        str(shot.get("prompt", "") or ""),
        str(shot.get("ff_desc", "") or ""),
        str(shot.get("lf_desc", "") or ""),
        str(shot.get("motion_desc", "") or ""),
        str(shot.get("narration_segment", "") or ""),
        str(shot.get("core_keywords", "") or ""),
    ])
    return any(w in text for w in _RESULT_WORDS)


class ClimaxCompletenessConstraint(Constraint):
    """F17. 高潮完整性：高潮必须有结果，不能悬而未决."""

    name = "climax_completeness"
    severity = Severity.WARN
    skip_hook = True

    def apply(self, ctx):
        repairs = []
        shots = ctx.shots
        if not isinstance(shots, list) or len(shots) < 3:
            return repairs

        climax_indices = _get_climax_shots(shots)
        if not climax_indices:
            return repairs  # 零高潮不归这个约束管（归 gap_analysis）

        # 检查全片有没有结果词（结果可能出现在非高潮标记的镜里）
        has_result_any = any(_has_result(s) for s in shots if isinstance(s, dict))

        if has_result_any:
            return repairs  # 有结果，完整

        # 高潮不完整：所有高潮镜都只有"起势"没有"结果"
        # 用 violation 报告（触发 GAP 反馈）
        first_climax_idx = climax_indices[0]
        shot = shots[first_climax_idx]
        shot_idx = shot.get("index", first_climax_idx + 1)

        ctx.record_violation(
            Violation(
                constraint_name=self.name,
                severity=Severity.WARN,
                shot_index=shot_idx,
                field="climax_completeness",
                repaired=False,
                message=(
                    "高潮不完整：全片 %d 个高潮镜均只有冲突起势（逼近/举刀/质问），"
                    "没有结果动作（刺中/倒地/败露/揭晓等）。"
                    "高潮必须拍完：起势 → 接触 → 结果，不能停在威胁阶段。"
                    % len(climax_indices)
                ),
            )
        )

        repairs.append(
            RepairRecord(
                constraint_name=self.name,
                shot_index=shot_idx,
                action="warn_incomplete",
                target="climax_sequence",
                before="高潮%d个，无结果" % len(climax_indices),
                after="标记不完整，将触发GAP重试",
            )
        )
        return repairs