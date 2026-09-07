"""L. 爆点密度约束：检测告警 + 历史注入残留剥离（v6.62 收敛）.

旧版（≤v6.61）在爆点密度不足时给末镜 prompt 追加「。局势骤变，N个爆点叠加」
——抽象节拍词对图片/视频模型无视觉意义，纯噪音 token。

v6.62 现行为：
  1. 剥离历史注入残留（climax_words.strip_injected_suffixes，幂等，留痕）
  2. 爆点密度不足 → ctx.record_violation 上报（WARN，不改写数据）。
     punch_density 在 validation._REGEN_FEEDBACK_EXCLUDE，不会引发 regen 空转。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.climax_words import strip_injected_suffixes
from step01_script_gen.validation import _PUNCH_WORDS
from .base import Constraint
from .types import RepairRecord, Severity, Violation


class PunchDensityConstraint(Constraint):
    """L. 爆点密度：每 30s 应有 1 爆点（v6.62 起检测告警，不注入）.

    severity=WARN：爆点密度属叙事全局问题，不进定向修复轮。
    """

    name = "punch_density"
    severity = Severity.WARN
    fields = ("prompt",)

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = ctx.shots
        if not shots or not isinstance(shots, list):
            return repairs

        # 1) 剥离历史注入残留（幂等）
        for s in shots:
            if not isinstance(s, dict):
                continue
            prompt = s.get("prompt")
            if not isinstance(prompt, str) or not prompt:
                continue
            cleaned = strip_injected_suffixes(prompt)
            if cleaned != prompt:
                s["prompt"] = cleaned
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=s.get("index", 0),
                        action="rewrite",
                        target="prompt",
                        before=prompt[:60],
                        after=cleaned[:60],
                        detail="剥离爆点注入残留（v6.62 收敛）",
                    )
                )

        # 2) 密度检测 → 只上报不改写
        punch_count = 0
        total_dur = 0.0
        for s in shots:
            if not isinstance(s, dict):
                continue
            text = " ".join([
                s.get("prompt", ""),
                s.get("narration_segment", ""),
                s.get("main_subject", ""),
                s.get("motion_desc", ""),
            ])
            if any(p in text for p in _PUNCH_WORDS):
                punch_count += 1
            try:
                total_dur += float(s.get("duration_sec") or s.get("duration") or 0)
            except (TypeError, ValueError):
                pass
        if total_dur <= 0:
            total_dur = 60.0
        needed = max(1, int(total_dur // 30) + (1 if total_dur % 30 else 0))
        if punch_count >= needed:
            return repairs

        shortage = needed - punch_count
        last = shots[-1] if isinstance(shots[-1], dict) else {}
        ctx.record_violation(
            Violation(
                constraint_name=self.name,
                severity=self.severity,
                shot_index=last.get("index"),
                field="prompt",
                message=(
                    f"爆点密度不足（{punch_count}/{needed}，缺 {shortage} 个）："
                    "建议在镜头内容里增加真实的冲突/动作爆点，不要注入节拍词"
                ),
                evidence=str(last.get("prompt") or "")[:60],
            )
        )
        return repairs
