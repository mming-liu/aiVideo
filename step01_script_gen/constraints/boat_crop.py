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
    """V. 船体裁切短语剥离：命中「延伸出画外/只露出一部分/船X在画外」→ 剥离.

    severity=WARN: 短语剥离是确定性改写，无副作用。
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
                new_v = v
                for pat in _BOAT_CROP_PATTERNS:
                    new_v = pat.sub("", new_v)
                # 仅当确实剥离了内容时才做后续清理（避免无修改时也加句号）
                if new_v == v:
                    continue
                # 剥离后若仍有孤立半括号（极端文本），一并清掉
                if new_v.count("（") != new_v.count("）"):
                    new_v = re.sub(r"[（）]", "", new_v)
                # 清理残留空段/多余标点
                new_v = re.sub(r"[，,。]{2,}", "。", new_v)
                new_v = new_v.strip("。，, ") + ("。" if new_v.strip() else "")
                if new_v != v:
                    shot[fld] = new_v
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx,
                            action="rewrite",
                            target=fld,
                            before=v,
                            after=new_v,
                        )
                    )
        return repairs
