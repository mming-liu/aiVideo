"""prompt_semantic.physics — 通用物理规则引擎.

核心思想：每条规则都是"在 entity graph 上跑检测 + 拼修复"。
不写死任何角色名/prop 名 —— 全部从 registry + 实体图动态查表。

数据流：
  detect(graph, ctx) → list[Violation]
  fix(violation, graph, ctx) → FixAction
  apply_fixes(graph, violations) → fixed_text

新增物理规则：
  1. 在 RULES 列表加 PhysicsRule 实例
  2. detect 函数接收 EntityGraph + ctx（可访问 scene/surfaces）
  3. fix 函数返回 FixAction(find_pattern, replace)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional, Tuple

from .entities import (
    Character, Prop, SpatialRelation, EntityGraph,
    PROP_STATE_DROPPED, PROP_STATE_HELD, PROP_STATE_HANGING,
    PROP_STATE_FLOATING, PROP_STATE_LEANING,
    REL_IN_WATER, REL_ABOVE, REL_FLOAT, REL_ON,
)
from .registry import (
    classify_pose, get_surfaces, get_anchor_dedup_phrases,
    is_threat_name, is_weapon_name, THREAT_KEYWORDS, WEAPON_KEYWORDS,
    ABOVE_WATER_PAT, get_invalid_prop_patterns, get_prop_spec,
)


# ============================================================================
# 1. Violation / FixAction 数据结构
# ============================================================================

@dataclass
class Violation:
    """物理/逻辑规则违反."""
    rule: str                            # 规则名
    entity_name: str                     # 违反实体
    msg: str                             # 描述
    severity: str = "WARN"               # INFO/WARN/HARD
    find_pattern: str = ""               # 原始文本片段
    replace: str = ""                    # 替换文本
    priority: int = 0                    # 修复优先级（高者优先）


# ============================================================================
# 2. 物理规则 1：脱手道具必须有支撑面
# ============================================================================

def detect_dropped_prop_no_support(graph: EntityGraph, ctx: "PhysicsContext") -> list[Violation]:
    """Rule: 任何 PROP_REGISTRY 登记的 prop 名 + 紧邻无效姿态词 → 物理违反.

    不依赖 prop.state 推断（无效姿态词本身已经把 state 写成 hanging/floating，
    再用 state 过滤会漏检）。直接扫文本：prop 名 24 字符内有无 INVALID_PROP_POSITIONS
    命中即触发。

    修法：把"挂/漂浮/斜伸"类描述改为"倾倒在 {owner} 身侧 {surface}"，
    find_pattern 扩展到包含整段（如"酒壶已倾倒脱手，挂在船舷"）。
    """
    violations: list[Violation] = []
    invalid_pats = get_invalid_prop_patterns()
    text = graph.raw_text
    if not text:
        return violations

    seen_props: set[str] = set()
    for prop in graph.props:
        if prop.name in seen_props:
            continue
        spec = get_prop_spec(prop.name)
        if not spec:
            continue
        # 找 prop 名在 text 中所有出现位置
        prop_pos = 0
        triggered = False
        while True:
            p = text.find(prop.name, prop_pos)
            if p < 0:
                break
            prop_pos = p + len(prop.name)
            # 扫 prop 后 24 字符内的无效姿态词
            window_start = p
            window_end = min(len(text), p + len(prop.name) + 24)
            window = text[window_start:window_end]
            for kind, pat in invalid_pats.items():
                m = pat.search(window)
                if not m:
                    continue
                # 命中：构造 find/replace
                owner = spec.default_owner or ""
                if not owner:
                    main_char = _pick_main_character(graph)
                    owner = main_char.name if main_char else "角色"
                surface = _pick_surface(text, ctx.surfaces)
                # 相对 text 的绝对位置
                abs_viol_start = window_start + m.start()
                abs_viol_end = window_start + m.end()
                # find_pattern 扩到包含整个无效短语
                find_segment = _extract_prop_phrase_with_violation(
                    text, p, abs_viol_start, abs_viol_end,
                )
                replace = _build_dropped_replacement(prop.name, owner, surface, kind, spec)
                violations.append(Violation(
                    rule="dropped_prop_must_have_support",
                    entity_name=prop.name,
                    msg=f"{prop.name} 处于{_kind_to_zh(kind)}无支撑态 → 倾倒在 {owner} 身侧 {surface}",
                    severity="WARN",
                    find_pattern=find_segment,
                    replace=replace,
                    priority=50,
                ))
                triggered = True
                break
            if triggered:
                break
        if triggered:
            seen_props.add(prop.name)
    return violations


def _extract_prop_phrase_with_violation(
    text: str, prop_pos: int,
    abs_viol_start: int, abs_viol_end: int,
) -> str:
    """提取包含 prop 名 + 整个无效短语的最小 find_pattern.

    abs_viol_start/end 是相对 text 的绝对位置.
    """
    end = abs_viol_end
    # 扩到下一个 ， 或 。
    while end < len(text) and text[end] not in "，。、；！？\n":
        end += 1
    # 起始：prop 名开头（包含前面的"已脱手"等修饰）
    start = prop_pos
    # 倒推到上一个 ， 或句首
    while start > 0 and text[start - 1] not in "，。、；！？\n":
        start -= 1
    seg = text[start:end]
    return seg.strip()


def _kind_to_zh(kind: str) -> str:
    return {
        "hang_outside_gunwale": "挂在船舷外",
        "floating_above_water": "漂浮/斜伸船舷",
        "hang_in_air": "悬挂空中",
        "lean_outside": "斜倚船舷外",
    }.get(kind, "无支撑")


def _is_near(text: str, prop_name: str, target_pos: int, window: int = 12) -> bool:
    """target_pos 是否在 prop_name 出现的 window 字符内."""
    pos = text.find(prop_name)
    while pos >= 0:
        if abs(pos - target_pos) <= window + len(prop_name):
            return True
        pos = text.find(prop_name, pos + 1)
    return False


def _extract_prop_phrase(text: str, near_pos: int, prop_name: str) -> str:
    """提取包含 prop_name 的最小短语（用于 find/replace）."""
    pos = text.find(prop_name, max(0, near_pos - 20))
    if pos < 0:
        return ""
    end = pos + len(prop_name)
    # 扩到最近的 ，。 边界
    while end < len(text) and text[end] not in "，。、；！？\n":
        end += 1
    return text[pos:end]


def _pick_surface(text: str, surfaces: Tuple[str, ...]) -> str:
    """从 text 出现的 surface 词里挑一个（按 scene 顺序）."""
    for s in surfaces:
        if s in text:
            return s
    return surfaces[0] if surfaces else "甲板"


def _pick_main_character(graph: EntityGraph) -> Optional[Character]:
    """取本镜主角色（role=主 或 第一个）."""
    for c in graph.characters:
        if c.role == "主":
            return c
    return graph.characters[0] if graph.characters else None


def _build_dropped_replacement(
    prop_name: str, owner: str, surface: str, kind: str, spec
) -> str:
    """按 prop 类别生成"脱手 + 落在承重面"模板.

    模板分两类：
      - 杆类（钓杆/竹箫）：斜倚在 {surface}（紧贴 {owner} 身体），杆尖自然垂下没入水面
      - 其他：{prop}已倾倒脱手，倾倒在 {surface}（紧贴 {owner} 身体）
    """
    # 杆类 prop（带"杆/竿/箫/笛"等）
    is_rod = any(w in prop_name for w in ("杆", "竿", "箫", "笛"))
    if is_rod:
        return (
            f"{prop_name}已脱手滑落，斜倚在{surface}（紧贴{owner}身体），"
            f"杆尖自然垂下没入水面"
        )
    # 默认模板
    return f"{prop_name}已倾倒脱手，倾倒在{surface}（紧贴{owner}身体）"


# ============================================================================
# 3. 物理规则 2：角色 anchor 块内同 phrase 重复
# ============================================================================

def detect_anchor_duplicate_phrase(graph: EntityGraph, ctx: "PhysicsContext") -> list[Violation]:
    """Rule: 角色 anchor 块（"X：Y" 格式）内同 phrase 出现 2+ 次 → strip 第二次.

    通用化：从 char_layout + characters_bank 抽所有角色名，逐个扫其 anchor 块。
    """
    violations: list[Violation] = []
    text = graph.raw_text
    if not text:
        return violations
    dedup_phrases = get_anchor_dedup_phrases()

    for char in graph.characters:
        # 找 "X：Y" anchor 块
        pat = re.compile(re.escape(char.name) + r"：([^。]*?)(?=。|$)")
        for m in pat.finditer(text):
            block = m.group(1)
            # 块内同 phrase 出现 2+ 次 → 记录
            seen: set[str] = set()
            for phrase in dedup_phrases:
                occurrences = [m.start() for m in re.finditer(re.escape(phrase), block)]
                if len(occurrences) >= 2:
                    # 第二次出现的位置（相对 text 的绝对 offset）
                    second_phrase_pos = m.start(1) + occurrences[1]
                    # 整段短语（含括号）作为 find_pattern
                    end_pos = _find_phrase_end(text, second_phrase_pos, len(phrase))
                    find_seg = text[second_phrase_pos:end_pos]
                    if not find_seg.strip():
                        continue
                    violations.append(Violation(
                        rule="character_anchor_phrase_dedup",
                        entity_name=char.name,
                        msg=f"{char.name} anchor 块内「{phrase}」重复 {len(occurrences)} 次",
                        severity="WARN",
                        find_pattern=find_seg,
                        replace="",  # 删除（replace 空）
                        priority=30,
                    ))
                    seen.add(phrase)
                    break  # 一块只报一次
    return violations


def _find_phrase_end(text: str, start: int, phrase_len: int) -> int:
    """找到包含括号的 phrase 完整边界."""
    end = start + phrase_len
    # 扩到下一个 ，  或  ）  边界
    while end < len(text) and text[end] not in "，；":
        if text[end] == "）":
            end += 1
            break
        end += 1
    return end


# ============================================================================
# 4. 物理规则 3：威胁源"头在水下 + 武器在头顶"矛盾
# ============================================================================

def detect_underwater_contradiction(graph: EntityGraph, ctx: "PhysicsContext") -> list[Violation]:
    """Rule: 威胁源 4 要素并存（刺客/神秘人 + 露出水面 + 刀尖/匕首 + 头顶）→ 矛盾.

    修法：把整段"刺客...露出水面...刀尖...头顶..."重写为"刺客完全潜伏水下"。
    """
    violations: list[Violation] = []
    text = graph.raw_text
    if not text:
        return violations
    if not ctx.is_hook:
        # 非钩子：仅当 char_layout 含威胁源 + position="水下" 时触发
        if not _has_threat_underwater_in_layout(graph):
            return violations

    has_threat = any(is_threat_name(c.name) for c in graph.characters) or \
                 any(kw in text for kw in THREAT_KEYWORDS)
    if not has_threat:
        return violations

    has_above_water = bool(ABOVE_WATER_PAT.search(text))
    has_weapon = any(kw in text for kw in WEAPON_KEYWORDS)
    has_top_of_head = "头顶" in text or "头上" in text
    if not (has_above_water and has_weapon and has_top_of_head):
        return violations

    # 找威胁源名（用 graph.characters 里的真实名，fallback 到 "刺客"）
    threat_name = _pick_threat_name(graph, text)
    find_seg = _extract_underwater_segment(text, threat_name)
    replace = (
        f"{threat_name}完全潜伏水面下未露出任何身体部位，"
        f"匕首在{threat_name}手中位于水面下"
    )
    violations.append(Violation(
        rule="underwater_head_no_weapon_above",
        entity_name=threat_name,
        msg=f"{threat_name} 头在水下但武器在头顶 → 物理矛盾",
        severity="HARD",
        find_pattern=find_seg,
        replace=replace,
        priority=100,
    ))
    return violations


def _has_threat_underwater_in_layout(graph: EntityGraph) -> bool:
    for c in graph.characters:
        if is_threat_name(c.name):
            pos = (c.position or "")
            if "水下" in pos or "水面下" in pos:
                return True
    return False


def _pick_threat_name(graph: EntityGraph, text: str) -> str:
    """从 graph 或 text 找威胁源名."""
    for c in graph.characters:
        if is_threat_name(c.name):
            return c.name
    for kw in THREAT_KEYWORDS:
        if kw in text:
            return kw
    return "刺客"


def _extract_underwater_segment(text: str, threat_name: str) -> str:
    """抽"威胁源名...露出水面...刀尖...头顶..."的整段."""
    pos = text.find(threat_name)
    if pos < 0:
        return ""
    end = len(text)
    for sep in ("。", "；"):
        sep_idx = text.find(sep, pos)
        if 0 < sep_idx < end:
            end = sep_idx
    return text[pos:end]


# ============================================================================
# 5. 物理规则注册表
# ============================================================================

@dataclass
class PhysicsRule:
    """单条物理规则."""
    name: str
    detect: Callable[[EntityGraph, "PhysicsContext"], list[Violation]]
    description: str = ""


@dataclass
class PhysicsContext:
    """物理规则执行上下文."""
    scene: str = ""
    surfaces: Tuple[str, ...] = ()
    is_hook: bool = False
    # 保留扩展位（未来：frame_kind=first/last、is_in_repair_loop 等）


RULES: list[PhysicsRule] = [
    PhysicsRule(
        name="dropped_prop_must_have_support",
        detect=detect_dropped_prop_no_support,
        description="脱手道具必须有承重面（不能挂/漂浮/斜伸船舷）",
    ),
    PhysicsRule(
        name="character_anchor_phrase_dedup",
        detect=detect_anchor_duplicate_phrase,
        description="角色 anchor 块内同 phrase 重复 → strip 第二次",
    ),
    PhysicsRule(
        name="underwater_head_no_weapon_above",
        detect=detect_underwater_contradiction,
        description="威胁源头在水下 + 武器在头顶 → 物理矛盾",
    ),
]


def run_all(graph: EntityGraph, ctx: PhysicsContext) -> list[Violation]:
    """跑所有规则，返回 violations 列表."""
    violations: list[Violation] = []
    for rule in RULES:
        try:
            violations.extend(rule.detect(graph, ctx))
        except Exception:
            pass
    return violations


__all__ = [
    "Violation",
    "PhysicsRule",
    "PhysicsContext",
    "RULES",
    "run_all",
    "detect_dropped_prop_no_support",
    "detect_anchor_duplicate_phrase",
    "detect_underwater_contradiction",
]
