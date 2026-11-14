# -*- coding: utf-8 -*-
"""shared.prompt_contradiction — 图片 prompt 提交前矛盾自检器（v6.50，08-28）.

背景：一个月跑批实测，发给 Seedream 的最终 prompt 已堆积成自相矛盾的
"法条"，模型无法同时满足只能抽奖（08-26 step02a.log 实锤样本）：
  - 「【道具锚定】神秘人匕首在杨旭手中」vs「【武器归属】杨旭手中没有武器，
    没有刀，没有剑，没有短刃」（同 holder 武器持有/否认对称矛盾）
  - 「禁止出现任何其他人物、黑影、人影」vs 剧情核心伏笔恰是水下黑影
    （禁黑影 + 需要黑影）→ 图里黑影消失
  - 角色描述块被整体重复注入两遍；「。。」双句号

本模块做**高精度**确定性检测（只报确定矛盾，不做语义猜测），在
vimax_bridge 组装 ff_desc_final / lf_desc_final 之后、提交图片模型之前调用。

fail-fast 策略：默认（PROMPT_CONTRADICTION_STRICT=1）发现矛盾即抛
PromptContradictionError，让该帧生成当场失败——调试期额度换质量，
矛盾 prompt 重试 N 次也是同一结果；设 0 降级为告警放行。

**词表与内容无关**（2026-11-14）：四组词原先写死在函数体里，且是从某一本书的
真实 prompt 提炼的措辞——换书即失效。现已外置到
`shared/prompt_contradiction_lexicon.py`（通用中文视觉词汇作默认值，项目级
覆盖走 `PROMPT_CONTRA_LEXICON`），判据本身只保留**结构**：
「同 holder 的持有/否认」「禁令禁掉前文需要的东西」「前态与伤情结束态同现」。
"""
from __future__ import annotations

import os
import re

from shared import prompt_contradiction_lexicon as _lex

# 刃器同义类：持有/否认判定时「匕首≈刀≈短刃」视为同类（默认值，可被词表覆盖）
_BLADE_WORDS = _lex.DEFAULTS["blade_terms"]


class PromptContradictionError(RuntimeError):
    """prompt 存在确定性自相矛盾（fail-fast）."""


def _weapon_possession(prompt: str) -> list[tuple[str, str]]:
    """抽取「(武器)在(人名)手中/手里」持有断言."""
    out = []
    for m in re.finditer(r"([\u4e00-\u9fff]{1,4})在([\u4e00-\u9fff]{2,5})(?:手中|手里)", prompt):
        weapon, holder = m.group(1), m.group(2)
        if weapon in ("他", "她", "其"):  # 代词不是武器
            continue
        out.append((weapon, holder))
    return out


def _weapon_denial(prompt: str) -> list[tuple[str, str]]:
    """抽取「(人名)手中没有(武器/具体武器名)」否认断言.

    兼容模板写法：「杨旭手中没有武器，没有刀，没有剑，没有短刃」——
    后续「没有X」沿用最近出现的 holder。
    """
    out: list[tuple[str, str]] = []
    holder = ""
    for m in re.finditer(r"([\u4e00-\u9fff]{2,5})手中没有([\u4e00-\u9fff]{1,4})|没有([\u4e00-\u9fff]{1,4})", prompt):
        if m.group(1):
            holder = m.group(1)
            out.append((holder, m.group(2)))
        elif holder and m.group(3):
            out.append((holder, m.group(3)))
    return out


def _is_blade(w: str, blade_terms=None) -> bool:
    return any(w in b or b in w for b in (blade_terms or _BLADE_WORDS))


def _check_weapon_contradiction(prompt: str, lex: dict | None = None) -> list[str]:
    lex = lex or _lex.current()
    blades = lex["blade_terms"]
    issues: list[str] = []
    denials = _weapon_denial(prompt)
    no_weapon_holders = {h for h, w in denials if w == "武器"}
    denied_weapons = {(h, w) for h, w in denials if w != "武器"}
    for weapon, holder in _weapon_possession(prompt):
        if holder in no_weapon_holders:
            issues.append(f"对称矛盾：断言「{weapon}在{holder}手中」又断言「{holder}手中没有武器」")
            continue
        for h, w in denied_weapons:
            if h == holder and (_is_blade(weapon, blades) and _is_blade(w, blades) or weapon == w):
                issues.append(f"对称矛盾：断言「{weapon}在{holder}手中」又断言「{holder}手中没有{w}」")
    return issues


