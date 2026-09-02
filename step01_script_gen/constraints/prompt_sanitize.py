"""J4. prompt 消毒约束（v6.51 新增）.

清理 prompt/ff_desc/lf_desc/motion_desc 中的噪声标记：
  - 空间锚点：「（画中船中）」「（画中）」「（画中水面）」等
  - 调试标记：「（AI生成）」「（测试）」等

这些标记是 LLM 写给代码/人类阅读的，对图片/视频生成模型无意义且污染 prompt。

severity=WARN：确定性清理，无需 regen。
幂等：清理后文本不再含任何匹配模式。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.utils.text import strip_spatial_anchors, strip_debug_markers
from .base import Constraint
from .types import RepairRecord, Severity


# 要清理的字段（所有模型可见的文本字段）
_SANITIZE_FIELDS = (
    "prompt",
    "ff_desc",
    "lf_desc",
    "motion_desc",
    "narration_segment",
    "subtitle",
    "audio_cue",
)


class PromptSanitizeConstraint(Constraint):
    """J4. prompt 消毒：清理空间锚点、调试标记等噪声."""

    name = "prompt_sanitize"
    severity = Severity.WARN
    fields = _SANITIZE_FIELDS

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            for field in _SANITIZE_FIELDS:
                text = shot.get(field)
                if not isinstance(text, str) or not text.strip():
                    continue
                before = text
                cleaned = strip_spatial_anchors(before)
                cleaned = strip_debug_markers(cleaned)
                if cleaned == before:
                    continue
                shot[field] = cleaned
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx if not is_hook else None,
                        action="sanitize",
                        target=field,
                        before=before[:60] + ("..." if len(before) > 60 else ""),
                        after=cleaned[:60] + ("..." if len(cleaned) > 60 else ""),
                    )
                )
        return repairs
