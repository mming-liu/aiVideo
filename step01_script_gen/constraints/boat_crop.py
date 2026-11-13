"""V. 船体裁切短语剥离约束（v6.42 迁移自 _hard_fix_boat_crop）.

08-23 用户反馈 shot0：船尾没画。根因：LLM 写「船身向画面右方延伸出画外
（只露出船身一部分）」，图片模型照做把船尾裁掉，与 bridge 注入的「完整小舟
在画中、船头船尾齐全不缺失」锚冲突。

修法：ff_desc/lf_desc/prompt 里命中船体裁切短语（延伸出画外/只露出一部分/
船X在画外）→ 剥离该短语（连同其括号注释）。

幂等：短语剥离后不再命中。改写后字段不含任何 _BOAT_CROP_PATTERNS 命中片段。
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


# 船体裁切短语（命中即整段剥离，连同括号注释）
_BOAT_CROP_PATTERNS = (
    # 「船身向画面右方延伸出画外（…）」 / 「船身向右延伸出画外」
    re.compile(r"[，,。]?\s*船身(?:向画)?[^。，,]{0,3}[左右]方?延伸出画外(?:（[^（）]{0,30}）)?"),
    # 「只露出船身一部分（…）」
    re.compile(r"[，,。]?\s*只露出船身(?:的)?一部分(?:（[^（）]{0,30}）)?"),
    # 「船头/船尾在画外/出画外/被截断/被切掉（…）」
    re.compile(r"[，,。]?\s*船(?:头|尾)[^。，,]{0,6}(?:在画外|出画外|被截断|被切掉)(?:（[^（）]{0,30}）)?"),
    # 「船身向右延伸/探出…出画（…）」（变体）
    re.compile(r"[，,。]?\s*船身(?:向画)?[^。，,]{0,3}[左右]方?(?:延伸|探出)(?:[^。，,（）]{0,12})?出画(?:（[^（）]{0,30}）)?"),
)


class BoatCropConstraint(Constraint):
    """V. 船体裁切短语**只报不改**：命中「延伸出画外/只露出一部分/船X在画外」.

    severity=WARN.

    2026-11-13（S02）：此前直接删除这类短语。裁切是**输入里的合法构图**（例如「船尾
    在画外」正是为了让船体不压人物比例），后处理无权判定它「不该存在」；真正需要
    全船入画时，那属于构图要求，应由计划/修订表达并走既有反馈，而不是在确定性层
    静默删字。现在只记录冲突，交 audit / 修订轮处理。
    """

    name = "boat_crop_strip"
    severity = Severity.WARN
    fields = ("ff_desc", "lf_desc", "prompt")

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            for fld in self.fields:
                v = shot.get(fld)
                if not isinstance(v, str) or not v:
                    continue
                hits = [m.group(0) for pat in _BOAT_CROP_PATTERNS
                        for m in pat.finditer(v)]
                if not hits:
                    continue
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx,
                        action="warn",
                        target=fld,
                        before=v[:120],
                        after=v[:120],
                        detail=("检测到船体裁切描述 %s：裁切可能是有意的构图，"
                                "保留原文交 audit/修订轮判断，不自动删除。"
                                % "、".join(h.strip() for h in hits[:3])),
                    )
                )
        return repairs
