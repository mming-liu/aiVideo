# -*- coding: utf-8 -*-
"""角色 alias 注册表（v6.53，单一来源）.

背景（chapter_001 实测）：char_layout[].name / main_subject 完全跳过合规替换
——hook char_layout[0].name="刺客"（desc 已写"神秘人"）、shot3 name="船夫"
（desc 无船夫）、shot10 main_subject="庄仆"。下游用 char_layout 推人数名单、
找肖像、做锚匹配，未清洗名导致三方失配。

折叠方向（与 v6.53 计划的差异有据）：
- 威胁源 → **"神秘人"** 而非计划原稿的"威胁源"：desc 四字段经合规替换后
  统一是"神秘人"，折叠到同一形态才能保证 desc / char_layout / 脚本 identifier
  别名链三方可互查；"威胁源"是抽象词，会让 _extract_char_layout 的
  surface/position 结构化锚匹配丢失（v6.45 听香站水里兜底会退化）。
- 非核心配角 → "群演"（与 prompts/main.md v6.49 硬约束、redguo 人物数上限一致）。

幂等："神秘人"/"群演" 不在 key 里，二次折叠无变化。
"""
from __future__ import annotations

# 威胁源合规形态（sanitize_script_shots 把 desc 里的 刺客→神秘人；此处对齐）
THREAT_CANONICAL = "神秘人"
# 非核心配角合规形态（redguo 人物上限 alias）
EXTRA_CANONICAL = "群演"

CHARACTER_ALIASES: dict[str, str] = {
    # 威胁源 → 神秘人（与 SENSITIVE_SUBSTITUTIONS "刺客"→"神秘人" 同向）
    "刺客": THREAT_CANONICAL,
    "杀手": THREAT_CANONICAL,
    "黑衣人": THREAT_CANONICAL,
    "蒙面人": THREAT_CANONICAL,
    "蒙面剑客": THREAT_CANONICAL,
    "黑影": THREAT_CANONICAL,
    "匪": THREAT_CANONICAL,
    # 非核心配角 → 群演
    "老汉": EXTRA_CANONICAL,
    "老农": EXTRA_CANONICAL,
    "船夫": EXTRA_CANONICAL,
    "船家": EXTRA_CANONICAL,
    "庄仆": EXTRA_CANONICAL,
    "家丁": EXTRA_CANONICAL,
    "仆从": EXTRA_CANONICAL,
    "仆人": EXTRA_CANONICAL,
    "丫鬟": EXTRA_CANONICAL,
    "小厮": EXTRA_CANONICAL,
    "路人": EXTRA_CANONICAL,
    "百姓": EXTRA_CANONICAL,
    "众人": EXTRA_CANONICAL,
    "农夫": EXTRA_CANONICAL,
    "渔民": EXTRA_CANONICAL,
    "纤夫": EXTRA_CANONICAL,
    "孩子们": EXTRA_CANONICAL,
}


def apply_char_alias(name: str) -> str:
    """单名折叠：全字匹配优先，子串包含次之（"刺客从水中跃出"→"神秘人从水中跃出"）."""
    if not name:
        return name
    if name in CHARACTER_ALIASES:
        return CHARACTER_ALIASES[name]
    for old, new in CHARACTER_ALIASES.items():
        if old in name:
            return name.replace(old, new)
    return name


def alias_walk_shot(shot: dict) -> int:
    """就地折叠 shot 的 main_subject + char_layout[].name，返回改动数."""
    changed = 0
    if shot.get("main_subject"):
        old = shot["main_subject"]
        new = apply_char_alias(old)
        if new != old:
            shot["main_subject"] = new
            changed += 1
    for item in shot.get("char_layout") or []:
        if isinstance(item, dict) and item.get("name"):
            old = item["name"]
            new = apply_char_alias(old)
            if new != old:
                item["name"] = new
                changed += 1
    return changed


def alias_walk_script(script: dict, verbose: bool = False) -> dict:
    """折叠 script 全部 shots + hook 的 main_subject / char_layout[].name."""
    from shared.log_utils import verbose_write
    changed = 0
    for shot in script.get("shots", []) or []:
        changed += alias_walk_shot(shot)
    hook = script.get("hook")
    if isinstance(hook, dict):
        changed += alias_walk_shot(hook)
    if changed and verbose:
        verbose_write(f"[alias] main_subject/char_layout 折叠 {changed} 处（→神秘人/群演）",
                      log_file="step01.log")
    return script
