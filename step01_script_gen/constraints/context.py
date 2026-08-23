"""约束执行的上下文.

ConstraintContext 携带一次 apply_constraints 调用所需的所有数据:
  - script / shots / hook（live view，可修改）
  - characters_bank / props_bank / scenes_bank（可选）
  - novel_text（原文，校验用）
  - markers（ElementMarkers sidecar，跨约束共享的派生状态）
  - 标志位（is_in_repair_loop / is_final_pass）

所有数据通过 context 传递，避免给 34 个 Constraint 子类都写构造器。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .element_markers import ElementMarkers


@dataclass
class ConstraintContext:
    """约束执行上下文（shots/hook 是 live view，可修改）.

    Attributes:
        script: 完整 script dict（含 shots / hook / characters / meta）
        shots: script["shots"]（live view，方便 Constraint 修改）
        hook: script.get("hook")（可能 None）
        characters_bank: 角色库 dict（{ident: Character}）—— A 约束用
        props_bank: 道具资产库 dict（{ident: Prop}）—— C 约束用
        scenes_bank: 场景资产库 dict（{ident: Scene}）—— E 约束用
        novel_text: 小说原文（grounding 校验用）
        markers: 跨约束共享的派生标记（ElementMarkers）
        total_duration: 预计算的总时长（秒）
        video_model_caps: 视频模型能力（duration_cap 等）
        is_in_repair_loop: True=main.py:1055 调用（repair round 后）
        is_final_pass: True=main.py:1664 调用（_compute_timecodes 后）
    """

    script: dict
    shots: list = field(default_factory=list)
    hook: Optional[dict] = None
    characters_bank: Optional[dict] = None
    props_bank: Optional[dict] = None
    scenes_bank: Optional[dict] = None
    novel_text: str = ""
    markers: Optional["ElementMarkers"] = None
    total_duration: float = 0.0
    video_model_caps: dict = field(default_factory=dict)
    is_in_repair_loop: bool = False
    is_final_pass: bool = False

    def __post_init__(self) -> None:
        """自动从 script 推导 shots/hook 引用（如未显式传入）."""
        if not self.shots and isinstance(self.script, dict):
            shots = self.script.get("shots")
            if isinstance(shots, list):
                self.shots = shots
        if self.hook is None and isinstance(self.script, dict):
            hook = self.script.get("hook")
            self.hook = hook if isinstance(hook, dict) else None

    @classmethod
    def from_script(
        cls,
        script: dict,
        *,
        characters_bank: Optional[dict] = None,
        props_bank: Optional[dict] = None,
        scenes_bank: Optional[dict] = None,
        novel_text: str = "",
        markers: Optional["ElementMarkers"] = None,
        is_in_repair_loop: bool = False,
        is_final_pass: bool = False,
    ) -> "ConstraintContext":
        """便捷工厂：从 script dict 构造 context.

        Args:
            script: 完整 script dict
            characters_bank: 角色库 dict（{ident: Character}）
            props_bank: 道具资产库 dict
            scenes_bank: 场景资产库 dict
            novel_text: 小说原文
            markers: ElementMarkers（如未传则自动创建空 markers）
            is_in_repair_loop: True=repair round 末尾
            is_final_pass: True=落盘前 final pass

        Returns:
            ConstraintContext 实例
        """
        # 延迟导入避免循环
        from .element_markers import ElementMarkers

        if markers is None:
            markers = ElementMarkers()
        return cls(
            script=script,
            characters_bank=characters_bank or {},
            props_bank=props_bank or {},
            scenes_bank=scenes_bank or {},
            novel_text=novel_text,
            markers=markers,
            is_in_repair_loop=is_in_repair_loop,
            is_final_pass=is_final_pass,
        )

    # ------------------------------------------------------------------
    # 记录通道（Constraint 调 record_* 上报，engine 调 drain_* 取出）
    # 用 side channel 避免 dataclass 字段污染
    # ------------------------------------------------------------------

    def record_repair(
        self,
        constraint_name: str,
        shot_index: Optional[int],
        action: str,
        target: str,
        before: str = "",
        after: str = "",
    ) -> None:
        """记录一次修复（Constraint 主动调用）."""
        from .types import RepairRecord

        record = RepairRecord(
            constraint_name=constraint_name,
            shot_index=shot_index,
            action=action,
            target=target,
            before=before,
            after=after,
        )
        if not hasattr(self, "_pending_repairs"):
            object.__setattr__(self, "_pending_repairs", [])
        self._pending_repairs.append(record)

    def record_violation(self, violation) -> None:
        """记录一次违例（即使已修复也记录，便于日志 + 回归测试）.

        Args:
            violation: Violation 实例（非 Violation 类型静默忽略）
        """
        from .types import Violation

        if not isinstance(violation, Violation):
            return
        if not hasattr(self, "_pending_violations"):
            object.__setattr__(self, "_pending_violations", [])
        self._pending_violations.append(violation)

    def drain_repairs(self) -> list:
        """取出并清空已记录的修复（engine 在 apply_constraints 末尾调用）."""
        from .types import RepairRecord

        result = getattr(self, "_pending_repairs", [])
        object.__setattr__(self, "_pending_repairs", [])
        return [r for r in result if isinstance(r, RepairRecord)]

    def drain_violations(self) -> list:
        """取出并清空已记录的违例（engine 调用）."""
        from .types import Violation

        result = getattr(self, "_pending_violations", [])
        object.__setattr__(self, "_pending_violations", [])
        return [v for v in result if isinstance(v, Violation)]
