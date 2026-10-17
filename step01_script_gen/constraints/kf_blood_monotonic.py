"""Report blood continuity candidates; blood words do not identify a fatal wound."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.kf_time import get_kf_t_sec
from step01_script_gen.prompt_semantic.registry import (
    BLOOD_STATE_WORDS,
    INJURY_ANCHOR_TAIL,
)
from .base import Constraint
from .types import RepairRecord, Severity, Violation


class KfBloodMonotonicConstraint(Constraint):
    """F2. kf 血状态单调：prev 有血 → cur 必须有血，否则追加锚词.

    **契约边界（审核发现 6 #3）**：本约束只表达「最终伤情连续」——前序帧有血时
    后续帧与尾帧不得把血写没。它**不是死亡姿态证据**：往 `lf_desc` 追加的伤情锚
    只说明伤处仍在，不得据此推断角色已死。

    伤情锚也不能证明闭眼或头颈僵直。v6_43 不再补造这些状态，
    无论本约束先后执行，都只保留已经明确的伤情与原有姿态。
    两种顺序的有限夹具回归见 test_constraint_order_dependency.py。
    """

    name = "kf_blood_monotonic"
    severity = Severity.WARN
    fields = ()  # 显式空——只扫 keyframes[N].desc
    repair_marker = INJURY_ANCHOR_TAIL  # 已含 tail 视为已修

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            kfs = shot.get("keyframes") or []
            if not isinstance(kfs, list):
                continue

            # 按 t_sec 升序收集 (t, desc, kf_idx)
            indexed: list[tuple[float, int]] = []
            for j, kf in enumerate(kfs):
                if not isinstance(kf, dict):
                    continue
                t = get_kf_t_sec(kf, shot)
                if t is None:
                    continue
                indexed.append((float(t), j))
            indexed.sort(key=lambda x: x[0])

            # 维护一个 "blood_active" 状态：一旦在前序 kf 看到血词，
            # 所有后续 kf 必须保留血词（否则视为回退）
            blood_active = False if len(indexed) >= 2 else any(
                any(w in (kf.get("desc", "") or "") for w in BLOOD_STATE_WORDS)
                for kf in kfs if isinstance(kf, dict)
            )
            for t, kf_j in (indexed if len(indexed) >= 2 else []):
                kf = kfs[kf_j]
                if not isinstance(kf, dict):
                    continue
                desc = kf.get("desc", "") or ""
                if not isinstance(desc, str):
                    continue
                # v6.50（08-29）：血状态判定改用 BLOOD_STATE_WORDS——
                # 宽表含"白袍"，开篇 kf「杨旭白袍盘坐」即置位 blood_active →
                # 后续 kf 全被判"血回退"而误追加伤处锚（shot1 三 kf 实测）
                has_blood = any(w in desc for w in BLOOD_STATE_WORDS)
                if has_blood:
                    # 本帧含血 → blood_active 保持 True
                    blood_active = True
                    continue
                if blood_active:
                    # 前序有血，本帧丢了 → 血回退
                    # 幂等检查：本帧已含 INJURY_ANCHOR_TAIL 视为已修
                    if INJURY_ANCHOR_TAIL in desc:
                        continue
                    new_desc = desc
                    ctx.record_violation(Violation(
                        constraint_name=self.name, severity=Severity.WARN,
                        shot_index=shot.get("index"), field=f"keyframes[{kf_j}].desc",
                        message="血迹连续性候选；需核所属人物、伤处及本帧可见范围，保留原文。",
                        evidence=desc, repaired=False))
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=shot.get("index") if not is_hook else None,
                            action="detect",
                            target=f"keyframes[{kf_j}].desc",
                            before=desc[:80] + ("..." if len(desc) > 80 else ""),
                            after=new_desc[:80] + ("..." if len(new_desc) > 80 else ""),
                        )
                    )

            # kf 已经有血时，尾帧也必须保持；否则 step02b 最后一帧会画出
            # 血迹消失，直接违反致命伤持续规则。
            if blood_active:
                lf_desc = shot.get("lf_desc", "") or ""
                if isinstance(lf_desc, str) and lf_desc and not any(
                    w in lf_desc for w in BLOOD_STATE_WORDS
                ):
                    new_lf = lf_desc
                    ctx.record_violation(Violation(
                        constraint_name=self.name, severity=Severity.WARN,
                        shot_index=shot.get("index"), field="lf_desc",
                        message="尾帧血迹连续性候选；不据其它帧的血词推定本帧伤处。",
                        evidence=lf_desc, repaired=False))
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=shot.get("index") if not is_hook else None,
                            action="detect",
                            target="lf_desc",
                            before=lf_desc[:80] + ("..." if len(lf_desc) > 80 else ""),
                            after=new_lf[:80] + ("..." if len(new_lf) > 80 else ""),
                        )
                    )
        return repairs
