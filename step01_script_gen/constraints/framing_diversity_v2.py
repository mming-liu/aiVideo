"""I2. 景别多样性 v2 约束（v6.42 迁移自 _hard_fix_framing_diversity_v2）.

检测连续 3+ 镜同 framing 词 → 把中间一镜的 framing 词替换为相邻景别
（中近景→中景/特写，中景→全景，全景→远景），打破连镜。

注意：旧版 _hard_fix_shot_size_diversity（全局缺项景别补 shot[0] 末尾）保留为
独立 constraint；本 constraint 只做"连续 3+ 镜同景别"修正。

幂等：替换后景别与前后不同，再次调用扫描会跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.validation import _FRAMING_REPLACE
from .base import Constraint
from .types import RepairRecord, Severity


_FRAMING_WORDS = (
    "大特写", "特写", "中近景", "中景", "中远景",
    "全景", "远景", "大远景", "鸟瞰",
)


def _extract_framing(prompt: str) -> str | None:
    """从 prompt 提取第一个景别词（按 _FRAMING_WORDS 顺序匹配）."""
    if not isinstance(prompt, str):
        return None
    for kw in _FRAMING_WORDS:
        if kw in prompt:
            return kw
    return None


class FramingDiversityV2Constraint(Constraint):
    """I2. 景别多样性 v2：连续 3+ 镜同景别 → 中间一镜替换.

    severity=WARN: 景别多样性可由后续 repair round 修.
    repair_marker=None: 本约束不依赖 repair_marker；通过「再次扫描发现景别已
    不同」实现幂等（替换后 framing 词与前后镜不同 → 连续 run 被打破）.
    """

    name = "framing_diversity_v2"
    severity = Severity.WARN
    fields = ("prompt",)
    skip_hook = True  # hook 单独成镜，混入会污染判定

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        if not isinstance(ctx.shots, list) or len(ctx.shots) < 3:
            return repairs

        # 2026-11-01 S16-1（M09 根因 A）：**一趟内跑到不动点**。
        # 旧实现每段只改一个中点：12 镜全同景别时第 1 趟改 mid=5，左右两侧
        # [0..4]（5 镜）与 [6..11]（6 镜）仍 >=3；第 2 趟又改 mid=2/8，末尾
        # [9..11] 仍 >=3；第 3 趟再改 mid=10。于是同进程多次 finalize 每次
        # revision 都变（fb465089 → 2d9aa118 → 9a02374d）。
        # 有界循环保证一次 apply 即达不动点，且不改任何调用方契约。
        for _ in range(len(ctx.shots)):
            if not self._apply_once(ctx, repairs):
                break
        return repairs

    def _apply_once(self, ctx, repairs: list[RepairRecord]) -> bool:
        """扫一遍连续同景别段并各改一个中点；返回本轮是否发生替换."""
        changed = False
        # 第一步：扫连续同景别段
        sequences: list[tuple[int, int, str | None]] = []
        cur_start = 0
        cur_framing: str | None = None
        for i, s in enumerate(ctx.shots):
            if not isinstance(s, dict):
                continue
            sz = _extract_framing(s.get("prompt", "") or "")
            if sz == cur_framing:
                continue
            if cur_framing is not None and i - cur_start >= 3:
                sequences.append((cur_start, i - 1, cur_framing))
            cur_start = i
            cur_framing = sz
        if cur_framing is not None and len(ctx.shots) - cur_start >= 3:
            sequences.append((cur_start, len(ctx.shots) - 1, cur_framing))

        # 第二步：每段挑中间一镜换 framing
        for start, end, framing in sequences:
            if start >= end or not framing:
                continue
            # 与原 _hard_fix_framing_diversity_v2 一致：dict 缺省用 "中景" 兜底
            new_framing = _FRAMING_REPLACE.get(framing, "中景")
            if new_framing == framing:
                continue
            mid = start + (end - start) // 2
            target = ctx.shots[mid]
            if not isinstance(target, dict):
                continue
            prompt = target.get("prompt", "") or ""
            # 幂等：已含目标 framing → 已修
            if new_framing in prompt:
                continue
            new_prompt = prompt.replace(framing, new_framing, 1)
            if new_prompt == prompt:
                continue
            before = prompt
            target["prompt"] = new_prompt
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=target.get("index", mid),
                    action="rewrite",
                    target="prompt",
                    before=before,
                    after=new_prompt,
                )
            )
            changed = True
        return changed