def _ignored_spans(text: str, ignores) -> list[tuple[int, int]]:
    """`text` 里属于"忽略子串"的区间（命中落在这些区间内就不算禁令命中）."""
    spans: list[tuple[int, int]] = []
    for ign in ignores or ():
        start = text.find(ign)
        while start >= 0:
            spans.append((start, start + len(ign)))
            start = text.find(ign, start + 1)
    return spans


def _banned_terms(clause: str, lex: dict) -> list[str]:
    """从「禁止…」子句里取被禁名词（`explicit` 查表 / `enumerate` 机械切词）."""
    if lex["prohibition_mode"] == "explicit":
        return list(lex["prohibition_terms"])
    # enumerate：按非汉字切分，剥掉标记词与动词/量词前缀，留 2–4 字名词，
    # 去掉过泛词（人物/画面/文字…）。切词必然有噪声，所以默认不启用。
    noise = [lex["prohibition_clause_marker"]] + list(lex["prohibition_token_prefixes"])
    stop = set(lex["prohibition_stop_terms"])
    out: list[str] = []
    for seg in re.split(r"[^\u4e00-\u9fff]+", clause):
        for word in noise:
            seg = seg.replace(word, "")
        if 2 <= len(seg) <= 4 and seg not in stop and seg not in out:
            out.append(seg)
    return out


def _check_prohibition_conflict(prompt: str, lex: dict | None = None) -> list[str]:
    """禁令条款禁掉前文正要出现的东西（禁黑影 + 需要黑影 → 对称矛盾）.

    样本：「画右水中一道黑影上浮」（剧情伏笔）+「禁止出现任何其他人物、
    黑影、人影」→ 模型按禁令抹掉黑影，伏笔丢失。

    判据是**结构**的：找出「禁止」子句里被禁的实体，再看同一个实体是否出现在
    子句之前（前文描述要它）。`真人` 这类风格前缀造成的跨词命中按词表排除。
    """
    lex = lex or _lex.current()
    marker = lex["prohibition_clause_marker"]
    start = prompt.find(marker)
    if start < 0:
        return []
    clause = prompt[start:start + int(lex["prohibition_clause_max_chars"])]
    for stop in ("。", "\n", "；", ";", "！", "!"):
        cut = clause.find(stop)
        if cut > 0:
            clause = clause[:cut]
    head = prompt[:start]
    ignores = lex["prohibition_ignore_substrings"]
    spans = _ignored_spans(head, ignores)
    # 只认「确实写在这条禁令里」的实体：词表是候选集，条款才是事实来源。
    for term in (t for t in _banned_terms(clause, lex) if t in clause):
        pos = head.find(term)
        while pos >= 0:
            # 与忽略子串**有重叠**即不算命中：`真人影视剧` 跨词会切出「人影」，
            # 这个词来自风格前缀，不是被禁的实体。
            if not any(pos < b and pos + len(term) > a for a, b in spans):
                return ["prohibition_conflict（对称矛盾）：前文描述需要「%s」，"
                        "禁止条款又禁掉它" % term]
            pos = head.find(term, pos + 1)
    return []


#: 兼容旧名：语义已泛化为「任意禁令实体」，不只是黑影。
_check_shadow_contradiction = _check_prohibition_conflict


def _check_duplicate_block(prompt: str) -> list[str]:
    """长文本块重复注入（≥24 字相同片段出现两次）→ 报重复.

    样本：角色描述块「杨旭：国字脸，剑眉星目，…手持钓杆」整体注入两遍
    （08-26 lf prompt 实测）。24 字滑窗逐位采样（步长 1——重复块偏移不可
    预知，跳步会错峰漏检）重复且不重叠即命中；窗口足够长以避开正常模板
    短句（「仰面朝上双目紧闭，头颈僵直不侧转」16 字）。
    """
    issues: list[str] = []
    if len(prompt) < 48:
        return issues
    seen: dict[str, int] = {}
    reported = False
    for i in range(0, len(prompt) - 24):
        win = prompt[i:i + 24]
        j = seen.get(win)
        if j is not None and i - j >= 24 and not reported:
            issues.append(f"描述块重复注入：「{win[:24]}…」出现多次")
            reported = True
        elif j is None:
            seen[win] = i
    return issues


