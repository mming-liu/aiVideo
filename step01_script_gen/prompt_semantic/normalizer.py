"""prompt_semantic.normalizer — 公共入口.

PromptSemanticNormalizer 是上层（v6_43_prompt_enforcement / vimax_bridge）
调用语义层的统一 API。

用法：
  from step01_script_gen.prompt_semantic import PromptSemanticNormalizer
  n = PromptSemanticNormalizer(scene="舴艋舟", char_layout=shot.get("char_layout", []))
  result = n.normalize(shot["ff_desc"], is_hook=True)
  # result.text = 修复后文本
  # result.violations = [Violation, ...]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .entities import EntityGraph
from .extractor import extract as extract_graph
from .fixer import apply_fixes
from .physics import PhysicsContext, run_all, Violation
from .registry import get_surfaces


@dataclass
class NormalizedResult:
    """规范化结果."""
    text: str                              # 修复后文本
    violations: list[Violation] = field(default_factory=list)
    graph: Optional[EntityGraph] = None

    @property
    def changed(self) -> bool:
        return self.graph is not None and self.text != self.graph.raw_text


class PromptSemanticNormalizer:
    """Prompt 语义层规范化器.

    构造时绑定 scene + char_layout（结构化数据），调用 normalize() 时跑：
      extract → run_all physics rules → apply_fixes

    幂等：二次调用应无新 violation.
    """

    def __init__(self, scene: str = "", char_layout: list | None = None):
        self.scene = scene or ""
        self.char_layout = char_layout or []
        self.surfaces = get_surfaces(self.scene)

    def normalize(
        self,
        text: str,
        is_hook: bool = False,
        characters_bank: dict | None = None,
    ) -> NormalizedResult:
        """规范化单段 prompt 文本."""
        if not text:
            return NormalizedResult(text="")

        graph = extract_graph(
            text=text,
            char_layout=self.char_layout,
            scene=self.scene,
            characters_bank=characters_bank,
        )
        ctx = PhysicsContext(
            scene=self.scene,
            surfaces=self.surfaces,
            is_hook=is_hook,
        )
        violations = run_all(graph, ctx)
        fixed_text = apply_fixes(graph, violations) if violations else text
        return NormalizedResult(text=fixed_text, violations=violations, graph=graph)


__all__ = ["PromptSemanticNormalizer", "NormalizedResult"]
