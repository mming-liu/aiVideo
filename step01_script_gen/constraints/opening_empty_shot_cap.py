"""F15. 开头空镜硬上限——前3镜中空镜>=2时合并多余空镜（节奏兜底）。

抖音短剧黄金3秒原则：前3镜必须有人物入场或冲突钩子。连续空镜堆叠=用户划走。
与 opening_empty_shot_insert（缺空镜时插一个）互补：
- 太少 → insert 补一个建立镜头
- 太多 → cap 合并多余的，保证节奏

severity=WARN: 节奏问题，不触发 regen，硬修即可。
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


# 与 opening_empty_shot_insert 同组词表（空镜关键词）
_EMPTY_HINTS = ("空镜", "环境", "远景", "水波", "树影")

# 核心事件词——含这些的镜头即使 main_subject 像空镜也不算
_EVENT_HINTS = (
    "刺杀", "打斗", "跃出", "破水", "冲入", "现身", "遇刺", "倒地",
    "对峙", "质问", "怒喝", "惊叫", "揭露", "反转", "逼近",
)

_WINDOW = 3  # 检查前 N 镜
_MAX_EMPTY = 1  # 前 N 镜最多允许 N 个空镜


def _is_empty_shot(shot):
    """判断是否为空镜/环境镜."""
    if not isinstance(shot, dict):
        return False
    ms = str(shot.get("main_subject") or "")
    prompt = str(shot.get("prompt") or "")
    ff = str(shot.get("ff_desc") or "")
    lf = str(shot.get("lf_desc") or "")
    all_text = ms + prompt + ff + lf

    # 有台词 → 不是空镜
    if str(shot.get("narration_segment") or "").strip():
        return False
    if str(shot.get("subtitle") or "").strip():
        return False

    # 含核心事件词 → 不是空镜
    if any(h in all_text for h in _EVENT_HINTS):
        return False

    # main_subject 含空镜关键词 → 算空镜
    if any(h in ms for h in _EMPTY_HINTS):
        return True
    if "环境空镜" in ff or "无人" in ff or "空无一人" in ff:
        return True

    return False


def _shot_info_score(shot):
    """信息丰富度评分（越高越值得保留）."""
    score = 0
    for f in ("prompt", "ff_desc", "lf_desc", "motion_desc"):
        v = str(shot.get(f) or "")
        score += len(v)
    kw = shot.get("core_keywords")
    if isinstance(kw, list):
        score += len(kw) * 10
    return score


class OpeningEmptyShotCapConstraint(Constraint):
    """F15. 开头空镜硬上限：前3镜空镜>1时合并多余的."""

    name = "opening_empty_shot_cap"
    severity = Severity.WARN
    skip_hook = True

    def apply(self, ctx):
        repairs = []
        shots = ctx.shots
        if not isinstance(shots, list) or len(shots) < _WINDOW:
            return repairs
        if not all(isinstance(s, dict) for s in shots[:_WINDOW]):
            return repairs

        window = shots[:_WINDOW]
        empty_indices = [i for i, s in enumerate(window) if _is_empty_shot(s)]

        if len(empty_indices) <= _MAX_EMPTY:
            return repairs

        # 保留信息最丰富的空镜，删除其余
        keep_idx = max(empty_indices, key=lambda i: _shot_info_score(window[i]))
        remove_indices = [i for i in empty_indices if i != keep_idx]

        if not remove_indices:
            return repairs

        keep_shot = window[keep_idx]
        keep_dur = float(keep_shot.get("duration_sec", 0) or 0)
        total_removed_dur = 0.0
        removed_kw = []

        for ri in sorted(remove_indices, reverse=True):
            removed = window[ri]
            dur = float(removed.get("duration_sec", 0) or 0)
            total_removed_dur += dur
            ms = str(removed.get("main_subject") or "")
            if "\u00b7" in ms:
                kw_part = ms.split("\u00b7", 1)[1]
                for k in kw_part.replace("/", "\u3001").split("\u3001"):
                    k = k.strip()
                    if k and k not in removed_kw:
                        removed_kw.append(k)
            del shots[ri]

        # 重新编号
        for pos, shot in enumerate(shots, 1):
            if isinstance(shot, dict):
                shot["index"] = pos

        new_dur = round(keep_dur + total_removed_dur, 1)
        keep_shot["duration_sec"] = new_dur
        keep_shot["duration"] = new_dur

        # 合并 main_subject 关键词
        if removed_kw:
            keep_ms = str(keep_shot.get("main_subject") or "空镜")
            if "\u00b7" in keep_ms:
                base, kw_part = keep_ms.split("\u00b7", 1)
                existing = [k.strip() for k in kw_part.replace("/", "\u3001").split("\u3001") if k.strip()]
                for k in removed_kw:
                    if k not in existing:
                        existing.append(k)
                keep_shot["main_subject"] = base + "\u00b7" + "\u3001".join(existing)
            else:
                keep_shot["main_subject"] = keep_ms + "\u00b7" + "\u3001".join(removed_kw)

        repairs.append(
            RepairRecord(
                constraint_name=self.name,
                shot_index=keep_shot.get("index", 1),
                action="merge_empty_shots",
                target="shots[0:%d]" % _WINDOW,
                before="前%d镜%d个空镜" % (_WINDOW, len(empty_indices)),
                after="合并为1个空镜，时长%.1fs→%.1fs" % (keep_dur, new_dur),
            )
        )
        return repairs