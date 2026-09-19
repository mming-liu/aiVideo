"""kf 时间线归一（2026-09-12 引入；2026-09-19 v6.76 降为只报告）.

背景（95 分终稿复盘实锤）：kf 由多个生产者写入（模型直出 / 弱模板回填 /
时段对齐派生 / frame_layouts 派生），没有任何单一执行点保证时间线不变量，
导致四类结构缺陷漏进终稿：
  1. 同刻堆叠——镜2 三个 kf 全在 t_sec=2.5（无信息量的重复帧）
  2. 时间倒退——镜9 kf@3.004 内容是起跳前状态（晚于 @2.52 的腾空态）；
     镜12 kf@3.933/4.892 回到开头态
  3. 首尾帧标签错位——镜12.5 首帧位置被填「本帧画面即尾帧静态状态」文本
  4. hold 帧抄错内容——「（画面延续，保持该状态）」帧的底文抄了 ff_desc
     而非时间序上前一帧

引入时的定位是「这些不变量的**单一执行点**」——任何生产者写坏，这里统一归一。

v6.76（2026-09-19）**降为只报告**，不再归一并写回。原因：
  1. 「单一执行点」的定位不成立：本约束自身的修复会被其后注册的
     _KfTimeWindowAlignmentFinal（v6.76 已删除） 再次改写（它 append 新帧且不重排），
     所以归一是**不保持**的——实测同一脚本连跑两次 run_hard_fix 结果不同
     （tests/.../test_full_hardfix_kf_idempotency.py 当前失败）。
  2. 四类缺陷的根因都在上游（拆镜按字符比例切、补帧 append 到末尾、
     多层描述写入者互相撤销），在链中段做归一只是把症状挪走，
     不改变「谁有权决定 kf 的时刻与内容」这一根本问题。
  3. 时刻与内容归属只能由事件结构决定，见
     docs/step01-temporal-writers-audit-2026-09-19.md。

现状：四类缺陷全部**只检测 + 上报**（Violation(repaired=False) +
RepairRecord(action="warn")），保留原值交 audit / regen。

保留导出（勿删，被外部引用）：
  - `_strip_frame_label`：`emotion_visualization_repair` 依赖
  - `_FIRST_FRAME_LABEL` / `_LAST_FRAME_LABEL` / `_HOLD_SUFFIX` / `_DUP_T_EPS`
"""
from __future__ import annotations

import sys
from difflib import SequenceMatcher
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity, Violation

# 首尾帧静态标签（weak-template W16 锚定语，错位时剥标签留内容）
_FIRST_FRAME_LABEL = "本帧画面即首帧静态状态"
_LAST_FRAME_LABEL = "本帧画面即尾帧静态状态"
_HOLD_SUFFIX = "（画面延续，保持该状态）"
# 同刻判定阈值（秒）：差 ≤0.15s 视为同一时刻的堆叠
_DUP_T_EPS = 0.15


def _strip_hold_suffix(text: str) -> str:
    return text.replace(_HOLD_SUFFIX, "").strip("，。； ")


def _strip_frame_label(text: str) -> str:
    """剥掉「（本帧画面即首帧/尾帧静态状态：…）」的外壳标签，保留内容."""
    out = text
    for label in (_FIRST_FRAME_LABEL, _LAST_FRAME_LABEL):
        if label in out:
            out = out.replace(label + "：", "").replace(label, "")
            out = out.replace("（）", "").replace("（本帧各角色姿态位置见首帧静态定格）", "")
    return out.strip("，。； ")


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


