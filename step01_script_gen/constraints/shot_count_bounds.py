"""shot_count_bounds — 镜头数 + 场景数范围通用兜底（v6.62.2 加严）.

通用化（v6.62.2）：从 .env 读 SHOT_COUNT_MIN/MAX/SCENE_ASSETS_MIN/MAX
（已在 config.py 注册）；不在范围即 warning。**完全通用**——不绑小说角色。
"""
from __future__ import annotations

import os

from .base import Constraint
from .types import RepairRecord, Severity


def _get_int_env(name: str, default: int) -> int:
    """读 env，0/空/无效都退回 default（与 .env.content "0=禁用" 约定一致）."""
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip() or str(raw).strip() == "0":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


class ShotCountBoundsConstraint(Constraint):
    """镜头数 + 场景数范围校验（warning-only）.

    默认：镜头数 ∈ [10, 18]（比 v4.1 软护栏 [8, 20] 收紧，60-90s 红果标准更稳）；
          场景数 ∈ [1, 5]（v6.38 红果 60-90s 单集 ≤ 3 场景但放宽到 5 兜住多线剧情）。
    可被 .env 覆盖：SHOT_COUNT_MIN / SHOT_COUNT_MAX / SCENE_ASSETS_MIN / SCENE_ASSETS_MAX。
    """

    name = "shot_count_bounds"
    severity = Severity.WARN
    fields = ("shots", "scenes")
    skip_hook = True

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots_min = _get_int_env("SHOT_COUNT_MIN", 10)
        shots_max = _get_int_env("SHOT_COUNT_MAX", 18)
        scenes_min = _get_int_env("SCENE_ASSETS_MIN", 1)
        scenes_max = _get_int_env("SCENE_ASSETS_MAX", 5)

        shots = ctx.shots
        if not isinstance(shots, list) or len(shots) == 0:
            return repairs  # 空脚本/edge case 不报（与 dialogue_density 一致）
        n_shots = len(shots)
        if n_shots < shots_min:
            repairs.append(RepairRecord(
                constraint_name=self.name,
                shot_index=None,
                action="warn",
                target="shots",
                detail=f"总镜头数 {n_shots} < 软下限 {shots_min}（剧情密度可能不足）。",
            ))
        elif n_shots > shots_max:
            repairs.append(RepairRecord(
                constraint_name=self.name,
                shot_index=None,
                action="warn",
                target="shots",
                detail=f"总镜头数 {n_shots} > 软上限 {shots_max}（单章 ≥ 90s，长度风险）。",
            ))

        script = getattr(ctx, "script", None) or {}
        # v6.62.3（09-08）修读值 bug：旧实现读 script["scenes"] —— 该键**全仓无写入方**，
        # 恒为 []，于是任何脚本都报「场景数 0 < 下限 1」（09-08 实测 scene_assets 实有
        # 8 条却报 0）。真实容器是 script["scene_assets"]（dict，键=identifier；
        # 由 _extract_assets_and_update_script 写入），保留 "scenes" 作前瞻兼容。
        # 通用性：只认容器长度，不认任何具体场景名。
        _container = None
        for _key in ("scene_assets", "scenes"):
            if _key in script:
                _container = script.get(_key)
                break
        if _container is None:
            # 资产提取尚未执行（修复轮内 hard_fix 早于 _extract_assets）→ 不报，
            # 否则每轮都刷一条假告警。落盘前 final_pass 时该键必已存在。
            return repairs
        if isinstance(_container, dict):
            n_scenes = len(_container)
        elif isinstance(_container, list):
            n_scenes = len(_container)
        else:
            return repairs
        if n_scenes < scenes_min:
            repairs.append(RepairRecord(
                constraint_name=self.name,
                shot_index=None,
                action="warn",
                target="scenes",
                detail=f"场景数 {n_scenes} < 下限 {scenes_min}（场景提取可能失败；参考 v6.38 红果标准 ≤ 3 场景）。",
            ))
        elif n_scenes > scenes_max:
            repairs.append(RepairRecord(
                constraint_name=self.name,
                shot_index=None,
                action="warn",
                target="scenes",
                detail=f"场景数 {n_scenes} > 上限 {scenes_max}（场景过多=频繁转场，单章 60-90s 不易把控）。",
            ))

        return repairs
