"""F17. 高潮完整性校验+硬修复——高潮必须有结果，不能"到威胁为止"。

v6.64 P4.6 升级：从纯 WARN 检测 → 检测+硬修复
- 检测：找高潮镜序列，检查是否含结果词
- 修复：如果不完整，给最后一个高潮镜的 lf_desc / motion_desc 追加结果态描述

修复策略（通用化，不绑小说）：
1. 判断高潮类型（武斗 / 文斗）
2. 武斗：追加"兵刃接触 + 受害者反应 + 伤害可视化"中至少 2 项
3. 文斗：追加"对方反应 / 真相揭晓 / 身份败露"
4. 只改最后一个高潮镜的末尾，不破坏前面的内容
"""
from __future__ import annotations

import sys
import random
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
    "受伤", "负伤", "流血", "洇红", "洇痕", "暗红", "血痕", "血迹",
    "血染", "中刀", "中剑", "刀伤", "剑伤", "伤口", "吐血", "喷血",
    "得手", "得逞", "拿下", "擒住", "绑住",
    "杀死", "击杀", "毙命", "身亡", "断气",
    "抽刀", "拔刃", "刃入", "刀入",
    # 文斗/计谋结果
    "揭晓", "揭露", "败露", "揭穿", "识破", "认出",
    "认罪", "招供", "坦白", "跪下", "臣服", "认输",
    "反转", "逆转", "突变", "异变",
)

# 高潮启动词——表示冲突开始升级
_CLIMAX_START_WORDS = (
    "举刀", "举剑", "举刃", "逼近", "扑向", "冲向", "冲来",
    "扬起", "举起", "高举", "挥起", "劈下", "刺向",
    "威胁", "呵斥", "怒喝", "质问", "怒斥", "厉声",
    "拔出", "抽出", "亮出", "出鞘",
    "弓上弦", "箭在弦", "瞄准", "对准",
)

# 武斗关键词（判断高潮类型）
_MARTIAL_KEYWORDS = (
    "刀", "剑", "刃", "匕首", "刺", "砍", "劈", "杀",
    "刺客", "暗杀", "袭击", "突袭", "偷袭",
    "搏斗", "打斗", "交手", "交锋",
    "血", "伤", "倒地",
)

# 文斗关键词
_VERBAL_KEYWORDS = (
    "质问", "对峙", "揭穿", "揭露", "败露", "识破",
    "呵斥", "怒喝", "厉声", "反驳", "争辩",
    "跪下", "认罪", "招供", "臣服", "认输",
)


