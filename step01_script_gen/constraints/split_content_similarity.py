"""A7. 拆分镜内容重复检测（v6.58, 2026-09-03）.

08-29 实测：shot10/10.5、shot11.5/12.5 等拆镜对，split_visuals 的「视觉
差异化」只改 ff/lf/prompt 上的装饰（景别/过肩/情绪/焦点），但**核心动作词**
（拔剑/格挡/跃起）是同一件事。LLM 没意识到要换动作，约束层硬修复只能改
"怎么画"，不能改"画什么"。

本约束 = 检测层（WARN，不 regen）：
  - 用字符 bigram Jaccard 检测「动作基底」（motion+prompt+lf_desc）重叠
  - 阈值 ≥ 0.7 视为重复 → 记 WARN Violation（反馈给 LLM regen）
  - 不自动合并（合并会破坏叙事节奏，LLM 应该重写动作而非字面合并）

severity=WARN: 检测性约束，不直接修复，让 regen 反馈链处理。
幂等：纯检测，多次扫描结果相同。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.split_similarity import (
    is_split_pair,
    split_pair_similarity,
)
from .base import Constraint
from .types import RepairRecord, Severity, Violation


# 阈值：Jaccard ≥ 0.7 视为「核心动作重复」
# 实测：完全相同 = 1.0；差 2 字 = 0.714；差 5 字 = 0.5 左右
SIMILARITY_THRESHOLD = 0.7


class SplitContentSimilarityConstraint(Constraint):
    """A7. 拆分镜内容重复检测.

    扫所有 shot，找相邻 shot_b（带 _split_from 标记的副镜）和它前面的
    shot_a（被拆的原镜）。如果动作基底相似度 ≥ 0.7，记 WARN.
    """

    name = "split_content_similarity"
    severity = Severity.WARN
    fields = ()  # 不扫单字段，处理的是跨镜对
    skip_hook = True  # hook 独立，不参与

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = ctx.shots if isinstance(ctx.shots, list) else []
        if len(shots) < 2:
            return repairs

        # 扫每个副镜（_split_from 标记 + 浮点 index），找它前面的原镜
        for i, shot_b in enumerate(shots):
            if not is_split_pair(shot_b):
                continue
            # 找原镜：往前找最近的 index == _split_from 的 shot
            split_from = shot_b.get("_split_from")
            shot_a = None
            for j in range(i - 1, -1, -1):
                candidate = shots[j]
                if not isinstance(candidate, dict):
                    continue
                try:
                    cand_idx = float(candidate.get("index", 0) or 0)
                    target = float(split_from) if split_from is not None else -1
                    if cand_idx == target:
                        shot_a = candidate
                        break
                except (TypeError, ValueError):
                    continue
            if shot_a is None:
                # 找不到原镜（异常情况），跳过
                continue

            sim = split_pair_similarity(shot_a, shot_b)
            if sim < SIMILARITY_THRESHOLD:
                continue

            # 记录 WARN Violation
            ctx.record_violation(
                Violation(
                    constraint_name=self.name,
                    severity=Severity.WARN,
                    shot_index=shot_b.get("index"),
                    field="motion_desc+prompt+lf_desc",
                    message=(
                        f"拆分镜对 (shot_a={shot_a.get('index')}, "
                        f"shot_b={shot_b.get('index')}) 动作基底相似度 {sim:.2f} "
                        f"≥ {SIMILARITY_THRESHOLD}，核心动作可能重复——"
                        f"建议 LLM 在 regen 中给 shot_b 换核心动作或合并到 shot_a。"
                    ),
                )
            )
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=shot_b.get("index"),
                    action="detect",
                    target="split_pair_similarity",
                    before=f"sim={sim:.3f}",
                    after="(WARN, 触发 LLM regen 反馈)",
                )
            )
        return repairs