def _check_double_punctuation(prompt: str) -> list[str]:
    if "。。" in prompt or "，，" in prompt:
        return ["双句号/双逗号残留（模板拼接痕迹）"]
    return []


def _check_ff_lf_stage_conflict(prompt: str, lex: dict | None = None) -> list[str]:
    """ff 既含「动作起始态」前置词 + 伤情结束态（且落在身体/衣物上）→ 矛盾.

    现场：prompt 同时含「闭目吟哦/仰卧未觉」（前态）和「伤处…心口一片暗红血迹
    洇开」（后态）——模型拿到自相矛盾的 prompt，自由发挥成"船里灌水/人在船外"。

    判定策略（避免误伤）：三个条件同时成立才报——

    1. 存在前态词（还没发生/将要发生）；
    2. 存在伤情结束态词，**且**该词左右 `injury_anchor_window` 字内出现身体或
       衣物词（"桌上一滩血"不算"角色已被刺"的画面）；
    3. 前态词与伤情词的间距 ≤ `pre_state_max_distance`（同一个人同一段描写，
       隔了整段的两处描述不构成自相矛盾）。

    纯前态、纯后态、以及伤情不在人身上的情形都不报。
    """
    lex = lex or _lex.current()
    pre_pos = []
    for term in lex["pre_state_terms"]:
        start = prompt.find(term)
        while start >= 0:
            pre_pos.append((start, start + len(term), term))
            start = prompt.find(term, start + 1)
    if not pre_pos:
        return []
    window = int(lex["injury_anchor_window"])
    limit = int(lex["pre_state_max_distance"])
    body = lex["body_or_garment_terms"]
    for term in lex["injury_terms"]:
        pos = prompt.find(term)
        while pos >= 0:
            seg = prompt[max(0, pos - window):pos + len(term) + window]
            if any(b in seg for b in body):
                for a, b, pre in pre_pos:
                    # 两段之间的**净间距**（重叠记 0）——隔了整段的两处描写不算矛盾
                    gap = max(0, pos - b, a - (pos + len(term)))
                    if gap <= limit:
                        return ["ff_lf_stage_conflict（ff 同时含前态词「%s」"
                                "与伤情结束态「%s」，模型无法同时满足）"
                                % (pre, term)]
            pos = prompt.find(term, pos + 1)
    return []


def find_contradictions(prompt: str) -> list[str]:
    """检测 prompt 内确定性自相矛盾，返回问题描述列表（空 = 通过）."""
    if not prompt or not isinstance(prompt, str):
        return []
    lex = _lex.current()
    issues: list[str] = []
    issues += _check_weapon_contradiction(prompt, lex)
    issues += _check_prohibition_conflict(prompt, lex)
    issues += _check_duplicate_block(prompt)
    issues += _check_double_punctuation(prompt)
    issues += _check_ff_lf_stage_conflict(prompt, lex)
    return issues


def strict_mode() -> bool:
    """PROMPT_CONTRADICTION_STRICT=1（默认）→ 矛盾即抛错拦帧."""
    return os.getenv("PROMPT_CONTRADICTION_STRICT", "1").strip() != "0"


def assert_no_contradictions(prompt: str, label: str = "") -> None:
    """提交前自检：发现矛盾 → 严格模式抛 PromptContradictionError，否则告警.

    label 用于日志定位（如 "shot0 首帧"）。
    """
    issues = find_contradictions(prompt)
    if not issues:
        return
    prefix = f"[{label}] " if label else ""
    detail = "；".join(issues)
    try:
        from shared.tee_logger import get_logger
        log = get_logger()
        if log:
            log.warn(f"{prefix}[CONTRADICTION] prompt 自相矛盾: {detail}")
    except Exception:
        pass
    if strict_mode():
        raise PromptContradictionError(f"{prefix}prompt 自相矛盾（fail-fast，"
                                       f"设 PROMPT_CONTRADICTION_STRICT=0 可降级放行）: {detail}")