class KfTimelineNormalizeConstraint(Constraint):
    """kf 时间线不变量：同刻堆叠/越界/标签错位/hold 帧回归 —— 只检测并上报（v6.76 起）."""

    name = "kf_timeline_normalize"
    severity = Severity.HARD
    fields = ("keyframes",)

    def _report(
        self, ctx, repairs: list[RepairRecord], shot_idx, kind: str,
        *, target: str, message: str, evidence: str, detail: str,
    ) -> None:
        """统一「只报告」出口。severity 用类声明级（HARD）——这些是结构性缺陷，
        本地修不了，属 regen 材料；REGEN_AUTO_TRIGGER 默认关闭，不产生 LLM 调用。"""
        ctx.record_violation(Violation(
            constraint_name=self.name,
            severity=self.severity,
            shot_index=shot_idx,
            field=target,
            message=message,
            evidence=evidence[:200],
            repaired=False,
        ))
        repairs.append(RepairRecord(
            constraint_name=self.name,
            shot_index=shot_idx,
            action="warn",
            target=target,
            before=evidence[:80],
            after=evidence[:80],
            detail=detail,
        ))

    def apply(self, ctx) -> list[RepairRecord]:
        from shared.kf_time import get_kf_t_sec

        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            kfs = shot.get("keyframes") or []
            if not isinstance(kfs, list) or len(kfs) < 2:
                continue
            kfs = [k for k in kfs if isinstance(k, dict)]
            try:
                dur = float(shot.get("duration_sec") or 0)
            except (TypeError, ValueError):
                dur = 0.0
            if dur <= 0:
                continue

            shot_idx = shot.get("index") if not is_hook else None
            t_secs = [get_kf_t_sec(k, shot) for k in kfs]

            # ---- 1. 越界 ----
            for i, t in enumerate(t_secs):
                if t is not None and t > dur:
                    self._report(
                        ctx, repairs, shot_idx, "oob",
                        target=f"keyframes[{i}].t_sec",
                        message=(
                            f"关键帧时刻 {t:.3f}s 超出镜头时长 {dur:.3f}s；"
                            "保留原值未钳制——越界说明该镜时间轴不可信，交 regen 重写。"
                        ),
                        evidence=f"t_sec={t:.3f} > duration_sec={dur:.3f}",
                        detail="关键帧时刻越界，保留原值交由 audit/regen",
                    )

            # ---- 2. 同刻堆叠（相邻差 ≤eps）----
            # 只检测不重排：原实现按 (lo, hi) 均匀铺开，位置无原文依据，
            # 且铺开后会被后续 _KfTimeWindowAlignmentFinal（v6.76 已删除） 再次打乱。
            ordered = sorted(
                range(len(kfs)),
                key=lambda i: (t_secs[i] if t_secs[i] is not None else 0),
            )
            i = 0
            while i < len(ordered) - 1:
                j = i
                while j + 1 < len(ordered) and abs(
                        (t_secs[ordered[j + 1]] or 0) - (t_secs[ordered[j]] or 0)) <= _DUP_T_EPS:
                    j += 1
                if j > i:
                    cluster = ordered[i:j + 1]
                    _txt = ", ".join(
                        f"kf[{k}]={t_secs[k]:.3f}s" if t_secs[k] is not None
                        else f"kf[{k}]=<none>" for k in cluster
                    )
                    self._report(
                        ctx, repairs, shot_idx, "dup_cluster",
                        target="keyframes[*].t_sec",
                        message=(
                            f"{len(cluster)} 个关键帧时刻堆叠在同一时刻附近（{_txt}，"
                            f"阈值 {_DUP_T_EPS}s）；保留原值未重排——铺开位置无原文依据，"
                            "交 regen 重写该镜时间结构。"
                        ),
                        evidence=_txt,
                        detail="同刻堆叠，保留原值交由 audit/regen",
                    )
                i = j + 1

            # ---- 3. 首尾帧标签错位 ----
            for i, kf in enumerate(kfs):
                desc = kf.get("desc") or ""
                if not isinstance(desc, str) or not desc:
                    continue
                bad = (i != 0 and _FIRST_FRAME_LABEL in desc) or \
                      (i != len(kfs) - 1 and _LAST_FRAME_LABEL in desc)
                if bad:
                    self._report(
                        ctx, repairs, shot_idx, "label_misposition",
                        target=f"keyframes[{i}].desc",
                        message=(
                            f"首尾帧静态标签错位：第 {i} 帧（共 {len(kfs)} 帧）"
                            "出现了只应属于首/尾帧的标签文本；保留原文未剥标签，"
                            "交 regen 明确该帧的真实状态。"
                        ),
                        evidence=desc,
                        detail="首尾帧标签错位，保留原文交由 audit/regen",
                    )

            # ---- 4. hold 帧内容回归（底文抄 ff）----
            ff_text = (shot.get("ff_desc") or "").strip()
            for i in range(1, len(kfs)):
                desc = kfs[i].get("desc") or ""
                if not isinstance(desc, str) or _HOLD_SUFFIX not in desc:
                    continue
                base = _strip_hold_suffix(desc)
                prev_base = _strip_hold_suffix(kfs[i - 1].get("desc") or "")
                if not base or not prev_base or base == prev_base:
                    continue
                if _similar(base, ff_text) >= 0.55 and _similar(base, prev_base) < 0.55:
                    self._report(
                        ctx, repairs, shot_idx, "hold_regression",
                        target=f"keyframes[{i}].desc",
                        message=(
                            "hold 帧底文与首帧高度相似而与时间序上前一帧差异明显"
                            "（内容时间回归）；保留原文未改写，交 regen 确认延续状态。"
                        ),
                        evidence=desc,
                        detail="hold 帧内容时间回归，保留原文交由 audit/regen",
                    )

        return repairs
