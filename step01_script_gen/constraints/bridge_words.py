"""W13. 镜头衔接词约束（v6.42 迁移自 _hard_fix_bridge_words）.

连续 3+ 镜同 location 且 prompt/context_bridge 都缺 _BRIDGE_TRANSITION_WORDS
任一词 → 追加 prompt 末尾"然后..."+ context_bridge 拼接"紧接着..."。

幂等：prompt 或 context_bridge 任一已含 transition word → 跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


_BRIDGE_TRANSITION_WORDS = (
    "继续", "然后", "紧接着", "随后", "随即", "紧接",
    "瞬间", "刹那", "突然", "猛然", "骤然",
)
_BRIDGE_TAIL_PROMPT = "。然后镜头继续，前镜动作与构图自然过渡到本镜"
_BRIDGE_TAIL_CONTEXT = "紧接着延续前镜动作与构图，自然过渡到本镜。"


class BridgeWordsConstraint(Constraint):
    """W13. 镜头衔接词：3+ 同 location 镜缺 transition word → 追加 + context_bridge.

    severity=WARN: 衔接词可由后续 repair round 修.
    repair_marker="然后"（含"继续/随后/随即/紧接/瞬间/刹那/突然/猛然/骤然"
    任一视为已修）.
    """

    name = "bridge_words"
    severity = Severity.WARN
    repair_marker = "然后"
    fields = ("prompt", "context_bridge")

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = ctx.shots
        if not shots or not isinstance(shots, list):
            return repairs
        # 找出连续 3+ 镜同 location 段
        runs: list[tuple[int, int]] = []
        cur_start = 0
        cur_loc = None
        for i, s in enumerate(shots):
            if not isinstance(s, dict):
                continue
            loc = s.get("location", "") or s.get("scene", "")
            if loc == cur_loc:
                continue
            if cur_loc and i - cur_start >= 3:
                runs.append((cur_start, i - 1))
            cur_start = i
            cur_loc = loc
        if cur_loc and len(shots) - cur_start >= 3:
            runs.append((cur_start, len(shots) - 1))

        for start, end in runs:
            if start >= end:
                continue
            for idx in range(start, end + 1):
                # v6.43（08-23 审计）：段首 shot 没有前镜，追加「前镜动作…过渡到本镜」
                # 与「开场建立」自相矛盾；段尾 + 中间才需要衔接词。
                if idx == start:
                    continue
                s = shots[idx]
                if not isinstance(s, dict):
                    continue
                if self._already_repaired(s):
                    continue
                prompt = s.get("prompt", "") or ""
                cb = (s.get("context_bridge", "") or "").strip()
                if cb and any(w in cb for w in _BRIDGE_TRANSITION_WORDS):
                    continue
                before_p = prompt
                s["prompt"] = prompt.rstrip("。") + _BRIDGE_TAIL_PROMPT
                if cb:
                    s["context_bridge"] = cb.rstrip("。") + "，" + _BRIDGE_TAIL_CONTEXT
                else:
                    s["context_bridge"] = _BRIDGE_TAIL_CONTEXT
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=s.get("index", idx),
                        action="append",
                        target="prompt",
                        before=before_p,
                        after=s["prompt"],
                    )
                )
        return repairs
