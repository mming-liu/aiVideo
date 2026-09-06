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
    "climax_words_for_gap",
    "KEYWORD_SYNONYMS",
]
