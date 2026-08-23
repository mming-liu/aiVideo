"""ElementMarkers: 跨约束共享的派生标记 sidecar.

设计：不要扩 shared/character_bank.py（它跨 step01-04 用）。injury 是 per-chapter
事件，不是 per-character 静态属性，应该在约束层维护。

数据结构：
  - InjuryMarker: 致命伤的"视觉标记"（anchor 词 + 持续可见短语）
  - PropMarker: 道具的"规范位置"

约束在首次 apply 时填充 markers（如 A 找到 build_idx → 注册 InjuryMarker），
后续约束（+ re-apply）查表，**实现数据驱动修复**。

持久化：可选，写到 {OUTPUT_BASE}/<novel>/global/element_markers.json，
跨章复用（Phase 2 实施完整 sidecar IO；Phase 1 只做内存版）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class InjuryMarker:
    """致命伤的视觉标记.

    Attributes:
        victim: 受害者角色名（如 '杨旭'）
        anchor_phrases: 锚词列表（任一在场 = 该 fatal wound 已建立）
        continuity_phrase: 后续镜头追加的持续可见描述
        location: 限制该 fatal wound 有效的场景（None = 任意）
    """

    victim: str
    anchor_phrases: Tuple[str, ...]
    continuity_phrase: str
    location: Optional[str] = None


@dataclass(frozen=True)
class PropMarker:
    """道具的规范空间位置.

    Attributes:
        prop: 道具名（如 '炭炉'）
        canonical_position: 规范位置（'画中甲板'）
        scene_hints: 场景提示词（出现时 override canonical_position）
    """

    prop: str
    canonical_position: str
    scene_hints: Tuple[str, ...] = ()


@dataclass
class ElementMarkers:
    """跨约束共享的派生标记.

    在约束执行过程中由各 Constraint 增量填充；后续 Constraint 查表。
    空 registries 视为"未发现"，不应被查询。
    """

    injuries: Dict[str, InjuryMarker] = field(default_factory=dict)  # victim → marker
    props: Dict[str, PropMarker] = field(default_factory=dict)        # prop → marker

    def register_injury(self, marker: InjuryMarker) -> None:
        """注册一个致命伤标记（首次发现时调用）.

        已有同名 victim 不覆盖（保留首次注册的内容，避免后续误判冲掉正确值）。
        """
        self.injuries.setdefault(marker.victim, marker)

    def get_injury(self, victim: str) -> Optional[InjuryMarker]:
        """查询 victim 的致命伤标记."""
        return self.injuries.get(victim)

    def register_prop(self, marker: PropMarker) -> None:
        """注册一个道具位置标记."""
        self.props.setdefault(marker.prop, marker)

    def get_prop(self, prop: str) -> Optional[PropMarker]:
        """查询 prop 的规范位置."""
        return self.props.get(prop)

    @classmethod
    def from_dict(cls, data: dict) -> "ElementMarkers":
        """从 dict 构造（sidecar JSON 读取）.

        Phase 2 实施完整 sidecar IO; Phase 1 暂返回空.
        """
        if not isinstance(data, dict):
            return cls()
        em = cls()
        injuries = data.get("injuries") or {}
        for victim, m in injuries.items():
            if not isinstance(m, dict):
                continue
            em.injuries[victim] = InjuryMarker(
                victim=victim,
                anchor_phrases=tuple(m.get("anchor_phrases") or ()),
                continuity_phrase=m.get("continuity_phrase") or "",
                location=m.get("location"),
            )
        props = data.get("props") or {}
        for prop, m in props.items():
            if not isinstance(m, dict):
                continue
            em.props[prop] = PropMarker(
                prop=prop,
                canonical_position=m.get("canonical_position") or "画中",
                scene_hints=tuple(m.get("scene_hints") or ()),
            )
        return em

    def to_dict(self) -> dict:
        """转 dict（sidecar JSON 写入）."""
        return {
            "injuries": {
                v: {
                    "anchor_phrases": list(m.anchor_phrases),
                    "continuity_phrase": m.continuity_phrase,
                    "location": m.location,
                }
                for v, m in self.injuries.items()
            },
            "props": {
                p: {
                    "canonical_position": m.canonical_position,
                    "scene_hints": list(m.scene_hints),
                }
                for p, m in self.props.items()
            },
        }
