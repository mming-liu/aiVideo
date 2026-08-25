"""prompt_semantic.extractor — 文本 → 实体图.

不硬编码角色名/prop 名 —— 全部从 char_layout + PROP_REGISTRY 查表.

抽取策略：
  1. 角色：扫 char_layout（结构化）+ 文本锚定（"X：Y" anchor 块）
  2. 道具：扫 PROP_REGISTRY 所有别名 + 文本出现
  3. 关系：按动词 + 承重面词表抽取
"""
from __future__ import annotations

import re
from typing import Tuple

from step01_script_gen.scene_anchors import (
    BASE_POS_WORDS,
    scene_anchors_for,
)

from .entities import (
    Character,
    Prop,
    SpatialRelation,
    EntityGraph,
    PROP_STATE_DROPPED,
    PROP_STATE_HELD,
    PROP_STATE_HANGING,
    PROP_STATE_FLOATING,
    PROP_STATE_LEANING,
    REL_ON, REL_INSIDE, REL_HANG, REL_LEAN, REL_FLOAT, REL_IN_WATER, REL_ABOVE, REL_BELOW,
)
from .registry import (
    PROP_REGISTRY,
    is_known_prop,
    classify_pose,
    get_surfaces,
    THREAT_KEYWORDS,
    WEAPON_KEYWORDS,
    is_threat_name,
    get_prop_spec,
)


# ============================================================================
# 1. 角色抽取
# ============================================================================

def _extract_characters_from_layout(
    char_layout: list[dict], characters_bank: dict | None = None
) -> list[Character]:
    """从 shot["char_layout"] 结构化字段抽取 Character.

    char_layout 示例: [{"name": "杨旭", "position": "船头", "facing": "面向镜头", "role": "主"}]
    characters_bank 形如: {ident: {"static_features": ..., "dynamic_features": ...}}
    """
    if not isinstance(char_layout, list):
        return []
    characters: list[Character] = []
    for item in char_layout:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        position = str(item.get("position", "")).strip()
        facing = str(item.get("facing", "")).strip()
        role = str(item.get("role", "")).strip()

        # 查 character_bank 取 static/dynamic
        static = ""
        dynamic = ""
        if characters_bank:
            entry = characters_bank.get(name) or {}
            static = str(entry.get("static_features", "") or "")
            dynamic = str(entry.get("dynamic_features", "") or "")

        is_threat = is_threat_name(name) or role in ("威胁", "threat", "antagonist", "主-威胁", "次-威胁")

        # 抽取发型状态（dynamic 含发型词）
        hair_state = _extract_hair_state(dynamic)

        characters.append(Character(
            name=name,
            static_features=static,
            dynamic_features=dynamic,
            position=position,
            facing=facing,
            role=role,
            hair_state=hair_state,
            is_threat=is_threat,
        ))
    return characters


_HAIR_STATE_KW = (
    "披头散发", "披发", "散发", "乱发", "发髻散落", "蓬发", "黑发披散",
)


def _extract_hair_state(dynamic: str) -> str:
    for kw in _HAIR_STATE_KW:
        if kw in dynamic:
            return kw
    return ""


# ============================================================================
# 2. 道具抽取
# ============================================================================

