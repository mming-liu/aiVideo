"""拆分镜内容相似度检测（v6.58 A7, 2026-09-03）.

检测 step01 拆镜后相邻子镜的内容重复——split_visuals 的「视觉差异化」
只改 ff/lf/prompt 上的装饰（景别/过肩/情绪/焦点），但**核心动作词**（拔剑/
格挡/跃起）是同一件事。08-29 实测 shot10/10.5、shot11.5/12.5 等都是这样。

为什么不用 jieba：依赖未安装；用字符 bigram 重叠足够捕捉「连续动作词」重复。

算法：
  - 取 shot_a.motion_desc + shot_a.prompt + shot_a.lf_desc 拼接作「动作基底 A」
  - 取 shot_b 同三段作「动作基底 B」
  - 字符 bigram 集合重叠度 = |A∩B| / |A∪B|（Jaccard）
  - 阈值 ≥ 0.7 视为「核心动作重复」（中文字符 bigram 自然容错少量同义改写）

设计原则：
  - 纯函数，无副作用
  - 不依赖任何 NLP 库（jieba/spacy 都不在 requirements）
  - 短文本（< 10 字符）直接返回 0（避免空集合除零）
"""
from __future__ import annotations


def _char_bigrams(text: str) -> set[str]:
    """字符 2-gram 集合（中英文都适用）."""
    text = (text or "").strip()
    if len(text) < 2:
        return set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def jaccard_bigram(text_a: str, text_b: str) -> float:
    """两段文本的字符 bigram Jaccard 相似度（0-1）.

    0: 完全不重叠；1: 字符 bigram 完全相同.
    空文本 / 极短文本（< 2 字符）→ 0.0
    """
    a, b = _char_bigrams(text_a), _char_bigrams(text_b)
    if not a or not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def shot_action_text(shot: dict) -> str:
    """从 shot 抽取「动作基底」文本：motion_desc + prompt + lf_desc."""
    parts: list[str] = []
    for fld in ("motion_desc", "prompt", "lf_desc"):
        v = (shot.get(fld) or "").strip()
        if v:
            parts.append(v)
    return "".join(parts)


def split_pair_similarity(shot_a: dict, shot_b: dict) -> float:
    """两 shot 的「动作基底」Jaccard 相似度.

    适用于拆分镜对（_split_from 关系）。空 shot 返 0.
    """
    a, b = shot_action_text(shot_a), shot_action_text(shot_b)
    if not a or not b:
        return 0.0
    return jaccard_bigram(a, b)


def is_split_pair(shot: dict) -> bool:
    """shot 是否被拆出来（带 _split_from 标记，且索引为副镜）.

    注意：float 8.0 == int 8 在 Python 是 True，必须用字符串判定副镜。
    副镜索引：1.5 / 7.5 / 8.0 / 8.25（v6.50 +0.5 / +0.25 / +0.125）
    """
    if not isinstance(shot, dict):
        return False
    if "_split_from" not in shot:
        return False
    idx = shot.get("index")
    if idx is None:
        return False
    # '7.5' / '8.0' / '8.25' 都含 '.'；'7' / '8' / '9' 不含
    return "." in str(idx)


def text_sim_ratio(text_a: str, text_b: str) -> float:
    """去标点空白后的 difflib 字符级相似度（0-1）.

    09-13 自 shared/vimax_bridge 迁入（单一事实源）：kf↔ff 同源判定共用
    （vimax_bridge._compose_kf_refs 与 constraints/kf_frame_redundancy）。
    与 jaccard_bigram 的区别：difflib 保序，对「同骨架微调语序」更敏感；
    去标点防「同文异标点」拉低分值。
    """
    import difflib
    import re as _re
    _clean = lambda s: _re.sub(r"[\s，。；：、（）()【】\"'“”…—-]", "", s or "")
    a, b = _clean(text_a), _clean(text_b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------------------
# kf 帧级冗余阈值注册表（09-13 审查收敛，2026-09-13）
# 单一事实源：shared/vimax_bridge._compose_kf_refs 与
# step01_script_gen/constraints/kf_frame_redundancy 都从这里 import。
# chapter_001 实测校准：
#   - 问题对（hook climax kf0 vs ff）text_sim_ratio = 0.508
#   - 正常 mid 帧 ≤ 0.193
#   - pre 帧 0.62-0.90（≈ff 是阶段语义设计，豁免）
# 阈值 0.40 居中，正常/异常余量都够；相邻 kf 字面重复阈值 0.90 防误报。
KF_FF_SIM_THRESHOLD = 0.40
KF_PAIR_SIM_THRESHOLD = 0.90
# 阶段豁免：pre≈ff、exit/after≈lf 是 v6.34 阶段语义设计，相似不触发替换。
KF_FF_ANCHOR_EXEMPT_STAGES = ("pre", "exit", "after")


__all__ = [
    "jaccard_bigram",
    "shot_action_text",
    "split_pair_similarity",
    "is_split_pair",
    "text_sim_ratio",
    "KF_FF_SIM_THRESHOLD",
    "KF_PAIR_SIM_THRESHOLD",
    "KF_FF_ANCHOR_EXEMPT_STAGES",
]
