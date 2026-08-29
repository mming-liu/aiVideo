"""J3. 运镜缺失补填约束（v6.47，08-27）.

_check_camera_movement 对「prompt/ff/motion 无任何运镜/机位/静态词」的镜头告警
「缺少运镜/机位描述」（08-27 实测 10 镜）。LLM 常漏写运镜语言 → 确定性补填：
  - 命中角色运镜 registry（远景建立→推近 / 打斗→跟拍 / 质问→推近 / 反应→环绕）
    → 补对应运镜 tail（复用 camera_variety._CAMERA_BY_SHOT_ROLE 单一事实源）
  - 未命中任何角色 → 补「固定机位」（_check_camera_movement 认可的合法默认）

幂等：文本已含任一运镜/机位/静态词 → 跳过；补填后词已含 → 再跑 0 修复。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.validation import (
    _MOVEMENT_TYPE_TERMS, _ANGLE_TECHNIQUES, _STATIC_MARKERS,
)
from .base import Constraint
from .types import RepairRecord, Severity


class CameraMovementFillConstraint(Constraint):
    """J3. 运镜缺失补填：无运镜语言 → 按角色补运镜 / 兜底固定机位.

    severity=WARN: 确定性补填，无需 regen.
    """

    name = "camera_movement_fill"
    severity = Severity.WARN
    fields = ("prompt",)

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            prompt = str(shot.get("prompt") or "")
            text = " ".join([
                prompt,
                str(shot.get("ff_desc") or ""),
                str(shot.get("motion_desc") or ""),
            ])
            # 幂等：已含运镜/机位/静态语言 → 跳过
            if self._has_camera_language(text):
                continue
            tail = self._pick_tail(shot)
            if not tail:
                continue
            before = prompt
            shot["prompt"] = prompt.rstrip("。") + tail
            repairs.append(
                RepairRecord(
                    constraint_name=self.name,
                    shot_index=idx,
                    action="append",
                    target="prompt",
                    before=before,
                    after=shot["prompt"],
                )
            )
        return repairs

    @staticmethod
    def _has_camera_language(text: str) -> bool:
        """与 _check_camera_movement 判定一致：命中运镜/机位/静态/空镜任一类即算有."""
        for terms in _MOVEMENT_TYPE_TERMS.values():
            if any(t in text for t in terms):
                return True
        if any(a in text for a in _ANGLE_TECHNIQUES):
            return True
        if any(m in text for m in _STATIC_MARKERS):
            return True
        return "空镜" in text

    @staticmethod
    def _pick_tail(shot: dict) -> str:
        """按角色运镜 registry 选 tail；未命中 → 固定机位（检查认可的默认）."""
        try:
            from .camera_variety import _CAMERA_BY_SHOT_ROLE
            for label, tail, cond in _CAMERA_BY_SHOT_ROLE:
                try:
                    if cond(shot):
                        return tail
                except Exception:
                    continue
        except Exception:
            pass
        return "。固定机位"
