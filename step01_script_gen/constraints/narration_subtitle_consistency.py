"""H. narration/subtitle 拼接一致性约束（v6.42 迁移自 _hard_fix_narration_subtitle_consistency）.

8-22 跑批实测：2 次 regen 都报「narration 与 subtitle 拼接不一致」。
LLM 偶发输出「…」 vs 「...」、全/半角空格差异、标点不一致。

修法（确定性归一化）：
  1. 「...」 → 「…」（全角统一）
  2. 多个连续空格 → 移除
  3. 同步 narration 与 subtitle（以 narration 为准，镜像到 subtitle）

幂等：归一化前后无变化则跳过。
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


def _normalize(text: str) -> str:
    """三连点 → 中文省略号；半角空格 → 移除；多个省略号折叠成 1 个.

    v6.43（08-23 审计）：旧版只处理 ASCII `...`；LLM 偶尔输出 `……`（两个全角
    省略号）会绕过归一化，与 top-level narration 拼接检查长期告警。补
    `…+` → `…` 折叠后两侧长度一致。
    """
    if not text:
        return text
    text = re.sub(r"\.{2,}", "…", text)
    text = re.sub(r"…+", "…", text)
    text = re.sub(r"\s+", "", text)
    return text


class NarrationSubtitleConsistencyConstraint(Constraint):
    """H. narration/subtitle 一致性：省略号归一 + 空格移除 + 双向同步.

    severity=WARN: 字段一致性可由后续 repair round 修.
    """

    name = "narration_subtitle_consistency"
    severity = Severity.WARN
    fields = ("narration_segment", "subtitle")

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            narr = shot.get("narration_segment")
            sub = shot.get("subtitle")
            if not narr and not sub:
                continue
            if not isinstance(narr, str):
                narr = ""
            if not isinstance(sub, str):
                sub = ""

            new_narr = _normalize(narr)
            new_sub = _normalize(sub)
            # 强制以 narration_segment 为准
            if new_narr:
                new_sub = new_narr
            else:
                new_narr = new_sub

            if new_narr != narr or new_sub != sub:
                shot["narration_segment"] = new_narr
                shot["subtitle"] = new_sub
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx,
                        action="rewrite",
                        target="narration_segment+subtitle",
                        before=f"narr={narr!r} sub={sub!r}",
                        after=f"narr={new_narr!r} sub={new_sub!r}",
                    )
                )

        # v6.43（08-23 审计）：top-level script["narration"] 长期与 subtitle
        # 拼接不一致——parse_response 在 line 7306 只在 narration 为空时兜底，
        # 归一化后回填。写入条件：顶层已存在该字段（schema 允许但非必须）。
        script = ctx.script if isinstance(ctx.script, dict) else None
        if script and "narration" in script:
            parts = []
            for s in ctx.shots:
                if not isinstance(s, dict):
                    continue
                seg = s.get("subtitle", "")
                if isinstance(seg, str) and seg:
                    parts.append(seg)
            joined = "".join(parts)
            joined = _normalize(joined) if joined else joined
            if joined != script.get("narration"):
                old_narr = script["narration"]
                script["narration"] = joined
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=0,
                        action="rewrite",
                        target="script.narration",
                        before=old_narr,
                        after=joined,
                    )
                )
        return repairs
