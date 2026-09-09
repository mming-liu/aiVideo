"""H. narration/subtitle 拼接一致性约束（v6.42 迁移自 _hard_fix_narration_subtitle_consistency）.

8-22 跑批实测：2 次 regen 都报「narration 与 subtitle 拼接不一致」。
LLM 偶发输出「…」 vs 「...」、全/半角空格差异、标点不一致。

修法（确定性归一化）：
  1. 「...」 → 「…」（全角统一）
  2. 多个连续空格 → 移除
  3. 同步 narration 与 subtitle（以 narration 为准，镜像到 subtitle）

幂等：归一化前后无变化则跳过。

v6.62.7（09-09）：删掉「对白镜双向空 → 用 ff_desc 后 20 字伪造台词」的兜底，
改为纯告警。理由见 apply() 内注释——伪造值会一路烧进 SRT 与成片。
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


_DIALOGUE_HINTS = (
    "质问", "怒喝", "叫道", "怒道", "厉声", "低语", "轻声道", "冷声",
    "低声", "说道", "答道", "问道", "喊道", "吼道", "对话", "对白",
)
_EMPTY_SUBJECTS = ("空镜", "场景", "环境", "远景")


def _is_dialogue_shot(shot: dict) -> bool:
    subject = str(shot.get("main_subject") or "")
    if any(hint in subject for hint in _DIALOGUE_HINTS):
        return True
    return bool(str(shot.get("subtitle") or "").strip())


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
            if not narr and not sub and _is_dialogue_shot(shot):
                subject = str(shot.get("main_subject") or "")
                if any(token in subject for token in _EMPTY_SUBJECTS):
                    continue
                # v6.62.7（09-09）**不再伪造台词**，只告警。
                # 旧实现兜底成 f"{ff_desc[-20:]}，某：……"——拿**画面描述的后 20 字**
                # 当旁白。这个值不是中间态：它同时写进 narration_segment 和
                # subtitle，再被下面的 top-level 拼接收进 script["narration"]，
                # 一路烧进 SRT 和视频模型的对白 prompt。09-08 实测 chapter_001
                # 镜头8 落盘成「香站在中舱，手持油纸伞，嘴唇微闭，某」——从
                # ff_desc 中间切开导致「听香」少了「听」，尾巴挂着半个占位符，
                # 而全片 script["narration"] 一共只有 38 字，其中 17 字是这句。
                # 空旁白只是「这镜没台词」（管线本就容忍，实测 8/10 镜为空照跑），
                # 伪造的旁白是**错内容进成片**——后者严格更坏，故降级为告警。
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx,
                        action="warn",
                        target="narration_segment+subtitle",
                        detail=(
                            f"shot {idx} 疑似对白镜（main_subject={subject[:20]!r}）"
                            f"但 narration_segment/subtitle 双向为空——"
                            f"该镜将无字幕无旁白，需 LLM 补台词。"
                        ),
                    )
                )
                continue
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