def _extract_props_from_text(text: str) -> list[Prop]:
    """从文本中扫所有 PROP_REGISTRY 已登记 prop 出现位置.

    关键：
      1. 整词匹配（避免"宝剑"误匹配"剑"）
      2. 同 spec 多别名去重（"油纸伞"/"伞" → 1 个 Prop，按最长别名展示）

    不抽未登记词（避免误报"角色名当 prop 名"等）。
    """
    if not text:
        return []
    # 按 spec_key 聚合（一个 spec 只产一个 Prop，即使有多个别名）
    spec_hits: dict[str, tuple[str, int]] = {}  # spec_key → (matched_name, pos)
    for prop_name in _PROP_NAME_SET:
        for m in _WORD_RE.finditer(text):
            pass
        # 整词匹配：prop_name 出现时，左右是 non-word 字符或文本边界
        for match in _find_whole_word(text, prop_name):
            spec = get_prop_spec(prop_name)
            if spec is None:
                continue
            # 找 spec_key（从 _PROP_NAME_TO_SPEC 反查）
            spec_key = _PROP_NAME_TO_KEY.get(prop_name)
            if spec_key is None:
                continue
            if spec_key not in spec_hits:
                spec_hits[spec_key] = (prop_name, match)
    # 构造 Prop 列表（每个 spec 1 个 Prop，用 names[0] 作为展示名）
    props: list[Prop] = []
    for spec_key, (matched_name, match_pos) in spec_hits.items():
        spec = PROP_REGISTRY[spec_key]
        # 用 names[0] 作为展示名（PROP_REGISTRY 的首个 name 视为 canonical）
        display_name = spec.names[0]
        state, attached_to = _infer_prop_state(text, display_name, match_pos)
        props.append(Prop(name=display_name, state=state, attached_to=attached_to))
    return props


_PROP_NAME_SET: set[str] = set()
_PROP_NAME_TO_KEY: dict[str, str] = {}
for _spec_key, _spec in PROP_REGISTRY.items():
    for _name in _spec.names:
        _PROP_NAME_SET.add(_name)
        _PROP_NAME_TO_KEY[_name] = _spec_key


# 中文整词匹配：prop 名前后是中文/标点（非 ASCII word char）
import re as _re_word
_WORD_RE = _re_word.compile(r"[一-鿿，。、；！？\s\.\!\?\,\;]")


def _find_whole_word(text: str, prop_name: str) -> list[int]:
    """返回 prop_name 在 text 中所有"有效"出现位置.

    中文不像英文有空格分词，因此采取以下启发式：
      - prop_name 长度 ≥ 3 → 直接 substring 匹配（"油纸伞"/"钓杆"/"酒壶"等）
      - prop_name 长度 = 2 → 要求紧邻左右至少一边是标点/空白/边界
        （避免"剑"匹配"宝剑"，但允许"酒壶"匹配"酒壶放在"）
    """
    positions: list[int] = []
    start = 0
    while True:
        idx = text.find(prop_name, start)
        if idx < 0:
            break
        if len(prop_name) >= 3:
            positions.append(idx)
        else:
            # 2 字符：要求至少一边是边界
            end = idx + len(prop_name)
            left_boundary = (idx == 0) or _is_separator(text[idx - 1])
            right_boundary = (end >= len(text)) or _is_separator(text[end])
            if left_boundary or right_boundary:
                positions.append(idx)
        start = idx + 1
    return positions


def _is_separator(ch: str) -> bool:
    """ch 是分词边界（标点/空白/换行/特殊字符）—— 不是中文字."""
    if not ch:
        return True
    if ch.isspace():
        return True
    if ch in "，。、；！？\n\r\t":
        return True
    if ch in ".,;:!?\"'()[]{}":
        return True
    return False


def _infer_prop_state(text: str, prop_name: str, pos: int | None = None) -> tuple[str, str | None]:
    """从 prop 周围 8 字符内推断 state（held/dropped/hanging/floating）.

    简单启发式：
      - "持" / "握" / "拿" / "手中" 紧邻 → held
      - "挂" / "吊" / "悬" 紧邻 → hanging
      - "漂" / "斜伸" 紧邻 → floating
      - "斜倚" / "靠在" → leaning
      - 否则 → dropped

    Args:
        pos: 已知 prop 出现位置（避免重复 find）。None = 第一次出现.
    """
    if pos is None:
        pos = text.find(prop_name)
        if pos < 0:
            return PROP_STATE_DROPPED, None
    ctx_before = text[max(0, pos - 4):pos]
    ctx_after = text[pos + len(prop_name):pos + len(prop_name) + 12]
    ctx = ctx_before + ctx_after

    if any(w in ctx for w in ("持", "握", "拿", "手中", "在握")):
        return PROP_STATE_HELD, None
    if any(w in ctx for w in ("挂", "吊", "悬")):
        return PROP_STATE_HANGING, None
    if any(w in ctx for w in ("漂", "斜伸")):
        return PROP_STATE_FLOATING, None
    if any(w in ctx for w in ("斜倚", "靠在", "靠着")):
        return PROP_STATE_LEANING, None
    return PROP_STATE_DROPPED, None


