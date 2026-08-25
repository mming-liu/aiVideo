"""prompt_semantic.entities — 实体数据结构（角色/道具/空间关系）.

设计目标：
  - frozen dataclass：可哈希、可放入 set/dict 作 dedup
  - 与 scene_anchors.py / character_bank.py 的字段直接对应
  - Character.canonical_anchor() 内置 dedup + 模板拼接（取代
    vimax_bridge._inject_character_costume 里的硬编码 "此刻X/身着X" 逻辑）

数据流：
  1. extractor.parse(text) → EntityGraph(characters, props, relations)
  2. physics.detect(graph) → list[Violation]（含 FixAction）
  3. fixer.apply(graph, violations) → 修复后文本
  4. Character.canonical_anchor() 单独被 vimax_bridge 复用
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Optional, Tuple


# ============================================================================
# 1. 角色（Character）
# ============================================================================

@dataclass(frozen=True)
class Character:
    """角色实体.

    来源：
      - character_bank[ident]["static_features"] / ["dynamic_features"]
      - shot["char_layout"][i]（position/facing/role/holding）

    与 character_bank 的字段对应：
      - static_features: 脸型/五官/身段（不随镜次变）
      - dynamic_features: 服装/发型/当前状态（随镜次变）
    """
    name: str
    static_features: str = ""           # 脸型/五官
    dynamic_features: str = ""          # 服装/发型
    pose: str = ""                       # 坐/站/躺/卧/跪/趴/跃
    position: str = ""                   # 船头/画左甲板/...
    facing: str = ""                     # 面向/背对/侧身
    role: str = ""                       # 主/次/背景/威胁
    holding: Tuple[str, ...] = ()        # 持有的 prop 名
    hair_state: str = ""                 # 当前发型状态词（披头散发/散发...）

    # 角色类型分类（用于物理规则路由）
    is_threat: bool = False
    is_victim: bool = False

    def canonical_anchor(self) -> str:
        """生成去重的角色 anchor 文本（取代 vimax_bridge 硬编码 dedup 逻辑）.

        模板："{name}：{static}，{connector}{dynamic}"

        关键 dedup：
          - 若 dynamic 已含 hair_state（披头散发等），不再前置 "此刻X" 避免重复
          - 若 dynamic 不含 hair_state，但有 hair_state 字段，则前置 "此刻[hair_state]"
          - connector 取决于 dynamic 是否含服装词（袍/衫/衣/裳/...）
            → 含服装词用 "，身着"；否则用 "，"

        与 vimax_bridge 旧 _inject_character_costume 行为完全等价。
        """
        static = self.static_features
        dynamic = self.dynamic_features
        hair_state = self.hair_state

        # 服装词判定（袍/衫/衣/裳/裙/袄/裰/服/袈/氅）
        _COSTUME = ("袍", "衫", "衣", "裳", "裙", "袄", "裰", "服", "袈", "氅")
        if any(w in dynamic for w in _COSTUME):
            connector = "，身着"
        else:
            connector = "，"

        # 静态发型短语（与动态发型冲突时剔除）
        _HAIR_STATIC = (
            "青丝挽美人髻", "黑发挽美人髻", "青丝盘起", "秀发高挽",
            "发髻高耸", "云鬓高挽", "挽发", "束发", "盘发",
            "美人髻", "发髻", "云鬓", "发髻高",
        )
        for phrase in _HAIR_STATIC:
            static = static.replace(phrase, "")

        # 清理空段（"如花似玉，青丝挽美人髻，秀颈" → "如花似玉，秀颈"）
        parts = [p for p in static.split("，") if p.strip()]
        static = "，".join(parts)

        # 关键 dedup：dynamic 已含 hair_state 时不再前置 "此刻X"
        if hair_state:
            if hair_state in dynamic:
                # 已含 → 不前置，保持简洁
                prefix = f"{static}{connector}" if static else connector.lstrip("，")
            else:
                # 未含 → 前置 "此刻X"
                prefix = f"{static}，此刻{hair_state}{connector}" if static else f"此刻{hair_state}{connector}"
        else:
            prefix = f"{static}{connector}" if static else connector.lstrip("，")

        return f"{self.name}：{prefix}{dynamic}"


# ============================================================================
# 2. 道具（Prop）
# ============================================================================

# Prop 状态（有限枚举）
PROP_STATE_HELD = "held"             # 角色持有
PROP_STATE_DROPPED = "dropped"       # 脱手
PROP_STATE_LEANING = "leaning"       # 斜靠
PROP_STATE_HANGING = "hanging"       # 悬挂
PROP_STATE_FLOATING = "floating"     # 漂浮水面
PROP_STATE_UNDERWATER = "underwater" # 水下

PROP_STATES = frozenset({
    PROP_STATE_HELD, PROP_STATE_DROPPED, PROP_STATE_LEANING,
    PROP_STATE_HANGING, PROP_STATE_FLOATING, PROP_STATE_UNDERWATER,
})


@dataclass(frozen=True)
class Prop:
    """道具实体.

    来源：
      - props_bank[ident]["identifier"] / ["consistent_terms"]
      - shot["prompt/ff_desc/lf_desc/motion_desc"] 里出现 prop 名 + 位置
    """
    name: str                            # 道具名（中文，如"酒壶"）
    position: str = ""                   # 当前位置词（画左甲板/水面下/...）
    state: str = PROP_STATE_DROPPED      # 见 PROP_STATES
    attached_to: Optional[str] = None    # 角色名 或 表面名（甲板/桌面）

    def is_in_air(self) -> bool:
        """是否处于"无支撑"状态（违反物理）."""
        return self.state in (PROP_STATE_HANGING, PROP_STATE_FLOATING) and not self.attached_to

    def has_support(self, surfaces: Tuple[str, ...]) -> bool:
        """是否落在承重面上（surface 来自 registry.get_surfaces）."""
        if self.state == PROP_STATE_HELD:
            return True
        if self.attached_to is None:
            return False
        # 角色持有或承重面
        return any(s in (self.attached_to or "") for s in surfaces) or self.attached_to


# ============================================================================
# 3. 空间关系（SpatialRelation）
# ============================================================================

# 关系动词分类
REL_ON = "on"               # 在...上
REL_INSIDE = "inside"       # 在...内
REL_HANG = "hang"           # 挂在/吊在
REL_LEAN = "lean"           # 斜倚/靠着
REL_FLOAT = "float"         # 漂浮
REL_IN_WATER = "in_water"   # 没入/沉入/潜入
REL_ABOVE = "above"         # 位于...上方/头顶
REL_BELOW = "below"         # 位于...下方
REL_BESIDE = "beside"       # 在...旁/旁边/侧

RELATION_TYPES = frozenset({
    REL_ON, REL_INSIDE, REL_HANG, REL_LEAN, REL_FLOAT,
    REL_IN_WATER, REL_ABOVE, REL_BELOW, REL_BESIDE,
})


@dataclass(frozen=True)
class SpatialRelation:
    """空间关系三元组（subject, relation, object）.

    例：
      ("刺客", REL_IN_WATER, "水面")
      ("酒壶", REL_LEAN, "画左甲板")
      ("刀尖", REL_ABOVE, "刺客头顶")
    """
    subject: str
    relation: str                          # 见 RELATION_TYPES
    object: str

    def is_contradiction_with(
        self, other: "SpatialRelation", graph_characters: dict[str, Character]
    ) -> bool:
        """判断两条关系是否空间矛盾（如"在水下"+"在头顶"）.

        矛盾集合：
          - subject 同时 REL_IN_WATER + REL_ABOVE
          - subject 同时 REL_FLOAT + REL_ON
        """
        if self.subject != other.subject:
            return False
        if self.relation == other.relation:
            return False
        if self.relation == REL_IN_WATER and other.relation == REL_ABOVE:
            return True
        if self.relation == REL_FLOAT and other.relation == REL_ON:
            return True
        return False


# ============================================================================
# 4. 实体图（EntityGraph）
# ============================================================================

@dataclass
class EntityGraph:
    """从 prompt 文本抽取的实体关系图.

    physics.detect(graph) 在此图上跑规则；
    fixer.apply(graph, violations) 用此图生成修复文本。
    """
    characters: list[Character] = field(default_factory=list)
    props: list[Prop] = field(default_factory=list)
    relations: list[SpatialRelation] = field(default_factory=list)
    raw_text: str = ""
    scene: str = ""                        # location/scene 名

    def character_by_name(self, name: str) -> Optional[Character]:
        for c in self.characters:
            if c.name == name:
                return c
        return None

    def prop_by_name(self, name: str) -> Optional[Prop]:
        for p in self.props:
            if p.name == name:
                return p
        return None

    def relations_for(self, subject: str) -> list[SpatialRelation]:
        return [r for r in self.relations if r.subject == subject]


__all__ = [
    "Character",
    "Prop",
    "SpatialRelation",
    "EntityGraph",
    "PROP_STATES",
    "PROP_STATE_HELD", "PROP_STATE_DROPPED", "PROP_STATE_LEANING",
    "PROP_STATE_HANGING", "PROP_STATE_FLOATING", "PROP_STATE_UNDERWATER",
    "RELATION_TYPES",
    "REL_ON", "REL_INSIDE", "REL_HANG", "REL_LEAN", "REL_FLOAT",
    "REL_IN_WATER", "REL_ABOVE", "REL_BELOW", "REL_BESIDE",
]
