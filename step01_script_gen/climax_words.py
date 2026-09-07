"""climax_words — 高潮/冲突标记词单源模块（v6.60.1 硬编码清理，2026-09-06）.

历史问题：高潮标记词散落 4 份各自生长——
  1. gap_analysis.analyze_shot_distribution 内联 23 词（尾部 3 词「密信/圣旨/
     破水而出」注释自称"针对你当前文本的特化关键词"——换章/换小说即死数据）
  2. validation.py 三段密度检测内联 13 词 set
  3. validation.py arc picker 内联 13 词 tuple（注释自称"与 validator 同源"
     实为复制粘贴——单边改词后注入的词检测不到）
  4. gap_analysis._get_synonyms 内联同义簇 map（混入 sun/earth/dry 三条英文
     死条目——中文 core_keywords 语境不可达；"马车"值列表含键自身死项）

本模块为唯一事实源。⚠️ 词集语义各异，勿合并（边界词表分工见下）。

meta 注入通道现状（2026-09-06 裁决）：script["meta"]["climax_keywords"] 当前
**无写入方**（script_result.json 实测无 meta 键、全仓无写入代码），恒走
NOVEL_FALLBACK——前瞻接口，写入方落地前零行为变化。写入方落地后，换小说/
换章的特化标记词改数据不改代码。

边界词表（用途不同，确认不纳入本模块，防未来混淆）：
  - validation.py _CLIMAX_CONFLICT_HINTS：2 字以上强冲突词（防单字误匹配）
  - validation.py _PUNCH_WORDS：短视频情绪爆点词（密度/开篇检查共用）
  - validation.py _COMBAT_ARC_CLIMAX_WORDS：大招/蓄力词（打斗四段式结构检查）
  三者做的是语义密度/时长/结构检查，与剧情弧检测合表会互相污染误报。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# 叙事弧三段密度：检测（validation 三段统计）与注入（arc picker）的配对词表
# ⚠️ 13 词**顺序不可重排**——arc picker 的 next(..., "反转") 按遍历序取第一个
# 未使用词，重排会改变注入选词结果。
# ---------------------------------------------------------------------------
CLIMAX_WORDS_ARC: tuple[str, ...] = (
    "刺杀", "战斗", "对决", "反转", "死亡", "诀别", "暴露", "发现",
    "冲突", "搏杀", "跃出", "挥刀", "拔剑",
)

# ---------------------------------------------------------------------------
# GAP 后半段高潮覆盖（gap_analysis.analyze_shot_distribution）
# ---------------------------------------------------------------------------
# 通用半：古风权谋/动作跨小说可用（逐字取自历史 23 词表去特化 3 词）
CLIMAX_WORDS_GAP_GENERIC: tuple[str, ...] = (
    "战斗", "刺杀", "决斗", "冲突", "反转", "爆炸", "死亡", "离开", "逃离",
    "追逐", "阴谋", "暴露", "真相", "身份暴露", "杀", "死", "跑",
    "寒光", "鲜血", "尖叫",
)

# B.3.3 Doubao 高潮回灌使用的最小集合。必须保持是 GAP 检测词表的子集，
# 否则修复器注入后 GAP 仍然读不到。
CLIMAX_WORDS_GAP_INJECT: tuple[str, ...] = ("冲突", "反转", "真相")
# 特化兜底：锦衣夜行第一部第一章的情节标记词（历史种子数据）。
# 迁移目标：script["meta"]["climax_keywords"]——meta 提供后本兜底自动停用。
CLIMAX_WORDS_GAP_NOVEL_FALLBACK: tuple[str, ...] = (
    "密信", "圣旨", "破水而出",
)

# ---------------------------------------------------------------------------
# 爆点/高潮「抽象节拍词」注入产物清单（v6.62 收敛，2026-09-06）——单一事实源
#
# 历史问题：高潮镜/爆点不足时，约束与硬修复曾把抽象节拍词追加进
# narration_segment/subtitle/prompt/main_subject（09-06 chapter_001 实测：
# prompt 尾部噪音、main_subject 污染 4 镜、narration 开头两遍纯节拍词——
# 空镜 narration_segment 原本为空，注入后 suffix 成整段，会进字幕被观众看到）。
# 抽象节拍词对图片/视频模型无视觉意义，v6.62 起全部停用注入，本清单用于
# 约束层剥离历史数据残留（幂等）。新增注入点已禁止；如未来确需注入，
# 必须先在此登记产物形态，保证可剥离。
# ---------------------------------------------------------------------------
# GAP 后半段注入后缀（旧 climax_distribution._inject 产物，随 GAP_INJECT 构造同步）
CLIMAX_INJECTED_GAP_SUFFIX: str = "。" + "，".join(CLIMAX_WORDS_GAP_INJECT) + "，局势骤变"
# W7 高潮分布尾缀（旧 validation._hard_fix_climax_distribution._CLIMAX_TAIL_TEMPLATES）
CLIMAX_INJECTED_TAILS: tuple[str, ...] = (
    "。局势骤变，真相大白",
    "。气氛逆转，危机四伏",
    "。身份暴露，矛盾激化",
    "。反转揭露，悬念陡升",
    "。冲突到达顶点，局势不可挽回",
)
# F2.5 爆点密度尾缀（旧 validation._hard_fix_punch_density / PunchDensityConstraint）
CLIMAX_INJECTED_PUNCH_PATTERN: str = r"。局势骤变，\d+个爆点叠加"
# F8 高潮优先合并标记（旧 climax_priority 注入 narration 的产物，09-06 实测
# 镜头10 narration 整段变成该标记 → 锚点校验「实词覆盖率 22%」+ 险进字幕）
CLIMAX_INJECTED_MERGE_MARKER: str = "[本镜信息已并入旁白，镜头降级为背景/过渡]"


def strip_injected_suffixes(text: str) -> str:
    """剥离爆点/高潮注入残留（幂等；无残留原样返回）.

    处理 narration_segment/subtitle/prompt/main_subject 四类曾注入字段。
    suffix 自带前导「。」（注入方 rstrip 掉原句尾后拼接），整段删除即还原。

    v6.62.1（09-06）：兼容两种落盘形态——
      a) 「前文。冲突，反转，真相，局势骤变」（正常拼接，整段剥前导「。」）
      b) 「冲突，反转，真相，局势骤变」（纯 meta 整段——空 narration 被
         旧注入方/LLM 直接填满 meta 词，无前文无「。」，09-06 实测形态）。
    判定 (b) 的条件：整段剥掉空白后 == 纯 meta 串（3 词 + 「，」拼接 +
    「，局势骤变」）→ 置空（meta 词不是原文散文，不能当 narration 上屏）。
    """
    if not text or not isinstance(text, str):
        return text
    out = text.replace(CLIMAX_INJECTED_GAP_SUFFIX, "")
    for tail in CLIMAX_INJECTED_TAILS:
        out = out.replace(tail, "")
    out = re.sub(CLIMAX_INJECTED_PUNCH_PATTERN, "", out)
    # F8 高潮优先合并标记残留（narration 里整段/句中拼接两种形态）
    if CLIMAX_INJECTED_MERGE_MARKER in out:
        out = out.replace(CLIMAX_INJECTED_MERGE_MARKER, "")
        out = out.replace("。。", "。").strip("。， ")
    # (b) 纯 meta 整段形态：剥掉suffix后剩下的就是 meta 词本体 → 置空
    _bare = out.strip().strip("。")
    if _bare == CLIMAX_INJECTED_GAP_SUFFIX.strip("。"):
        return ""
    return out

# 历史 23 词**原序**表（含特化 3 词原位：第 13/14/23 位）。检测用 any() 匹配、
# 顺序不影响语义，但为满足「无 meta 时与历史词表逐字有序一致」的零变化断言，
# 无 meta 路径直接返回本表。
_CLIMAX_WORDS_GAP_NO_META: tuple[str, ...] = (
    "战斗", "刺杀", "决斗", "冲突", "反转", "爆炸", "死亡", "离开", "逃离",
    "追逐", "阴谋", "暴露", "密信", "圣旨", "真相", "身份暴露", "杀", "死", "跑",
    "寒光", "鲜血", "尖叫", "破水而出",
)


def climax_words_for_gap(script: dict | None) -> list[str]:
    """GAP 高潮覆盖检测用词表 = 通用半 +（meta 注入 或 特化兜底）.

    meta 形态：script["meta"]["climax_keywords"] = ["词1", "词2", ...]
    （list/tuple 均可且全为 str、非空时视为该章/部自己的特化标记词，
    替换 NOVEL_FALLBACK；否则恒走 fallback——见模块头现状声明）。
    """
    if script and isinstance(script, dict):
        meta = script.get("meta")
        if isinstance(meta, dict):
            extra = meta.get("climax_keywords")
            if (isinstance(extra, (list, tuple)) and extra
                    and all(isinstance(w, str) and w for w in extra)):
                return list(CLIMAX_WORDS_GAP_GENERIC) + list(extra)
    # 无 meta（现状）：返回历史 23 词原序表——与改前内联字面量逐字有序一致
    return list(_CLIMAX_WORDS_GAP_NO_META)


# ---------------------------------------------------------------------------
# core_keywords 覆盖匹配的中文近义簇（gap_analysis._keyword_covered 同义扩展）
# 值与历史字面量一致；仅清理 4 条死条目：sun/earth/dry 三键（英文键在中文
# core_keywords 语境不可达）+ "马车"值列表内的自映射项。
# 通用中文近义簇，非特化数据——暂无外置必要（见分析文档 A 类结论）。
# ---------------------------------------------------------------------------
KEYWORD_SYNONYMS: dict[str, list[str]] = {
    # 刺杀类
    "刺杀": ["刺", "杀", "寒光", "匕首", "袭击", "突袭", "遇害"],
    "刺": ["刺杀", "戳", "穿", "扎入"],
    "杀": ["刺杀", "击杀", "斩", "劈"],
    "寒光": ["剑光", "刀光", "银光", "闪光"],
    "匕首": ["短剑", "利刃", "兵刃"],
    "袭击": ["突袭", "攻击", "冲来", "杀来"],
    # 冲突类
    "冲突": ["对峙", "争吵", "打斗", "交锋", "矛盾"],
    "对峙": ["冲突", "相持", "怒视", "对视"],
    "打斗": ["交手", "过招", "搏斗", "争斗"],
    "交锋": ["对决", "比拼", "争锋"],
    # 逃离类
    "逃离": ["疾驰", "逃跑", "离开", "马车", "离去", "奔逃", "遁走"],
    "逃跑": ["逃离", "奔逃", "逃窜", "逃走"],
    "疾驰": ["飞奔", "疾奔", "奔跑", "策马"],
    "马车": ["车", "轿子", "车马"],
    # 死亡类
    "死亡": ["死", "亡", "断气", "瞳孔散大", "冰冷", "倒下", "毙命"],
    "死": ["死亡", "亡", "断气", "毙命"],
    "亡": ["死亡", "死", "灭亡"],
    "倒下": ["倒地", "跪倒", "瘫倒"],
    # 太阳/天气类
    "太阳": ["烈日", "骄阳", "火球", "日头"],
    "大地": ["地面", "田野", "土地", "田地"],
    "干旱": ["干裂", "燥热", "炙烤"],
}


__all__ = [
    "CLIMAX_WORDS_ARC",
    "CLIMAX_WORDS_GAP_GENERIC",
    "CLIMAX_WORDS_GAP_INJECT",
    "CLIMAX_WORDS_GAP_NOVEL_FALLBACK",
    "CLIMAX_INJECTED_GAP_SUFFIX",
    "CLIMAX_INJECTED_TAILS",
    "CLIMAX_INJECTED_PUNCH_PATTERN",
    "CLIMAX_INJECTED_MERGE_MARKER",
    "climax_words_for_gap",
    "strip_injected_suffixes",
    "KEYWORD_SYNONYMS",
]