# ============================================================================
# 3. 空间关系抽取
# ============================================================================

# 关系动词正则（不写死具体名，按 prop/character + 动词抽）
_VERB_TO_RELATION = {
    "在": REL_ON,
    "在...上": REL_ON,
    "位于": REL_ON,
    "挂": REL_HANG,
    "挂在": REL_HANG,
    "吊": REL_HANG,
    "斜倚": REL_LEAN,
    "靠着": REL_LEAN,
    "倒在": REL_ON,
    "倾倒在": REL_ON,
    "漂浮": REL_FLOAT,
    "没入": REL_IN_WATER,
    "沉入": REL_IN_WATER,
    "露出": REL_ON,
    "位于...上方": REL_ABOVE,
    "头顶": REL_ABOVE,
    "上方": REL_ABOVE,
}


def _extract_relations(
    text: str, characters: list[Character], props: list[Prop]
) -> list[SpatialRelation]:
    """抽取空间关系.

    简化策略：对每个 entity（character/prop），扫其后 12 字符内的动词 + 承重面词。
    """
    relations: list[SpatialRelation] = []
    for entity in list(characters) + list(props):
        name = entity.name
        idx = 0
        while True:
            pos = text.find(name, idx)
            if pos < 0:
                break
            ctx = text[pos:pos + len(name) + 16]
            for verb, rel_type in _VERB_TO_RELATION.items():
                if verb in ctx:
                    # 抽 object（动词后的承重面词或角色名）
                    obj = _extract_rel_object(ctx, verb, characters)
                    if obj:
                        relations.append(SpatialRelation(
                            subject=name, relation=rel_type, object=obj,
                        ))
                        break
            idx = pos + len(name)
    return relations


def _extract_rel_object(ctx: str, verb: str, characters: list[Character]) -> str:
    """从动词后抽 relation.object（承重面词或角色名）."""
    idx = ctx.find(verb)
    if idx < 0:
        return ""
    after = ctx[idx + len(verb):]
    # 优先级：先看是否是角色名，再看是否是 surface 词
    for c in characters:
        if c.name and c.name in after[:8]:
            return c.name
    # 通用 surface 词
    for s in ("画左甲板", "画右甲板", "画中甲板", "甲板", "桌面", "案上",
              "地面", "地上", "船头", "船中", "船尾", "桥面", "岸边",
              "水面", "水下", "空中"):
        if s in after[:12]:
            return s
    return ""


# ============================================================================
# 4. 主入口：parse(text, ...) → EntityGraph
# ============================================================================

def extract(
    text: str,
    char_layout: list[dict] | None = None,
    scene: str = "",
    characters_bank: dict | None = None,
) -> EntityGraph:
    """从 prompt 文本抽取实体关系图.

    Args:
        text: prompt/ff_desc/lf_desc/motion_desc 文本
        char_layout: shot["char_layout"]（结构化角色表，可选）
        scene: location/scene 名（用于 get_surfaces）
        characters_bank: {ident: {static_features, dynamic_features}}（可选）

    Returns:
        EntityGraph 实例（含 characters, props, relations, raw_text）
    """
    if not text:
        return EntityGraph(raw_text="", scene=scene)

    characters = _extract_characters_from_layout(char_layout or [], characters_bank)
    props = _extract_props_from_text(text)
    relations = _extract_relations(text, characters, props)

    return EntityGraph(
        characters=characters,
        props=props,
        relations=relations,
        raw_text=text,
        scene=scene,
    )


__all__ = ["extract"]
