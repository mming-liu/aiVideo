"""opening_character_presence — 前 2 镜必须含 1 个有角色出场的镜（通用兜底）.

通用化（v6.62.2）：R2 兜底——不写死"主角/杨旭"，而是从 script.characters 取所有
已注册角色名（含 LLM 提取的角色 identifier）；只要前 2 镜任意一镜 main_subject
含其中任一角色名即视为合规。**完全通用**，换小说自动跟随。
"""
from __future__ import annotations

from .base import Constraint
from .types import RepairRecord, Severity


class OpeningCharacterPresenceConstraint(Constraint):
    """前 2 镜至少 1 个 main_subject 含 script.characters 中的任一角色名.

    触发场景：开篇连续空镜/群演/特写导致人物晚到，钩子张力不足。
    设计：检测即报 warning（不改写——补角色是 LLM 职责，硬写违反"模型不脑补"原则）。
    """

    name = "opening_character_presence"
    severity = Severity.WARN
    fields = ("main_subject",)
    skip_hook = True

    _WINDOW = 2  # 前 2 镜（不含 hook）

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = ctx.shots
        if len(shots) < self._WINDOW:
            return repairs
        window = shots[: self._WINDOW]
        if not all(isinstance(shot, dict) for shot in window):
            return repairs

        # 通用化（v6.62.2）：从 ctx.script.characters 动态取所有角色名——
        # LLM 提取的 identifier（如"杨旭"/"林平之"/"那女子"等），不含任何硬编码。
        script_chars = []
        script = getattr(ctx, "script", None) or {}
        for c in script.get("characters", []) or []:
            if isinstance(c, dict):
                name = c.get("identifier") or c.get("name")
                if name and isinstance(name, str):
                    script_chars.append(name.strip())
        # 兜底：从 window 自身 main_subject 抽 2-3 字中文片段作"伪角色"——避免零角色
        # 极端情况（character_extractor 全失败）下规则整体失明。
        if not script_chars:
            for shot in window:
                ms = str(shot.get("main_subject") or "").strip()
                if ms and "空镜" not in ms and "群演" not in ms:
                    script_chars.append(ms)
        if not script_chars:
            return repairs  # 无任何角色信息，无法判断，直接放行

        for shot in window:
            ms = str(shot.get("main_subject") or "")
            if any(name in ms for name in script_chars):
                return repairs  # 至少 1 镜有角色出场，规则满足

        repairs.append(RepairRecord(
            constraint_name=self.name,
            shot_index=window[0].get("index", 0),
            action="warn",
            target="main_subject",
            detail=(
                f"前 {self._WINDOW} 镜均无角色出场（main_subject 未含 script.characters 任一角色名），"
                f"开篇张力不足。建议至少 1 镜加入主要角色作钩子。"
            ),
        ))
        return repairs
