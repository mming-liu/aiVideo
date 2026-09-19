"""D. kf 时序矛盾约束（v6.42 迁移自 _hard_fix_kf_pre_post_conflict；v6.76 只报告）.

检测三类缺陷：
  1. kf 帧内 pre+post 矛盾——同一帧既含 pre-state（"刺客半身探出举刀"）
     又含角色已"躺血"的 post-state（"杨旭仰卧 + 心口洇痕"）
  2. 纯 pre 但 t > 0.33×dur
  3. 纯 post 但 t < 0.67×dur

历史修法（v6.42~v6.73）：
  - 矛盾 → 删 pre 词保留 post，并把 t 压到 0.5×dur
  - 纯 pre → 压到 0.2×dur；纯 post → 拉到 0.8×dur

v6.76（2026-09-19）降为**只报告**，不再删词、不再搬时刻。原因：
  1. `0.2 / 0.5 / 0.8 × dur` 是三个魔数，没有任何原文依据。它假设「一镜内部
     时间均匀分布」，而真实的动作节拍由原文事件决定，不由镜头时长均分。
  2. 「删 pre 词」是**内容层改写**：删掉的是原文有据的画面要素，属于用推断
     覆盖事实。pre/post 词表（`_KF_PRE_KW` / `_KF_POST_KW`）是关键词启发式，
     误删后无法恢复。
  3. 本约束跑在链中段，其后 kf_t_sec_bounds / kf_timeline_normalize /
     _KfTimeWindowAlignmentFinal（v6.76 已删除） 会再次移动同一批帧，修复不保持。
  4. 时刻与内容归属只能由事件结构决定，见
     docs/step01-temporal-writers-audit-2026-09-19.md。

现状：record_violation(repaired=False) + RepairRecord(action="warn")，保留原值交 regen。

⚠️ `name` 是 `kf_temporal_consistency`（**不是文件名**）——开关变量为
`CONSTRAINT_KF_TEMPORAL_CONSISTENCY_ENABLED`，按文件名去关它无效。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.kf_time import get_kf_t_sec
from step01_script_gen.validation import _KF_POST_KW, _KF_PRE_KW
from .base import Constraint
from .types import RepairRecord, Severity, Violation


_LAYING_WORDS = ("仰卧", "后仰", "倒地", "倒下", "坠落", "跌落")
_BLOOD_WORDS = ("洇痕", "暗红", "血")


def _kf_index(kfs: list, kf: dict) -> int:
    """返回 kf 在 kfs 列表中的索引（用于 RepairRecord.target 路径）."""
    try:
        return kfs.index(kf)
    except ValueError:
        return -1


class KfPrePostConflictConstraint(Constraint):
    """D. kf 帧内 pre+post 矛盾 / 纯 pre / 纯 post 时间错位：只检测并上报（v6.76 起）."""

    name = "kf_temporal_consistency"
    severity = Severity.WARN
    fields = ()  # 不扫文本字段，处理的是 keyframes 列表

    def _report(
        self, ctx, repairs: list[RepairRecord], idx, *, target: str,
        message: str, evidence: str, detail: str,
    ) -> None:
        ctx.record_violation(Violation(
            constraint_name=self.name,
            severity=self.severity,
            shot_index=idx,
            field=target,
            message=message,
            evidence=evidence[:200],
            repaired=False,
        ))
        repairs.append(RepairRecord(
            constraint_name=self.name,
            shot_index=idx,
            action="warn",
            target=target,
            before=evidence[:80],
            after=evidence[:80],
            detail=detail,
        ))

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            try:
                dur = float(shot.get("duration_sec") or shot.get("duration") or 0)
            except (TypeError, ValueError):
                dur = 0
            if dur <= 0:
                continue

            # 范围界定（v6.76 起是 scope 决策，不再是幂等性妥协）：
            # motion_desc 有显式时段时，该镜的时刻由时段定义，纯 pre/post
            # 关键词时间猜想不适用，报了也是噪声。
            motion = str(shot.get("motion_desc") or "").strip()
            if motion:
                try:
                    from step01_script_gen.validation import _parse_motion_time_segments
                    if _parse_motion_time_segments(motion, dur):
                        continue
                except Exception:
                    pass

            kfs = shot.get("keyframes") or []
            if not isinstance(kfs, list):
                continue

            for kf in kfs:
                if not isinstance(kf, dict):
                    continue
                t = get_kf_t_sec(kf, shot)
                if t is None or t <= 0 or t >= dur:
                    continue
                desc = kf.get("desc") or ""
                # 【角色锚定】块内的「正对镜头/仰卧」等词会令 is_pre/has_laying
                # 恒真（自触发），故检测只在锚定之前的正文进行，锚定原样保留。
                body = desc
                if "【角色锚定】" in desc:
                    body, _anchor_tail = desc.split("【角色锚定】", 1)
                is_pre = any(kw in body for kw in _KF_PRE_KW)
                is_post = any(kw in body for kw in _KF_POST_KW)
                if is_pre and is_post:
                    continue
                has_laying = any(w in body for w in _LAYING_WORDS)
                has_blood = any(w in body for w in _BLOOD_WORDS)
                _j = _kf_index(kfs, kf)

                # 真实矛盾：pre + 角色已「躺血」
                if is_pre and has_laying and has_blood:
                    self._report(
                        ctx, repairs, idx, target=f"keyframes[{_j}].desc",
                        message=(
                            "关键帧同一画面内既含动作前状态（pre）又含角色已卧倒带血"
                            "（post）的矛盾描述；保留原文未删 pre 词、未改时刻——"
                            "删词是用推断覆盖原文画面要素，交 regen 重写该帧。"
                        ),
                        evidence=body,
                        detail="关键帧 pre/post 矛盾，保留原文交由 audit/regen",
                    )
                    continue
                # 纯 pre 但靠后
                if is_pre and t > dur * 0.33:
                    self._report(
                        ctx, repairs, idx, target=f"keyframes[{_j}].t_sec",
                        message=(
                            f"纯 pre 状态帧位于 t={t:.3f}s（>{0.33:.2f}×{dur:.2f}s）；"
                            "保留原值未压到 0.2×dur——该比例是无原文依据的魔数，"
                            "交 regen 按事件节拍定时刻。"
                        ),
                        evidence=f"t={t:.3f}, dur={dur:.3f}",
                        detail="纯 pre 帧时刻靠后，保留原值交由 audit/regen",
                    )
                # 纯 post 但靠前
                elif is_post and t < dur * 0.67:
                    self._report(
                        ctx, repairs, idx, target=f"keyframes[{_j}].t_sec",
                        message=(
                            f"纯 post 状态帧位于 t={t:.3f}s（<{0.67:.2f}×{dur:.2f}s）；"
                            "保留原值未拉到 0.8×dur——该比例是无原文依据的魔数，"
                            "交 regen 按事件节拍定时刻。"
                        ),
                        evidence=f"t={t:.3f}, dur={dur:.3f}",
                        detail="纯 post 帧时刻靠前，保留原值交由 audit/regen",
                    )
        return repairs
