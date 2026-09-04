"""B1. 业务必填字段兜底（2026-09-04 新增，doubao-seed-2-1-turbo 适配）.

4 个业务字段在 doubao-seed-2-1-turbo 输出中频繁缺失（实测 09-03 全章节 12 镜
227 项修复里 60+ 项是这 4 字段缺失）。本约束做确定性兜底，避免在 LLM prompt
里反复强约束（v6.43 教训：长 prompt 后段规则失声）。

兜底策略（按字段语义）：
- core_keywords：缺失 → 从 ff_desc + main_subject 提取 3-5 个关键词
- fx_effect：缺失 → "无"（业务约定占位）
- clip_transition：缺失 → "硬切"（默认转场，最保守）
- context_bridge：缺失 → 从前镜 lf_desc 截取尾句，承接到本镜开头

幂等：每个字段填入后即视为已修（修复值含确定性锚词），避免反复跑反复填。

severity=WARN：业务字段缺失不构成 hard fail，由 LLM 修轮 + 后续 hard_fix 兜底。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


# 4 字段名
_MANDATORY_FIELDS = (
    "core_keywords",
    "fx_effect",
    "clip_transition",
    "context_bridge",
)

# 默认兜底值（确定性锚词，repair_marker 命中即视为已修）
_DEFAULTS = {
    "core_keywords": "古风,短剧,镜头叙事",  # repair_marker = "古风"
    "fx_effect": "无",                        # repair_marker = "无"
    "clip_transition": "硬切",                  # repair_marker = "硬切"
    "context_bridge": "承接上一镜头剧情自然过渡",  # repair_marker = "承接"
}

# 转场词白名单（v6.51 既有 16 种转场词库；用于校验兜底值是否合法）
_VALID_TRANSITIONS = (
    "硬切", "叠化", "淡入淡出", "推镜", "拉镜", "闪白", "闪黑", "形状匹配",
    "视线引导", "遮挡", "匹配剪辑", "动作衔接",
)


def _extract_keywords(shot: dict) -> str:
    """从 ff_desc + main_subject 提取 3-5 个关键词（粗提取，不依赖 LLM）.

    策略：取 main_subject 整词 + ff_desc 中前 5 个 2-4 字名词短语。
    """
    parts: list[str] = []
    main_subject = (shot.get("main_subject") or "").strip()
    if main_subject and main_subject not in ("空镜", "场景", "环境"):
        parts.append(main_subject)
    ff = (shot.get("ff_desc") or "").strip()
    if ff:
        # 提取 2-4 字连续汉字词
        for m in re.finditer(r"[一-龥]{2,4}", ff):
            w = m.group(0)
            if w not in parts and len(parts) < 5:
                parts.append(w)
            if len(parts) >= 5:
                break
    if not parts:
        parts = ["古风", "短剧", "镜头叙事"]
    return "，".join(parts[:5])


def _bridge_from_prev(prev_shot: dict | None) -> str:
    """从前镜 lf_desc 截取尾句作为本镜 context_bridge."""
    if not prev_shot:
        return "承接上一镜头剧情自然过渡"
    lf = (prev_shot.get("lf_desc") or "").strip()
    if not lf:
        return "承接上一镜头剧情自然过渡"
    # 取最后 1 句（按 。！？ 切）
    for end in ("。", "！", "？"):
        idx = lf.rfind(end)
        if idx > 0:
            tail = lf[:idx + 1].strip()
            if len(tail) > 60:
                tail = tail[-60:]
            return f"承接上一镜头：{tail}"
    # 无句号兜底
    if len(lf) > 60:
        lf = lf[-60:]
    return f"承接上一镜头：{lf}"


class MandatoryBizFieldsConstraint(Constraint):
    """B1. 4 字段兜底：core_keywords / fx_effect / clip_transition / context_bridge.

    severity=WARN：业务字段缺失走 hard_fix 兜底，不触发 regen。
    repair_marker 命中视为已修，避免幂等失败。
    """

    name = "mandatory_biz_fields"
    severity = Severity.WARN
    fields = ("core_keywords", "fx_effect", "clip_transition", "context_bridge")

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = ctx.shots
        if not shots or not isinstance(shots, list):
            return repairs

        for idx, shot in enumerate(shots):
            if not isinstance(shot, dict):
                continue
            if self._already_repaired(shot):
                continue
            prev = shots[idx - 1] if idx > 0 else None
            for field in _MANDATORY_FIELDS:
                if field not in shot or shot.get(field) in (None, "", "null", "None"):
                    before = shot.get(field, "")
                    if field == "core_keywords":
                        shot[field] = _extract_keywords(shot)
                    elif field == "context_bridge":
                        shot[field] = _bridge_from_prev(prev)
                    else:
                        shot[field] = _DEFAULTS[field]
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=shot.get("index", idx),
                            action="set_field",
                            target=field,
                            before=str(before) if before else "",
                            after=shot[field],
                            detail=f"业务必填字段 {field} 缺失，兜底填入（doubao-seed-2-1-turbo 高频漏字段）",
                        )
                    )
        return repairs
