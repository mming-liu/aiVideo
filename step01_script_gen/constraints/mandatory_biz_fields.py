"""业务必填字段兜底.

LLM 在长 prompt 下偶发漏填业务字段。这里只做确定性兜底，字段级幂等：
已有非空值保持不变，缺失才填。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.gap_analysis import normalize_core_keywords
from .base import Constraint
from .types import RepairRecord, Severity


_SHOT_FIELDS = (
    "core_keywords",
    "fx_effect",
    "clip_transition",
    "context_bridge",
)
_HOOK_FIELDS = (
    "core_keywords",
    "fx_effect",
    "clip_transition",
    "context_bridge",
)
_DEFAULTS = {
    "fx_effect": "无",
    "clip_transition": "硬切",
    "context_bridge": "承接上一镜头剧情自然过渡",
}
_MISSING_STRINGS = {None, "", "null", "None"}


def _is_missing(value) -> bool:
    if isinstance(value, list):
        return not normalize_core_keywords(value)
    return value in _MISSING_STRINGS


def _extract_keywords(shot: dict) -> list[str]:
    parts: list[str] = []
    main_subject = str(shot.get("main_subject") or "").strip()
    if main_subject and main_subject not in ("空镜", "场景", "环境"):
        parts.append(main_subject)
    ff_desc = str(shot.get("ff_desc") or "").strip()
    for match in re.finditer(r"[一-龥]{2,4}", ff_desc):
        keyword = match.group(0)
        if keyword not in parts:
            parts.append(keyword)
        if len(parts) >= 5:
            break
    return parts or ["古风", "短剧", "镜头叙事"]


def _bridge_from_prev(prev_shot: dict | None) -> str:
    lf_desc = str((prev_shot or {}).get("lf_desc") or "").strip()
    if not lf_desc:
        return _DEFAULTS["context_bridge"]
    for ending in ("。", "！", "？"):
        pos = lf_desc.rfind(ending)
        if pos > 0:
            tail = lf_desc[:pos + 1].strip()[-60:]
            return f"承接上一镜头：{tail}"
    return f"承接上一镜头：{lf_desc[-60:]}"


class MandatoryBizFieldsConstraint(Constraint):
    """业务字段缺失兜底，不改变已有非空值。"""

    name = "mandatory_biz_fields"
    severity = Severity.WARN
    fields = _SHOT_FIELDS

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        if not isinstance(ctx.shots, list):
            return repairs

        prev_shot = None
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            required_fields = _HOOK_FIELDS if is_hook else _SHOT_FIELDS
            for field_name in required_fields:
                if field_name not in shot or _is_missing(shot.get(field_name)):
                    before = shot.get(field_name, "")
                    if field_name == "core_keywords":
                        after: list[str] | str = _extract_keywords(shot)
                    elif field_name == "context_bridge":
                        after = _bridge_from_prev(prev_shot)
                    else:
                        after = _DEFAULTS[field_name]
                    shot[field_name] = after
                    repairs.append(RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx,
                        action="set_field",
                        target=field_name,
                        before=str(before) if before else "",
                        after=after,
                    ))
            if not is_hook:
                prev_shot = shot
        return repairs
