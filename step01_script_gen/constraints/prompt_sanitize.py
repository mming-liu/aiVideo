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
            # v6.62（09-06）：keyframes[].desc 同样是模型可见文本（step02a 生图
            # prompt 的直接输入），一并消毒——hook kf 实测残留「可容可容2-3人
            # （杨旭、听香、张十三）+ 炭炉…」旧损坏文本。
            for _i, _kf in enumerate(shot.get("keyframes") or []):
                if not isinstance(_kf, dict):
                    continue
                _text = _kf.get("desc")
                if not isinstance(_text, str) or not _text.strip():
                    continue
                _before = _text
                _cleaned = strip_spatial_anchors(_before)
                _cleaned = strip_debug_markers(_cleaned)
                if _cleaned == _before:
                    continue
                _kf["desc"] = _cleaned
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx if not is_hook else None,
                        action="sanitize",
                        target=f"keyframes[{_i}].desc",
                        before=_before[:60] + ("..." if len(_before) > 60 else ""),
                        after=_cleaned[:60] + ("..." if len(_cleaned) > 60 else ""),
                    )
                )
        return repairs