def _get_climax_shots(shots):
    """返回高潮镜索引列表（与 gap_analysis 同一判定逻辑简化版）."""
    from step01_script_gen.climax_words import climax_words_for_gap
    from step01_script_gen.gap_analysis import normalize_core_keywords

    script = {"shots": shots, "meta": {}}
    try:
        keywords = climax_words_for_gap(script)
    except Exception:
        keywords = []

    if not keywords:
        keywords = _CLIMAX_START_WORDS

    climax_indices = []
    for i, shot in enumerate(shots):
        if not isinstance(shot, dict):
            continue
        text = " ".join([
            str(shot.get("narration_segment", "") or ""),
            str(shot.get("subtitle", "") or ""),
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


def _climax_type(shot) -> str:
    """判断高潮类型：martial（武斗）/ verbal（文斗）/ unknown."""
    if not isinstance(shot, dict):
        return "unknown"
    text = " ".join([
        str(shot.get("prompt", "") or ""),
        str(shot.get("ff_desc", "") or ""),
        str(shot.get("lf_desc", "") or ""),
        str(shot.get("motion_desc", "") or ""),
        str(shot.get("main_subject", "") or ""),
        str(shot.get("narration_segment", "") or ""),
        " ".join(str(k) for k in (shot.get("core_keywords", []) or [])),
    ])
    martial = sum(1 for kw in _MARTIAL_KEYWORDS if kw in text)
    verbal = sum(1 for kw in _VERBAL_KEYWORDS if kw in text)
    if martial > verbal and martial >= 2:
        return "martial"
    elif verbal > martial and verbal >= 2:
        return "verbal"
    elif martial > 0:
        return "martial"
    elif verbal > 0:
        return "verbal"
    return "unknown"


def _find_victim_and_attacker(shot) -> tuple[str, str]:
    """从高潮镜中找出受害者和攻击者角色名."""
    chars = shot.get("characters", []) or []
    main_subj = str(shot.get("main_subject", "") or "")
    
    # 从 characters 列表取
    char_names = []
    for c in chars:
        if isinstance(c, dict):
            name = c.get("name") or c.get("identifier") or ""
            if name and len(name) >= 2:
                char_names.append(name)
        elif isinstance(c, str) and len(c) >= 2:
            char_names.append(c)
    
    if len(char_names) >= 2:
        # 简单假设：前一个是攻击者，后一个是受害者
        # 实际可以从 motion_desc 推断，但这里做保守估计
        return char_names[0], char_names[1]
    elif len(char_names) == 1:
        return "攻击者", char_names[0]
    
    # 从 main_subject 提取
    parts = main_subj.replace("，", ",").replace("、", ",").split(",")
    parts = [p.strip() for p in parts if len(p.strip()) >= 2 and "群演" not in p and "空镜" not in p]
    if len(parts) >= 2:
        return parts[0], parts[1]
    elif len(parts) == 1:
        return "刺客", parts[0]
    
    return "刺客", "目标人物"


def _append_result_to_lf(shot, ctype: str) -> str:
    """给 lf_desc 追加结果态描述."""
    lf = str(shot.get("lf_desc", "") or "").rstrip("。").rstrip()
    attacker, victim = _find_victim_and_attacker(shot)
    
    if ctype == "martial":
        # 武斗结果：兵刃刺入 + 受害者反应 + 伤害可视化
        suffix = (
            f"，匕首刺入{victim}肩头，{victim}闷哼一声身形后仰，"
            f"衣襟暗红迅速洇开。{attacker}握刃的手青筋暴起，"
            f"刀尖仍在推进"
        )
    elif ctype == "verbal":
        # 文斗结果：对方震惊/败露/跪下
        suffix = (
            f"，{victim}脸色骤变身形一晃，颤声后退半步，"
            f"嘴唇哆嗦着说不出话。{attacker}目光如刀紧逼，"
            f"空气中弥漫着一触即发的张力"
        )
    else:
        # 通用结果：紧张升级 + 明确后果
        suffix = (
            f"，{victim}身体猛地一震，面露惊骇之色，"
            f"气氛骤然紧张到极点"
        )
    
    # 避免重复追加
    if "暗红" in lf or "刺入" in lf or "骤变" in lf:
        return lf
    
    return lf + suffix + "。"


def _append_result_to_motion(shot, ctype: str) -> str:
    """给 motion_desc 追加结果动作段."""
    motion = str(shot.get("motion_desc", "") or "")
    dur = shot.get("duration_sec", 7.0)
    attacker, victim = _find_victim_and_attacker(shot)
    
    # 找到最后一个时间段
    if "6-8秒" in motion:
        if ctype == "martial":
            insert = (f"6-8秒：匕首猛地刺出，{victim}躲闪不及肩头中招，"
                      f"身子一晃向后趔趄，血染白衣")
        elif ctype == "verbal":
            insert = (f"6-8秒：{victim}听罢浑身一颤，踉跄后退半步，"
                      f"脸色煞白说不出话")
        else:
            insert = (f"6-8秒：气氛骤然紧绷，{victim}面露惊骇，"
                      f"局势急转直下")
        # 替换最后一段
        import re
        motion = re.sub(r"6-8秒[：:].*", insert, motion)
    elif "5-7秒" in motion:
        if ctype == "martial":
            insert = (f"5-7秒：兵刃递出刺中{victim}，"
                      f"{victim}闷哼后仰，衣襟洇红")
        elif ctype == "verbal":
            insert = (f"5-7秒：{victim}脸色剧变，身形摇晃，"
                      f"显然被戳中要害")
        else:
            insert = (f"5-7秒：冲突达到顶点，{victim}震惊失色，"
                      f"局势突变")
        import re
        motion = re.sub(r"5-7秒[：:].*", insert, motion)
    else:
        # 没有明确分段，直接追加在末尾
        if ctype == "martial":
            suffix = f"最终兵刃刺中{victim}，鲜血洇出，{victim}受伤后仰"
        elif ctype == "verbal":
            suffix = f"最终{victim}败露震惊，脸色煞白，身形摇晃"
        else:
            suffix = f"最终冲突爆发，{victim}措手不及"
        motion = motion.rstrip("。") + "；" + suffix + "。"
    
    return motion


class ClimaxCompletenessConstraint(Constraint):
    """F17. 高潮完整性：高潮必须有结果，不能悬而未决.

    v6.64 P4.6 升级：从 WARN 检测 → 硬修复
    - 检测到高潮不完整时，给最后一个高潮镜追加结果态描述
    - 只改 lf_desc + motion_desc，不改剧情走向
    """

    name = "climax_completeness"
    severity = Severity.INFO
    skip_hook = True

    def apply(self, ctx):
        repairs = []
        shots = ctx.shots
        if not isinstance(shots, list) or len(shots) < 3:
            return repairs

        climax_indices = _get_climax_shots(shots)
        if not climax_indices:
            return repairs  # 零高潮不归这个约束管

        last_climax = climax_indices[-1]
        scope = set(climax_indices) | set(
            range(last_climax + 1, min(len(shots), last_climax + 3))
        )
        has_result_any = any(
            _has_result(shots[i]) for i in sorted(scope) if isinstance(shots[i], dict)
        )

        if has_result_any:
            return repairs  # 已有结果，完整

        last_shot = shots[last_climax]
        if not isinstance(last_shot, dict):
            return repairs

        # Detection-only.  The old repair appended a fabricated “刺入/中招/倒地”
        # result to the last climax shot, which can change the plot even when
        # the source only reached a threat or preparation beat.  The audit/LLM
        # layer must decide whether a result actually exists.
        ctx.record_violation(Violation(
            constraint_name=self.name,
            severity=Severity.WARN,
            shot_index=last_shot.get("index", last_climax + 1),
            field="lf_desc",
            message=(
                "高潮相关镜头及后两镜未找到明确结果态；保留原文，"
                "禁止自动追加兵刃刺中、受伤或败露等虚构结果。"
            ),
            evidence=str(last_shot.get("lf_desc") or last_shot.get("motion_desc") or "")[-180:],
            repaired=False,
        ))
        repairs.append(RepairRecord(
            constraint_name=self.name,
            shot_index=last_shot.get("index", last_climax + 1),
            action="warn",
            target="lf_desc",
            detail="高潮缺少可证结果，保留原文交由 audit/LLM 裁决",
        ))
        return repairs
