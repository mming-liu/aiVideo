"""W13. 镜头衔接词约束（v6.42 迁移自 _hard_fix_bridge_words）.

连续 3+ 镜同 location 且 prompt/context_bridge 都缺 _BRIDGE_TRANSITION_WORDS
任一词 → 在 context_bridge 追加「延续前镜动作与构图」的衔接说明。

S02（2026-11-11）：本约束此前追加的是「紧接着延续前镜动作与构图，自然过渡到本镜」。
「紧接着」是一条**时间断言**——遇到合法的场景转换或时间省略时，它把两段分开的
时间写成连续动作，视频模型会据此演成一镜。现在：① 本镜已声明时间跳跃（scene 变化 /
`clip_transition` 表明时间跳跃 / 显式 `time_jump`）时**跳过**；② location 为空时
不再把整篇当成一个「同 location 段」；③ 追加文字只声明构图与空间承接，不声明时间紧邻。

幂等：context_bridge 已含衔接标记或任一 transition word → 跳过。
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
#: 本约束写入 context_bridge 的固定前缀（幂等锚；不含时间紧邻断言）
_BRIDGE_MARKER = "延续前镜动作与构图"
_BRIDGE_TAIL_CONTEXT = _BRIDGE_MARKER + "，空间关系不变。"
#: 声明「本镜与前镜之间发生过时间跳跃/场景转换」的转场词。出现其一即不再补衔接说明：
#: 这类切点本来就是分开的时间或空间，补「延续前镜动作」会抹掉省略。
_TIME_JUMP_TRANSITION_WORDS = (
    "淡出", "淡入淡出", "黑场", "叠化", "闪回", "回忆", "跳切", "时间流逝",
)


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
        # 找出连续 3+ 镜同 location 段（location 为空**不构成**段：整篇空 location
        # 曾被当成一个段，于是除首镜外全部被补上「前镜动作…」，等于凭空声明连续）
        runs: list[tuple[int, int]] = []
        cur_start = 0
        cur_loc = None
        for i, s in enumerate(shots):
            if not isinstance(s, dict):
                continue
            loc = (s.get("location") or s.get("scene") or "").strip()
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
                # S02：本镜已声明时间跳跃/场景转换 → 合法的省略，不得补「延续前镜动作」
                if self._declares_time_jump(shots[idx - 1], s):
                    continue
                if self._already_repaired(s):
                    continue
                prompt = s.get("prompt", "") or ""
                cb = (s.get("context_bridge", "") or "").strip()
                if cb and any(w in cb for w in _BRIDGE_TRANSITION_WORDS):
                    continue
                before_cb = cb
                if cb:
                    s["context_bridge"] = cb.rstrip("。") + "，" + _BRIDGE_TAIL_CONTEXT
                else:
                    s["context_bridge"] = _BRIDGE_TAIL_CONTEXT
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=s.get("index", idx),
                        action="set_field",
                        target="context_bridge",
                        before=before_cb,
                        after=s.get("context_bridge", ""),
                    )
                )
        return repairs

    def _already_repaired(self, s: dict) -> bool:
        text = str(s.get("prompt") or "") + " " + str(s.get("context_bridge") or "")
        if _BRIDGE_MARKER in text:
            return True
        return any(w in text for w in _BRIDGE_TRANSITION_WORDS)

    @staticmethod
    def _declares_time_jump(prev, shot) -> bool:
        """本镜是否已声明与前镜之间发生过时间跳跃或场景转换.

        只看**本稿显式声明**的依据，不猜内容：显式 `time_jump`、`clip_transition`
        里的时间跳跃类转场词、`scene` 变化。命中即视为合法省略，不补衔接说明。
        """
        if not isinstance(shot, dict):
            return False
        if shot.get("time_jump"):
            return True
        ct = str(shot.get("clip_transition") or "")
        if any(w in ct for w in _TIME_JUMP_TRANSITION_WORDS):
            return True
        prev_scene = str((prev or {}).get("scene") or "").strip() if isinstance(prev, dict) else ""
        scene = str(shot.get("scene") or "").strip()
        return bool(prev_scene and scene and prev_scene != scene)