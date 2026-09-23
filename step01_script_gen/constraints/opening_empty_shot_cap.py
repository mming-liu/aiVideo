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


# v6.101：空镜谓词**只有一份实现**（在 `quality_issues` 里，结构收敛轮也用它）。
# 本模块此前自带 `_EMPTY_HINTS` / `_EVENT_HINTS` / `_is_empty_shot` 三件套——
# 与收敛轮各自的启发式一旦漂移，就会出现「约束说超上限、收敛轮看不见」这类
# 两边不一致。现在统一从 `quality_issues` 取（沿用本仓既有方向：约束 → quality_issues，
# 且**函数内导入**避免模块级环）。
# 窗口/上限与 `quality_issues._OPENING_EMPTY_WINDOW/_MAX` **同值**：检测源统一，
# 但本约束自己的判定阈值仍在这里声明（约束的严重度与报告措辞也归它）。
_WINDOW = 3      # 检查前 N 镜
_MAX_EMPTY = 1   # 前 N 镜最多允许 1 个空镜


def _is_empty_shot(shot) -> bool:
    from step01_script_gen.quality_issues import is_opening_empty_shot
    return is_opening_empty_shot(shot)


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
    """F15. 开头空镜**超上限报告**：前3镜空镜>1时报告（v6.99 起 report-only）.

    2026-09-23 二次审核 P0-B + 用户决定：**不再删除/合并**——语义删除无法证明等价，
    且删镜头会让按位置比较的时间快照把后续镜头全判成「时间字段被写」。
    """

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

        # v6.99（2026-09-23 二次审核 P0-B，用户决定**选项 B**）：**只报告，不改写**。
        #
        # 原实现按窄文本启发式把多余的开场空镜 `del shots[ri]`，再把时长相加、
        # 关键词并入保留镜。审核指出这是**无法证明等价的语义删除**：被删镜的
        # prompt / ff / lf / motion / KF / 布局 / 姿态 / 道具 / 事件结果随 `del`
        # 一起消失，而「并入 main_subject 关键词 + 时长相加」不能证明这些信息
        # 仍然存在。v6.98 曾试图把它登记为「受豁免的结构写入者」保留行为——
        # 二次审核证明那条路**同时**踩了两个坑：
        #   1. 只豁免了结构检查，没豁免时间屏障——删首镜会让后续镜头按列表位置
        #      前移，而时间快照正是按位置比较的 → 同一动作既得 advisory 又得
        #      `time_field_audit_write` HARD（实测：删 1 镜 → 20 条未授权时间写入）；
        #   2. 即使豁免时间检查，被删镜的语义仍无等价证明。
        #
        # 所以改为 report-only：**不删除、不合并、不重编号、不改时长**，
        # 只把「命中的镜头 + 判空证据 + 建议保留顺序」报出来，交结构 regen /
        # 人工处理。这条约束自此不再写 `shots`，阶段契约「审计段只读」对它成立。
        hits = []
        for ri in sorted(remove_indices):
            sh = window[ri]
            hits.append("镜头%s(%s, %.1fs)" % (
                sh.get("index", ri + 1),
                str(sh.get("main_subject") or "")[:18],
                float(sh.get("duration_sec", 0) or 0)))
        repairs.append(
            RepairRecord(
                constraint_name=self.name,
                shot_index=window[keep_idx].get("index", 1),
                action="warn",
                target="shots[0:%d]" % _WINDOW,
                before="前%d镜有 %d 个空镜（上限 %d）" % (_WINDOW, len(empty_indices), _MAX_EMPTY),
                after="**只报告，未做任何改写**（v6.99 report-only）",
                detail=("开篇空镜超上限：建议保留信息最丰富的镜头%s，其余 %d 个请由"
                        "结构 regen 重新规划（本约束不再删除镜头）；候选：%s"
                        % (window[keep_idx].get("index", 1), len(remove_indices),
                           "、".join(hits))),
            )
        )
        return repairs