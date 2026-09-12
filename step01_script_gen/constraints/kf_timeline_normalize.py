"""kf 时间线归一（2026-09-12，shot 95 分稿质量复盘根治项）.

背景（95 分终稿复盘实锤）：kf 由多个生产者写入（模型直出 / 弱模板回填 /
时段对齐派生 / frame_layouts 派生），没有任何单一执行点保证时间线不变量，
导致四类结构缺陷漏进终稿：
  1. 同刻堆叠——镜2 三个 kf 全在 t_sec=2.5（无信息量的重复帧）
  2. 时间倒退——镜9 kf@3.004 内容是起跳前状态（晚于 @2.52 的腾空态）；
     镜12 kf@3.933/4.892 回到开头态
  3. 首尾帧标签错位——镜12.5 首帧位置被填「本帧画面即尾帧静态状态」文本
  4. hold 帧抄错内容——「（画面延续，保持该状态）」帧的底文抄了 ff_desc
     而非时间序上前一帧（上游 hold 填充在分段错位时选错 _last_text）

本约束是这些**不变量**的单一执行点（对齐「新缺陷 = 加 1 个 Constraint」惯例）：
任何生产者写坏，这里统一归一。修复动作：
  - t_sec 越界（>duration）→ 钳到 duration×0.95
  - 相邻 t_sec 差 ≤0.15s 的同刻簇 → 在前后邻刻之间均匀重排
  - 「本帧画面即首帧静态状态」仅允许出现在第 0 帧；「…尾帧静态状态」仅允许
    出现在最后一帧——错位则剥掉标签保留内容
  - hold 帧（含「画面延续，保持该状态」）底文与 ff_desc 高度相似且存在更早的
    差异 kf → 底文改写为前一 kf 文本（延续语义归真）

幂等：归一后再跑不再产生修改。
"""
from __future__ import annotations

import sys
from difflib import SequenceMatcher
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity

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
    """kf 时间线不变量单一执行点：同刻重排/越界钳制/标签错位/hold 帧回归."""

    name = "kf_timeline_normalize"
    severity = Severity.HARD
    fields = ("keyframes",)

    def apply(self, ctx) -> list[RepairRecord]:
        from shared.kf_time import get_kf_t_sec, set_kf_t_sec

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

            # ---- 1. 越界钳制 ----
            for i, t in enumerate(t_secs):
                if t is not None and t > dur:
                    new_t = round(dur * 0.95, 3)
                    set_kf_t_sec(kfs[i], new_t)
                    t_secs[i] = new_t
                    repairs.append(RepairRecord(
                        constraint_name=self.name, shot_index=shot_idx,
                        action="set_field",
                        target=f"keyframes[{i}].t_sec",
                        before=f"{t:.3f}", after=f"{new_t:.3f}",
                    ))

            # ---- 2. 同刻簇重排（相邻差 ≤eps 视为堆叠）----
            ordered = sorted(range(len(kfs)), key=lambda i: (t_secs[i] if t_secs[i] is not None else 0))
            i = 0
            while i < len(ordered) - 1:
                j = i
                while j + 1 < len(ordered) and abs(
                        (t_secs[ordered[j + 1]] or 0) - (t_secs[ordered[j]] or 0)) <= _DUP_T_EPS:
                    j += 1
                if j > i:
                    lo = t_secs[ordered[i]] or 0.0
                    hi_candidates = [t_secs[k] for k in ordered
                                     if (t_secs[k] or 0) > lo + _DUP_T_EPS]
                    hi = min(hi_candidates) if hi_candidates else min(dur * 0.95, lo + 0.8 * (j - i + 1))
                    span = max(hi - lo, 0.1 * (j - i + 1))
                    for offset, k_idx in enumerate(ordered[i:j + 1]):
                        new_t = round(lo + span * offset / (j - i), 3)
                        if abs(new_t - (t_secs[k_idx] or 0)) > 0.01:
                            set_kf_t_sec(kfs[k_idx], new_t)
                            t_secs[k_idx] = new_t
                            repairs.append(RepairRecord(
                                constraint_name=self.name, shot_index=shot_idx,
                                action="set_field",
                                target=f"keyframes[{k_idx}].t_sec",
                                before="同刻堆叠", after=f"{new_t:.3f}",
                            ))
                i = j + 1

            # ---- 3. 首尾帧标签错位 ----
            for i, kf in enumerate(kfs):
                desc = kf.get("desc") or ""
                if not isinstance(desc, str) or not desc:
                    continue
                fixed = desc
                if i != 0 and _FIRST_FRAME_LABEL in fixed:
                    fixed = _strip_frame_label(fixed) if _LAST_FRAME_LABEL not in fixed else fixed
                if i != len(kfs) - 1 and _LAST_FRAME_LABEL in fixed:
                    fixed = fixed.replace(_LAST_FRAME_LABEL + "：", "").replace(_LAST_FRAME_LABEL, "")
                    fixed = fixed.replace("（）", "").strip("，。； ")
                if fixed != desc:
                    kf["desc"] = fixed
                    repairs.append(RepairRecord(
                        constraint_name=self.name, shot_index=shot_idx,
                        action="rewrite",
                        target=f"keyframes[{i}].desc",
                        before=desc[:50], after=fixed[:50],
                    ))

            # ---- 4. hold 帧内容回归（底文抄 ff → 改抄前一 kf）----
            ff_text = (shot.get("ff_desc") or "").strip()
            for i in range(1, len(kfs)):
                kf = kfs[i]
                desc = kf.get("desc") or ""
                if not isinstance(desc, str) or _HOLD_SUFFIX not in desc:
                    continue
                base = _strip_hold_suffix(desc)
                prev_base = _strip_hold_suffix(kfs[i - 1].get("desc") or "")
                if not base or not prev_base or base == prev_base:
                    continue
                # 底文与 ff 高度相似、且与前一 kf 差异明显 → 内容时间回归
                if _similar(base, ff_text) >= 0.55 and _similar(base, prev_base) < 0.55:
                    new_desc = f"{prev_base}{_HOLD_SUFFIX}"
                    kf["desc"] = new_desc
                    repairs.append(RepairRecord(
                        constraint_name=self.name, shot_index=shot_idx,
                        action="rewrite",
                        target=f"keyframes[{i}].desc",
                        before=desc[:50], after=new_desc[:50],
                    ))

        return repairs
