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
"""
from __future__ import annotations

import os
import re

# 刃器同义类：持有/否认判定时「匕首≈刀≈短刃」视为同类
_BLADE_WORDS = ("匕首", "短刃", "短刀", "刀", "剑")


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


def _is_blade(w: str) -> bool:
    return any(w in b or b in w for b in _BLADE_WORDS)


def _check_weapon_contradiction(prompt: str) -> list[str]:
    issues: list[str] = []
    denials = _weapon_denial(prompt)
    no_weapon_holders = {h for h, w in denials if w == "武器"}
    denied_weapons = {(h, w) for h, w in denials if w != "武器"}
    for weapon, holder in _weapon_possession(prompt):
        if holder in no_weapon_holders:
            issues.append(f"对称矛盾：断言「{weapon}在{holder}手中」又断言「{holder}手中没有武器」")
            continue
        for h, w in denied_weapons:
            if h == holder and (_is_blade(weapon) and _is_blade(w) or weapon == w):
                issues.append(f"对称矛盾：断言「{weapon}在{holder}手中」又断言「{holder}手中没有{w}」")
    return issues


def _check_shadow_contradiction(prompt: str) -> list[str]:
    """禁黑影/人影 + 前文描述需要黑影 → 对称矛盾.

    样本：「画右水中一道黑影上浮」（剧情伏笔）+「禁止出现任何其他人物、
    黑影、人影」→ 模型按禁令抹掉黑影，伏笔丢失。

    v6.50 精度修订：威胁不可见模板（style_prefix）在威胁源为黑影时会
    故意只禁「人影」保留「黑影」——此时仅当 ①禁令含「黑影」本身，或
    ②描述需要的是「人影」而禁令禁「人影」才算矛盾；「描述用黑影 +
    禁令只禁人影」是设计内组合，放行。
    """
    # 懒惰量词：取禁令子句里**最先出现**的被禁词（贪婪会把「黑影」吞进
    # 填充符、误取后面的「人影」——「禁止…黑影、人影」须判 banned=黑影）。
    # 人影带 (?<!真)：风格前缀「古风真人影视剧」跨词含「人影」子串，须排除。
    m = re.search(r"禁止[^\n。]{0,24}?(黑影|(?<!真)人影)", prompt)
    if not m:
        return []
    banned = m.group(1)
    head = prompt[: m.start()]
    needs_shadow = re.search(r"(一道|一团)?黑影", head) is not None
    needs_figure = re.search(r"(?<!真)人影", head) is not None
    if banned == "黑影" and needs_shadow:
        return ["对称矛盾：前文描述需要「黑影」（剧情威胁伏笔），禁止条款又禁掉它"]
    if banned == "人影" and needs_figure:
        return ["对称矛盾：前文描述需要「人影」，禁止条款又禁掉它"]
    return []


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


def find_contradictions(prompt: str) -> list[str]:
    """检测 prompt 内确定性自相矛盾，返回问题描述列表（空 = 通过）."""
    if not prompt or not isinstance(prompt, str):
        return []
    issues: list[str] = []
    issues += _check_weapon_contradiction(prompt)
    issues += _check_shadow_contradiction(prompt)
    issues += _check_duplicate_block(prompt)
    issues += _check_double_punctuation(prompt)
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
