"""镜头 index 顺序重编号：消除**模型自造**的半整数 index（v6.84，阶段 1）.

## 背景

拆镜 pre-pass（`_do_pre_split_oversized_shots`）已于 v6.79 移出生产路径，它当年用
`index + 0.5 / +0.25` 表示拆出的子镜。但 **2026-09-21 重跑实测**：模型自己会写
`9.5` / `10.5` 这种 index（`_split_from` 为空，不是我们产的），下游据此按「拆镜
子镜」语义处理它们：

- `duplicate_shot_merge` 跳过含半整数的相邻对（不做重复检测）；
- `split_continuity` 对它们做 motion 重定基；
- `quality_issues._split_boundary_issues` 只对它们做「重放前镜已完成动作」检测。

结果是一批**本该按普通镜头处理**的镜头走上了特殊路径：`split_boundary_replay` /
`multi_person_staging` / `kf_time_not_strict` 三条 blocking 全落在那两个镜上。

## 契约

按**列表顺序**把正片镜头重编号为 `1..N` 连续整数（钩子保持自己的 index=0）。
非单调的输入先按 index 排序——重排后列表顺序才是叙事顺序，下游一律按列表顺序
消费。同时清掉遗留的 `_split_from` / `_split_to` 标记：重编号之后它们不再指向
任何真实关系。

**只改 index，不动任何其它字段**，幂等。

## 位置：第 1 段（`PRE_TEMPORAL_NORMALIZE`）第一位

所有下游（含 `opening_empty_shot_insert` 之类的结构约束）都按 index 组织，
让它们一开始就见到干净的连续整数。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class ShotIndexRenumberConstraint(Constraint):
    """正片 index → 1..N 连续整数（幂等）；钩子不参与."""

    name = "shot_index_renumber"
    severity = Severity.WARN
    fields = ()

    def apply(self, ctx) -> list[RepairRecord]:
        # v6.94（2026-09-22 实测）：**修复轮内不重编号**。
        #
        # 本约束是 **finalize 阶段**的归一化（把模型自造的半整数 index 拉回 1..N）。
        # 在修复轮里跑它会让 index 在轮内平移，而两处「按 index 标识镜头」的地方
        # 因此失配：
        #   · `_snapshot_untargeted` / `_restore_untargeted`（按 `shot_{index}` 为键）
        #     → 还原到**错的镜头** → 未点名镜头看起来被改过 → 整轮**假拒绝**；
        #   · 本轮点名的 `picked` 与 audit issue 的 `shot_id` → 找不到目标。
        # 实测形态：`非目标镜头被改动 9 处: [13, 13.333, 14, 14.25, 14.5]`
        # ——命中的正是结构收敛轮拆出的子镜。
        # 这与 `shared/semantic_barrier` 在 v6.84 踩过的坑同源。
        if getattr(ctx, "is_in_repair_loop", False):
            return []

        repairs: list[RepairRecord] = []
        shots = [s for s in (ctx.script.get("shots") or []) if isinstance(s, dict)]
        if not shots:
            return repairs

        # 非单调输入先按 index 排序：重编号后「列表顺序 = 叙事顺序」这条
        # 隐含契约必须成立，否则重编号会把镜头顺序改错。
        keys = [_as_float(s.get("index")) for s in shots]
        if any(k is None for k in keys) or keys != sorted(keys):
            shots.sort(key=lambda s: (_as_float(s.get("index")) is None,
                                      _as_float(s.get("index")) or 0.0))
            if ctx.script.get("shots") is not shots:
                ctx.script["shots"] = shots
            repairs.append(RepairRecord(
                constraint_name=self.name,
                shot_index=None,
                action="rewrite",
                target="shots",
                before=str(keys),
                after=str([s.get("index") for s in shots]),
                detail="index 非单调：先按 index 排序，保证列表顺序 = 叙事顺序",
            ))

        for pos, shot in enumerate(shots, 1):
            old = shot.get("index")
            dropped = [k for k in ("_split_from", "_split_to") if k in shot]
            for k in dropped:
                shot.pop(k, None)
            if _as_float(old) == float(pos) and not dropped:
                continue
            shot["index"] = pos
            repairs.append(RepairRecord(
                constraint_name=self.name,
                shot_index=pos,
                action="rewrite",
                target="index",
                before=str(old),
                after=str(pos),
                detail=(
                    "顺序重编号为连续整数（消除模型自造的半整数 index"
                    + (f"；顺带清掉遗留标记 {dropped}" if dropped else "")
                    + "）"
                ),
            ))
        return repairs
