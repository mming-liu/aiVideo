"""F13. 景别多样性达标约束（v6.62.1，09-06）.

09-06 chapter_001 实测：11 镜只用 4 种景别（远景/中远景/中景/中近景），
_check_shot_size_diversity 每轮报「4 种 < 5 要求」——framing_diversity_v2 只修
「连续 3+ 镜同景别」（本 run 无连续 3 镜同景别 → 死亡约束），全局缺项景别
无任何修复器，告警永不收敛。

修法（确定性补缺，不依赖 LLM regen）：全片 unique 景别 < 5 时，从**内容适配**
的镜头按规则改 1 镜的 framing 词，每轮补 1 种直到达标：

候选规则（内容 → 目标景别，只挑规则命中的镜，不硬乱改）：
  a. 对白镜（narration 有冒号台词）+ framing=中景 → 近景（对话近景，规则22.8 正反打惯例）
  b. 末镜（拉镜/拉远收尾）+ framing≠全景 → 全景（收尾拉开，剪辑惯例）
  c. 空镜 + framing=远景 → 大远景（空镜拉远不损内容）
  d. 特写语境镜（五官/洇痕词）+ framing≠特写 → 特写

幂等：unique ≥ 5 后整体跳过；被改镜已含目标词跳过。
只改 prompt 中第一个 framing 词（与 framing_diversity_v2 同口径），
不改 ff_desc/lf_desc（它们描述的是画面内容，不是构图指令）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity

_FRAMING_WORDS = (
    "大特写", "中远景", "中近景", "大远景",
    "特写", "中景", "全景", "远景", "鸟瞰", "近景",
)


def _extract_framing(prompt: str) -> str | None:
    if not isinstance(prompt, str):
        return None
    for kw in _FRAMING_WORDS:
        if kw in prompt:
            return kw
    return None


# 特写语境词（与 F11 _CLOSEUP_WORDS 同源，勿漂移）
_CLOSEUP_WORDS = ("瞳孔", "双目", "双眼", "眼神", "眼角", "嘴唇", "指尖",
                  "洇痕", "睫毛", "面部特写")


def _candidates(shot: dict, idx: int, total: int, is_last: bool) -> list[str]:
    """该镜适合改成哪些景别（按优先级；空 list = 不适合动）."""
    prompt = str(shot.get("prompt") or "")
    narration = str(shot.get("narration_segment") or "")
    out: list[str] = []
    # a. 对白镜 中景 → 近景
    if narration and ("：" in narration or "道" in narration[:6]) \
            and _extract_framing(prompt) == "中景":
        out.append("近景")
    # b. 末镜拉远 → 全景
    if is_last and ("拉" in prompt or _extract_framing(prompt) in ("远景", "中景")):
        out.append("全景")
    # c. 空镜 远景 → 大远景
    ms = str(shot.get("main_subject") or "")
    if "空镜" in ms and _extract_framing(prompt) == "远景":
        out.append("大远景")
    # d. 特写语境
    if any(w in prompt for w in _CLOSEUP_WORDS) and _extract_framing(prompt) not in ("特写", "大特写"):
        out.append("特写")
    return out


class FramingVarietyConstraint(Constraint):
    """F13. 景别多样性达标：unique < 5 → 内容适配补缺景别."""

    name = "framing_variety"
    severity = Severity.WARN
    fields = ("prompt",)
    skip_hook = True

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = [s for s in (ctx.shots or []) if isinstance(s, dict)]
        if len(shots) < 3:
            return repairs

        used = {
            _extract_framing(s.get("prompt", "") or "") for s in shots
        } - {None}
        target = 5
        if len(used) >= target:
            return repairs

        for i, s in enumerate(shots):
            if len(used) >= target:
                break
            is_last = i == len(shots) - 1
            prompt = str(s.get("prompt") or "")
            current = _extract_framing(prompt)
            if not current:
                continue
            for goal in _candidates(s, i, len(shots), is_last):
                if goal in used or goal == current:
                    continue
                # 只替换第一个 framing 词出现处
                new_prompt = prompt.replace(current, goal, 1)
                if new_prompt == prompt:
                    continue
                s["prompt"] = new_prompt
                used.add(goal)
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=s.get("index", i),
                        action="rewrite",
                        target="prompt",
                        before=prompt[:60],
                        after=new_prompt[:60],
                    )
                )
                break
        return repairs
