"""D. kf 时序矛盾约束（v6.42 迁移自 _hard_fix_kf_pre_post_conflict）.

同一 kf frame 同时含 pre-state（"刺客半身探出举刀"）和 post-state
（"杨旭仰卧+心口洇痕"）矛盾 → 删 pre 词保留 post，t=0.5；
纯 pre 但 t>0.33×dur → 压到 0.2×dur；
纯 post 但 t<0.67×dur → 拉到 0.8×dur.

幂等：clamp 后 t 与原值相同则跳过；删 pre 词前 desc 含"身侧"等豁免词.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.kf_time import get_kf_t_sec, set_kf_t_sec
from step01_script_gen.validation import _KF_POST_KW, _KF_PRE_KW
from .base import Constraint
from .types import RepairRecord, Severity


_LAYING_WORDS = ("仰卧", "后仰", "倒地", "倒下", "坠落", "跌落")
_BLOOD_WORDS = ("洇痕", "暗红", "血")


class KfPrePostConflictConstraint(Constraint):
    """D. kf 帧内 pre+post 矛盾：删 pre 词 + t=0.5; 纯 pre/post 时间错位 clamp.

    severity=WARN: kf 时序可由后续 repair round 修.
    幂等通过「clamp 后值与原值相同则跳过」保证.
    """

    name = "kf_temporal_consistency"
    severity = Severity.WARN
    fields = ()  # 不扫文本字段，处理的是 keyframes 列表

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

            for kf in kfs:
                if not isinstance(kf, dict):
                    continue
                t = get_kf_t_sec(kf, shot)
                if t is None or t <= 0 or t >= dur:
                    continue
                desc = kf.get("desc") or ""
                # v6.43（08-23 审计）：【角色锚定】块内的「正对镜头/仰卧」等词
                # 会令 is_pre/has_laying 恒真（自触发），删除正则还会跨标点
                # 吞掉锚定文本（实测「威胁源必须面向其攻，」残缺）。检测与删除
                # 只在锚定之前的正文进行，锚定原样保留。
                anchor = ""
                body = desc
                if "【角色锚定】" in desc:
                    body, _anchor_tail = desc.split("【角色锚定】", 1)
                    anchor = "【角色锚定】" + _anchor_tail
                is_pre = any(kw in body for kw in _KF_PRE_KW)
                is_post = any(kw in body for kw in _KF_POST_KW)
                if is_pre and is_post:
                    continue
                has_laying = any(w in body for w in _LAYING_WORDS)
                has_blood = any(w in body for w in _BLOOD_WORDS)

                # 真实矛盾：pre + 角色已"躺血" → 删 pre 词 + t=0.5
                if is_pre and has_laying and has_blood:
                    original = desc
                    for kw in _KF_PRE_KW:
                        # [^，。；] 限定在单个逗号段内，禁止跨标点吞句
                        pat = re.compile(
                            r"[,，]?\s*[^，。；]*?"
                            + re.escape(kw)
                            + r"[^，。；]*?(?=[，。；]|$)"
                        )
                        body = pat.sub("", body)
                    for kw in _KF_PRE_KW:
                        body = re.sub(r"[,，]?\s*" + re.escape(kw), "", body)
                    body = re.sub(r"[,，]\s*[,，]", "，", body).strip("，。； ")
                    desc = body + anchor
                    if desc != original:
                        kf["desc"] = desc
                        set_kf_t_sec(kf, dur * 0.5)
                        repairs.append(
                            RepairRecord(
                                constraint_name=self.name,
                                shot_index=idx,
                                action="rewrite",
                                target=f"keyframes[{_kf_index(kfs, kf)}].desc",
                                before=original,
                                after=desc,
                            )
                        )
                    continue
                # 纯 pre 但 t>0.33×dur → 压到 0.2×dur
                if is_pre and t > dur * 0.33:
                    before_t = t
                    set_kf_t_sec(kf, dur * 0.2)
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx,
                            action="clamp",
                            target=f"keyframes[{_kf_index(kfs, kf)}].t_sec",
                            before=f"{before_t:.3f}",
                            after=f"{dur * 0.2:.3f}",
                        )
                    )
                # 纯 post 但 t<0.67×dur → 拉到 0.8×dur
                elif is_post and t < dur * 0.67:
                    before_t = t
                    set_kf_t_sec(kf, dur * 0.8)
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx,
                            action="clamp",
                            target=f"keyframes[{_kf_index(kfs, kf)}].t_sec",
                            before=f"{before_t:.3f}",
                            after=f"{dur * 0.8:.3f}",
                        )
                    )
        return repairs


def _kf_index(kfs: list, kf: dict) -> int:
    """返回 kf 在 kfs 列表中的索引（用于 RepairRecord.target 路径）."""
    try:
        return kfs.index(kf)
    except ValueError:
        return -1
