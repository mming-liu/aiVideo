"""F. kf t_sec 越界约束（v6.42 迁移自 _hard_fix_kf_t_sec_bounds）.

t > dur × 0.9 → clamp 到 0.9×dur
t < 0.1 且 dur > 0.2 → 拉到 0.1s
幂等：clamp 后值与原值相同则跳过.

08-27（P1-1）：LLM 常把一镜内所有 kf 写同一个 t_sec（单次跑越界值 54+43 处），
clamp 后 5 个 stage（pre/approach/climax/react/after）全塌缩到同一时刻——stage
时间轴丢失。全镜同值且 stage 多样时，按 stage 顺序把 kf 摊到 [0.1, 0.9]×dur。
"""
from __future__ import annotations

from shared.kf_time import get_kf_t_sec, set_kf_t_sec, stage_to_index
from .base import Constraint
from .types import RepairRecord, Severity

# 全镜同值判定阈值（秒）：kf t_sec 两两差 < ε 视为「塌缩」（LLM 写死同一个时间）
_COLLAPSE_EPS = 0.01

# v6.47（08-27）：stage → 语义时间位置（比例 × duration_sec）。
# LLM 常写 17.97/31.03/44.10 这类远超单镜时长的 t_sec（系统性越界 131 处/run），
# 一刀切 clamp 到 0.9×dur 会丢 stage 时间轴。越界时改按 stage 推导位置。
_STAGE_FRACTIONS = {
    "pre": 0.10, "approach": 0.26, "climax": 0.42,
    "react": 0.58, "exit": 0.74, "after": 0.90,
}


class KfTSecBoundsConstraint(Constraint):
    """F. kf t_sec 越界 clamp 到 dur × 0.9 + 全镜同值塌缩按 stage 摊开."""

    name = "kf_t_sec_bounds"
    severity = Severity.WARN
    fields = ()

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            try:
                dur = float(shot.get("duration_sec") or shot.get("duration") or 0)
            except (TypeError, ValueError):
                dur = 0
            if dur <= 0:
                continue
            kfs = shot.get("keyframes") or []
            if not isinstance(kfs, list):
                continue
            for j, kf in enumerate(kfs):
                if not isinstance(kf, dict):
                    continue
                t = get_kf_t_sec(kf, shot)
                if t is None:
                    continue
                new_t = t
                oob = new_t > dur * 0.9 or (new_t < 0.1 and dur > 0.2)
                if oob:
                    # v6.47：越界 = LLM 写的 t_sec 不可信（常见 44.10s/7s 镜），
                    # 按 stage 语义位置推导（pre=0.1×dur … after=0.9×dur），
                    # 替代一刀切 clamp 到 0.9×dur（会丢 stage 时间轴，需靠摊开兜底）。
                    frac = _STAGE_FRACTIONS.get(str(kf.get("stage") or "").strip().lower())
                    if frac is not None:
                        new_t = dur * frac
                    elif new_t > dur * 0.9:
                        new_t = dur * 0.9
                    elif new_t < 0.1 and dur > 0.2:
                        new_t = 0.1
                if abs(new_t - t) > 0.001:
                    set_kf_t_sec(kf, new_t)
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx,
                            action="derive" if oob else "clamp",
                            target=f"keyframes[{j}].t_sec",
                            before=f"{t:.3f}",
                            after=f"{new_t:.3f}",
                        )
                    )
            # 08-27：全镜同值塌缩 → 按 stage 顺序摊到 [0.1, 0.9]×dur
            self._distribute_collapsed(kfs, shot, dur, idx, repairs)
        return repairs

    @staticmethod
    def _distribute_collapsed(
        kfs: list, shot: dict, dur: float, idx: int,
        repairs: list[RepairRecord],
    ) -> None:
        """全镜 kf 共享同一 t_sec 时，按 stage 顺序摊开（幂等：摊开后值已不同）. """
        ts = [get_kf_t_sec(kf, shot) for kf in kfs if isinstance(kf, dict)]
        if len(ts) < 2 or any(t is None for t in ts):
            return
        if not all(abs(t - ts[0]) < _COLLAPSE_EPS for t in ts):
            return  # 时刻已分散，无需摊开
        stages = {kf.get("stage", "") for kf in kfs if isinstance(kf, dict)}
        if len(stages) < 2:
            return  # 同 stage 重复/未知 → 不摊开
        # 按 stage 顺序（pre=0 … after=5；未知排末尾）稳定排序
        items = [
            (stage_to_index(kf.get("stage", "")), j, kf)
            for j, kf in enumerate(kfs) if isinstance(kf, dict)
        ]
        items.sort(key=lambda t: (t[0], t[1]))
        n = len(items)
        for rank, (_, j, kf) in enumerate(items):
            frac = 0.1 + 0.8 * (rank / (n - 1))  # [0.1, 0.9] 等距
            new_t = round(dur * frac, 3)
            cur = get_kf_t_sec(kf, shot)
            if abs(new_t - cur) > 0.001:
                set_kf_t_sec(kf, new_t)
                repairs.append(
                    RepairRecord(
                        constraint_name=KfTSecBoundsConstraint.name,
                        shot_index=idx,
                        action="distribute",
                        target=f"keyframes[{j}].t_sec",
                        before=f"{cur:.3f}",
                        after=f"{new_t:.3f}",
                    )
                )
