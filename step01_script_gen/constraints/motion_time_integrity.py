"""Validate motion time labels; never guess a replacement timeline.

## 2026-09-27（工作包 A）：判据改成**全仓唯一语法事实源**

计划：`docs/step01-temporal-contract-and-quality-gate-plan-2026-09-27.md` §3。

此前本约束自带一条**只认区间**的前缀正则（`^\\s*\\d+[-~至到]\\d+`），而提示规定的是
三段式 `前X秒：…；X-Y秒：…；Y秒后：…`（`prompts/main.md` §v6.51 时序三铁律 3）。
后果不是偶发误报，而是**结构性**的：

  · 提示强制中段写 `X-Y秒` → 「整段含区间才检查」的前置条件**恒真**；
  · 检查随之扫全镜，而首段 `前N秒：` / 末段 `N秒后：` 都不匹配那条区间正则
    → **每一镜必被报成 HARD**「时间前缀非法或损坏」。

2026-09-27 冻结稿实测：11 镜 11 报，占该轮 `HARD 未解决 19 处` 的 11 处，并因此把
真缺陷（`kf_event_order` 8 处）埋在噪声里。提示与检查器必须同一套语法，所以本约束
改为只经 `shared.motion_segments.head_label()` 判定，不再自持第二套正则。

## 检查范围（未扩大，且更准）

只检**带时序标签**的 motion：出现任一可解析标签，或任一「形状像标签」的残片
（标签区含数字/「秒」但解析不了）。纯自由文本没有「前缀」可损坏，不在本约束范围。

不报的两类（各有明确理由，不是漏检）：

- **部分越界**（`3-9秒` 落在 4 秒镜里）：`parse_segments` 的既有 clamp 语义
  （`motion_segments` 模块文档：「部分越界是正常的 clamp 场景」）；
- **首尾相接**（`(2,4)` 与 `(4,6)`）：不是重叠。

## 关于报告文案里的「阻止进入下游」

旧文案写「保留原文并阻止进入下游」。2026-09-27 逐条核对消费者：`repaired=False` 的
HARD 只进 `report.hard_unresolved`，而全仓唯一读取者是 `constraints/regen_trigger.py`，
`trigger_regen_and_merge()` **已无生产调用方**（见 `constraints/engine.py` v6.98 注：
「regen_trigger 的 LLM 能力保留给显式调用方与测试」）。即「阻止进入下游」当时并不成立，
属**断言失真**（工作准则 7）。本次改为如实描述：本约束只报、不修、不臆造时间轴。
真正要不要拦，属工作包 B（已核实的缺帧接入质量门），不在此处顺手升级。
"""
from __future__ import annotations

from shared import motion_segments as _ms

from .base import Constraint
from .types import Severity, Violation

#: 每类解析错误的报告文案（`HeadLabel.error` → 人话）
_ERROR_TEXT = {
    "unparseable": "时间标签无法解析（残缺或写法不合法）",
    "reversed": "时间区间倒置（起点晚于终点）",
    "zero_length": "时间区间起止相等（零长度，不是时段）",
    "negative": "时间标签含负数",
    "out_of_range": "时间标签整段落在本镜时长之外",
}

#: 本约束**不修**（没有修复前可靠快照就不能臆造时间轴），故统一带这句
_NO_REPAIR = "本约束不臆造时间轴，保留原文交修复轮/审计复核"


class MotionTimeIntegrityConstraint(Constraint):
    name = "motion_time_integrity"
    severity = Severity.HARD
    fields = ("motion_desc",)

    def apply(self, ctx) -> list:
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            motion = str(shot.get("motion_desc") or "")
            if not motion:
                continue
            try:
                duration = float(shot.get("duration_sec") or shot.get("duration") or 0)
            except (TypeError, ValueError):
                duration = 0.0

            labels = [_ms.head_label(a, duration) for a in _ms.atoms(motion)]
            # 无任何时序标签（也无形状像标签的残片）→ 自由文本，不在本约束范围
            if not any(l.kind != "none" or l.error for l in labels):
                continue

            for lab in labels:
                if not lab.error:
                    continue
                why = _ERROR_TEXT.get(lab.error, lab.error)
                if lab.error == "out_of_range":
                    why += "（标签 %s，镜头 %.2fs）" % (lab.label, duration)
                else:
                    why += "（标签 %s）" % (lab.label or "（无标签）")
                ctx.record_violation(Violation(
                    constraint_name=self.name,
                    severity=Severity.HARD,
                    shot_index=None if is_hook else shot.get("index", idx),
                    field="motion_desc",
                    message="motion %s；%s" % (why, _NO_REPAIR),
                    evidence=motion[:180],
                    repaired=False,
                ))

            for l1, l2 in _ms.overlapping_labels(labels):
                ctx.record_violation(Violation(
                    constraint_name=self.name,
                    severity=Severity.HARD,
                    shot_index=None if is_hook else shot.get("index", idx),
                    field="motion_desc",
                    message=("motion 时间区间重叠（%s 与 %s）；%s"
                             % (l1.label, l2.label, _NO_REPAIR)),
                    evidence=motion[:180],
                    repaired=False,
                ))
        return []


__all__ = ["MotionTimeIntegrityConstraint"]
