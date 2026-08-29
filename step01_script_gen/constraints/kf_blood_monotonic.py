"""F2. kf 血状态单调性约束（v6.49 新增，08-28 跑批实测）.

08-28 chapter_001 跑批实测 6 处血状态回退（镜头 1/2/6/7/8）：
  - 镜1 kf t=1.50 血 → kf t=2.90 无血
  - 镜2 kf t=2.20 有血 → 后续 kf 无血
  - 镜6/7/8 类似

物理逻辑：致命伤建立后，血只增不减（不消失）。LLM 中段 kf 把血丢了
→ 图片渲染时序错位（血忽有忽无）。

修法：扫单镜 kf 序列，按 t_sec 升序遍历，若 prev 含血词而 cur 不含
→ 视为回退，把 INJURY_ANCHOR_TAIL 追加到 cur.desc 末尾（幂等：
已含 INJURY_ANCHOR_WORDS 任一词即跳过）。

severity=WARN（不阻断 regen；与 kf_pre_post_conflict 同级）
fields=()（不扫 DEFAULT_FIELDS，扫 keyframes[N].desc）
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.kf_time import get_kf_t_sec
from step01_script_gen.prompt_semantic.registry import (
    INJURY_ANCHOR_WORDS,
    INJURY_ANCHOR_TAIL,
)
from .base import Constraint
from .types import RepairRecord, Severity


class KfBloodMonotonicConstraint(Constraint):
    """F2. kf 血状态单调：prev 有血 → cur 必须有血，否则追加锚词."""

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
            if not isinstance(kfs, list) or len(kfs) < 2:
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
            if len(indexed) < 2:
                continue
            indexed.sort(key=lambda x: x[0])

            # 维护一个 "blood_active" 状态：一旦在前序 kf 看到血词，
            # 所有后续 kf 必须保留血词（否则视为回退）
            blood_active = False
            for t, kf_j in indexed:
                kf = kfs[kf_j]
                if not isinstance(kf, dict):
                    continue
                desc = kf.get("desc", "") or ""
                if not isinstance(desc, str):
                    continue
                has_blood = any(w in desc for w in INJURY_ANCHOR_WORDS)
                if has_blood:
                    # 本帧含血 → blood_active 保持 True
                    blood_active = True
                    continue
                if blood_active:
                    # 前序有血，本帧丢了 → 血回退
                    # 幂等检查：本帧已含 INJURY_ANCHOR_TAIL 视为已修
                    if INJURY_ANCHOR_TAIL in desc:
                        continue
                    new_desc = desc.rstrip("。.，,;；") + "。" + INJURY_ANCHOR_TAIL
                    kf["desc"] = new_desc
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=shot.get("index") if not is_hook else None,
                            action="append",
                            target=f"keyframes[{kf_j}].desc",
                            before=desc[:80] + ("..." if len(desc) > 80 else ""),
                            after=new_desc[:80] + ("..." if len(new_desc) > 80 else ""),
                        )
                    )
        return repairs
