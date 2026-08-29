"""prompt_semantic — 实体-空间语义层.

公共 API：
  - PromptSemanticNormalizer: 入口类
  - Character.canonical_anchor(): 单角色 anchor 文本（含 dedup）
  - PROP_REGISTRY / POSE_REGISTRY / get_surfaces(): 数据注册表
  - Character / Prop / SpatialRelation / EntityGraph: 实体数据类
"""
from __future__ import annotations

from .entities import (
    Character,
    Prop,
    SpatialRelation,
    EntityGraph,
    PROP_STATES,
    PROP_STATE_HELD, PROP_STATE_DROPPED, PROP_STATE_LEANING,
    PROP_STATE_HANGING, PROP_STATE_FLOATING, PROP_STATE_UNDERWATER,
    RELATION_TYPES,
    REL_ON, REL_INSIDE, REL_HANG, REL_LEAN, REL_FLOAT,
    REL_IN_WATER, REL_ABOVE, REL_BELOW, REL_BESIDE,
)
from .registry import (
    PROP_REGISTRY, PropSpec, get_prop_spec, is_known_prop,
    POSE_REGISTRY, classify_pose,
    get_surfaces,
    INVALID_PROP_POSITIONS, get_invalid_prop_patterns,
    ANCHOR_DEDUP_PHRASES, get_anchor_dedup_phrases,
    THREAT_KEYWORDS, WEAPON_KEYWORDS, ABOVE_WATER_PAT,
    ATTACK_KEYWORDS, PRE_STATE_KEYWORDS, POST_STATE_KEYWORDS,
    HANDLE_PROPS, VAGUE_DIRECTION_KW, ANCHOR_VAGUE_KW_PATTERN,
    ABSTRACT_POSITION_WORDS, LYING_NO_HEAD_FIX, BACK_PATTERN,
    COMMON_PERSON_NAMES,
    WARDROBE_WORDS, HAIR_STATE_WORDS, HAIR_STATIC_PHRASES,
    ANIMAL_FACE_KEYWORDS, NO_ANIMAL_FEATURES_GUARD,
    ANIMAL_METAPHOR_FACE_REPLACEMENTS,
    INJURY_ANCHOR_WORDS, INJURY_ANCHOR_TAIL, WOUND_ANCHOR_WORDS,
    THREAT_WORDS_ENLARGED, THREAT_FACING_WORDS, THREAT_GONE_WORDS,
    THREAT_WATER_WORDS, THREAT_STAB_VERBS, ACTION_STRONG_VERBS,
    THREAT_VISIBLE_HINTS,
    is_threat_name, is_weapon_name,
)
from .physics import (
    Violation,
    PhysicsRule, PhysicsContext,
    RULES, run_all,
    detect_dropped_prop_no_support,
    detect_anchor_duplicate_phrase,
    detect_underwater_contradiction,
)
from .extractor import extract
from .fixer import apply_fixes
from .normalizer import PromptSemanticNormalizer, NormalizedResult


__all__ = [
    # entities
    "Character", "Prop", "SpatialRelation", "EntityGraph",
    "PROP_STATES",
    "PROP_STATE_HELD", "PROP_STATE_DROPPED", "PROP_STATE_LEANING",
    "PROP_STATE_HANGING", "PROP_STATE_FLOATING", "PROP_STATE_UNDERWATER",
    "RELATION_TYPES",
    "REL_ON", "REL_INSIDE", "REL_HANG", "REL_LEAN", "REL_FLOAT",
    "REL_IN_WATER", "REL_ABOVE", "REL_BELOW", "REL_BESIDE",
    # registry
    "PROP_REGISTRY", "PropSpec", "get_prop_spec", "is_known_prop",
    "POSE_REGISTRY", "classify_pose",
    "get_surfaces",
    "INVALID_PROP_POSITIONS", "get_invalid_prop_patterns",
    "ANCHOR_DEDUP_PHRASES", "get_anchor_dedup_phrases",
    "THREAT_KEYWORDS", "WEAPON_KEYWORDS", "ABOVE_WATER_PAT",
    "ATTACK_KEYWORDS", "PRE_STATE_KEYWORDS", "POST_STATE_KEYWORDS",
    "HANDLE_PROPS", "VAGUE_DIRECTION_KW", "ANCHOR_VAGUE_KW_PATTERN",
    "ABSTRACT_POSITION_WORDS", "LYING_NO_HEAD_FIX", "BACK_PATTERN",
    "COMMON_PERSON_NAMES",
    "WARDROBE_WORDS", "HAIR_STATE_WORDS", "HAIR_STATIC_PHRASES",
    "ANIMAL_FACE_KEYWORDS", "NO_ANIMAL_FEATURES_GUARD",
    "ANIMAL_METAPHOR_FACE_REPLACEMENTS",
    "INJURY_ANCHOR_WORDS", "INJURY_ANCHOR_TAIL", "WOUND_ANCHOR_WORDS",
    "THREAT_WORDS_ENLARGED", "THREAT_FACING_WORDS", "THREAT_GONE_WORDS",
    "THREAT_WATER_WORDS", "THREAT_STAB_VERBS", "ACTION_STRONG_VERBS",
    "THREAT_VISIBLE_HINTS",
    "is_threat_name", "is_weapon_name",
    # physics
    "Violation", "PhysicsRule", "PhysicsContext", "RULES", "run_all",
    "detect_dropped_prop_no_support",
    "detect_anchor_duplicate_phrase",
    "detect_underwater_contradiction",
    # pipeline
    "extract", "apply_fixes", "PromptSemanticNormalizer", "NormalizedResult",
]
