"""Climax distribution constraint: 检测告警 + 历史注入残留剥离（v6.62 收敛）.

旧版（≤v6.61）在高潮镜不足时把「。冲突，反转，真相，局势骤变」注入
narration_segment/subtitle/prompt/main_subject/core_keywords 五字段：
抽象节拍词对图片/视频模型无视觉意义、会进字幕被观众看到（09-06 chapter_001
实测 narration 开头两遍纯节拍词——空镜 narration_segment 原本为空，注入后
suffix 成整段）、污染 main_subject 肖像匹配。

v6.62 现行为：
  1. 剥离历史注入残留（climax_words.strip_injected_suffixes，幂等，留痕）
  2. 高潮镜不足 → ctx.record_violation 上报（WARN，不改写数据）。
     narrative_arc/punch_density 均在 validation._REGEN_FEEDBACK_EXCLUDE，
     不会引发 regen 空转；是否补爆点交上游/人工决策。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.climax_words import (
    climax_words_for_gap,
    strip_injected_suffixes,
)
from step01_script_gen.gap_analysis import normalize_core_keywords
from .base import Constraint
from .types import RepairRecord, Severity, Violation


class ClimaxDistributionConstraint(Constraint):
    """Ensure climax shots are sufficient and distributed (detect-only)."""

    name = "climax_distribution"
    severity = Severity.WARN
    fields = ("main_subject", "motion_desc", "prompt", "narration_segment", "subtitle")
    skip_hook = True

    @staticmethod
    def _is_climax(shot: dict, words: list[str]) -> bool:
        text = " ".join(
            [
                str(shot.get("narration_segment") or ""),
                str(shot.get("subtitle") or ""),
                *normalize_core_keywords(shot.get("core_keywords")),
            ]
        )
        return any(word in text for word in words)

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = [s for s in ctx.shots if isinstance(s, dict)]

        # 1) 剥离历史注入残留（hook 也扫，幂等——注入方早已停用）
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            for field in ("narration_segment", "subtitle", "prompt", "main_subject"):
                value = shot.get(field)
                if not isinstance(value, str) or not value:
                    continue
                cleaned = strip_injected_suffixes(value)
                if cleaned != value:
                    shot[field] = cleaned
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=shot.get("index", idx),
                            action="rewrite",
                            target=field,
                            before=value[:60],
                            after=cleaned[:60],
                            detail="剥离爆点注入残留（v6.62 收敛，见 climax_words.CLIMAX_INJECTED_*）",
                        )
                    )

        # 2) 高潮镜不足 → 只上报不改写
        if len(shots) < 3:
            return repairs
        words = climax_words_for_gap(ctx.script)
        readable = [
            (i, shot, self._is_climax(shot, words))
            for i, shot in enumerate(shots)
        ]
        missing = 3 - sum(state for _, _, state in readable)
        if missing <= 0:
            return repairs

        half = len(shots) // 2
        candidates = [item for item in readable[1:] if not item[2]]
        candidates.sort(key=lambda item: item[0] >= half, reverse=True)
        for i, shot, _ in candidates[:missing]:
            ctx.record_violation(
                Violation(
                    constraint_name=self.name,
                    severity=self.severity,
                    shot_index=shot.get("index", i),
                    field="narration_segment",
                    message=(
                        f"高潮镜不足（缺 {missing} 个）：建议给该镜写视觉化的冲突/反转"
                        "内容（动作/表情/构图），不要注入抽象节拍词"
                    ),
                    evidence=str(shot.get("prompt") or "")[:60],
                )
            )
        return repairs
