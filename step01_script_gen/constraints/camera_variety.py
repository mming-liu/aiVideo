"""J2. 运镜多样性约束（v6.42 迁移自 _hard_fix_camera_variety）.

全片已用运镜 < 3 种时，按每镜角色判定补 1 个差异化运镜：
  - 远景建立镜 → "缓慢推近"
  - 对白镜（对话角色） → "过肩"
  - 打斗/刺杀镜 → "快速跟拍"
  - 质问/怒喝 → "缓推近"
  - 反应/特写 → "环绕镜"

注意：原 _hard_fix_camera_movement（旧版，缺运镜时补"固定机位"）保留为独立
constraint；本 constraint 只做"全片运镜 < 3 种时按角色补差异".

幂等：已含对应运镜词跳过；本镜已有 ≥2 个运镜词也不再叠加。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.validation import _MOVEMENT_TYPE_TERMS
from .base import Constraint
from .types import RepairRecord, Severity


# 静态标记词（已有这些时先剥离再加运镜词）
_STATIC_MARKERS_RE = re.compile(
    r"[。，]?\s*(固定机位|静止|不动|静态|锁机位|定格)\s*[，,。]?"
)

# 按角色判定 + 推荐运镜 tail
_CAMERA_BY_SHOT_ROLE: list[tuple[str, str, "callable"]] = [
    # 远景建立镜 → 推近
    (
        "远景建立",
        "。缓慢推近",
        lambda s: "远景" in (s.get("prompt", "") or "")
        and "推近" not in (s.get("prompt", "") or ""),
    ),
    # 打斗/刺杀镜 → 跟拍
    (
        "打斗/刺杀",
        "。快速跟拍",
        lambda s: any(
            kw in (s.get("main_subject", "") or "") + (s.get("motion_desc", "") or "")
            for kw in ("刺杀", "打斗", "搏斗", "搏杀", "偷袭", "伏击")
        )
        and "跟拍" not in (s.get("prompt", "") or ""),
    ),
    # 质问/怒喝 → 推近
    (
        "质问/怒喝",
        "。缓推近",
        lambda s: any(
            kw in (s.get("narration_segment", "") or "")
            for kw in ("怒", "喝", "质问", "厉声", "骂道")
        )
        and "推" not in (s.get("prompt", "") or ""),
    ),
    # 反应/特写 → 环绕
    (
        "反应/特写",
        "。环绕镜",
        lambda s: "中近景" in (s.get("prompt", "") or "")
        and "环绕" not in (s.get("prompt", "") or ""),
    ),
]


def _all_movement_words() -> set[str]:
    """展平 _MOVEMENT_TYPE_TERMS 的所有值到 set."""
    out: set[str] = set()
    for v in _MOVEMENT_TYPE_TERMS.values():
        if isinstance(v, (list, tuple, set)):
            out.update(v)
        elif isinstance(v, str):
            out.add(v)
    return out


class CameraVarietyConstraint(Constraint):
    """J2. 运镜多样性：全片 < 3 种运镜 → 按角色补差异化运镜.

    severity=WARN.
    repair_marker=None: 幂等由"已含运镜词跳过"保证.
    """

    name = "camera_variety"
    severity = Severity.WARN
    fields = ("prompt",)
    skip_hook = True

    def apply(self, ctx) -> list[RepairRecord]:
        if not isinstance(ctx.shots, list) or not ctx.shots:
            return []

        movement_words = _all_movement_words()
        if not movement_words:
            return []

        # 统计全片已用运镜种类
        used_movement: set[str] = set()
        for s in ctx.shots:
            if not isinstance(s, dict):
                continue
            text = " ".join([
                s.get("prompt", "") or "",
                s.get("ff_desc", "") or "",
                s.get("motion_desc", "") or "",
            ])
            for w in movement_words:
                if w in text:
                    used_movement.add(w)
        if len(used_movement) >= 3:
            return []

        # 缺多样性：按每镜角色挑差异化运镜
        repairs: list[RepairRecord] = []
        for s in ctx.shots:
            if not isinstance(s, dict):
                continue
            prompt = s.get("prompt", "") or ""
            cnt = sum(1 for w in movement_words if w in prompt)
            if cnt >= 2:
                continue
            for label, tail, predicate in _CAMERA_BY_SHOT_ROLE:
                if not predicate(s):
                    continue
                # 已含静态标记 → 先去掉再加
                if any(
                    m in prompt
                    for m in ("固定机位", "静止", "不动", "静态", "锁机位", "定格")
                ):
                    prompt = _STATIC_MARKERS_RE.sub("", prompt)
                    s["prompt"] = prompt
                # 已含本运镜词 → 跳过
                if any(w in prompt for w in movement_words):
                    break
                before = prompt
                s["prompt"] = prompt.rstrip("。") + tail
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=s.get("index"),
                        action="append",
                        target="prompt",
                        before=before,
                        after=s["prompt"],
                    )
                )
                prompt = s["prompt"]
                break
        return repairs
