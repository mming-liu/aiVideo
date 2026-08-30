"""S. frame_spec_consistency（v6.57+ per-frame spec 一致性约束）.

LLM 输出的 frame_layouts（ff/lf/kfs 三桶）作为 QC + prompt 组装的事实源。
本约束 5 类违例：

  1. frame_layouts 缺失 / 三桶不全 → HARD（HARD 未修触发 LLM regen 修复轮）
  2. 每项 6 必填字段缺一 → HARD
  3. 字段类型错（on_screen 非 bool / items 非 list）→ WARN
  4. kfs 数量 ≠ keyframes 数量 → HARD（治 kf 时序塌缩 + spec-keyframes 错位）
  5. on_screen=True 角色在对应 desc 中体现「退场/没入/倒下」→ HARD
     （治 lf 互斥根因：spec 标在场 + desc 写已没入水中 = 矛盾 → regen）

老章（无 frame_layouts）走 char_layout 降级 → 全部降为 WARN（不阻塞 regen）。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity, Violation


# spec 每项必填 6 字段（与 _FRAME_SPEC_ITEM schema 同步）
_REQUIRED_FIELDS = ("name", "on_screen", "position", "pose", "gaze_target", "props")
_BUCKETS_TOP = ("ff", "lf")  # 二维 kfs 单独处理

# 角色「退场/没入/倒下」标记词（spec on_screen=True + desc 命中 → 矛盾）
# 仅含「在主体名附近 30 字符内」匹配，避免误判其他动作
_EXIT_WORDS = (
    "已没入", "没入水中", "没入水下", "倒纵入水", "已倒下", "倒地不起",
    "退入", "已退场", "沉入", "沉入水底", "不见踪影", "已消失",
    "已死", "死去", "倒地", "倒下", "不在",
)


class FrameSpecConsistencyConstraint(Constraint):
    """S. frame_spec_consistency：per-frame spec 必填 + 完整性 + desc 一致性.

    severity=HARD: 违例未修触发 LLM regen 修复轮。
    老章（无 frame_layouts）走 char_layout 降级 → 全部降为 WARN（不阻塞）。
    """

    name = "frame_spec_consistency"
    severity = Severity.HARD
    # 描述类字段：用于「on_screen 矛盾」检测
    fields = ("ff_desc", "lf_desc", "motion_desc", "prompt")
    skip_hook = False

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            label = "hook" if is_hook else f"shot{shot.get('index', idx)}"
            shot_idx = None if is_hook else shot.get("index")

            fl = shot.get("frame_layouts")
            if not isinstance(fl, dict):
                # 老章降级 → WARN（不阻塞）
                ctx.record_violation(
                    Violation(
                        constraint_name=self.name,
                        severity=Severity.WARN,
                        shot_index=shot_idx,
                        field="frame_layouts",
                        message=(
                            f"{label} 缺 frame_layouts（老章降级，使用 char_layout 派生）"
                        ),
                    )
                )
                continue

            # === 检查 1：三桶必填 ===
            for bucket in ("ff", "lf", "kfs"):
                if bucket not in fl:
                    ctx.record_violation(
                        Violation(
                            constraint_name=self.name,
                            severity=Severity.HARD,
                            shot_index=shot_idx,
                            field=f"frame_layouts.{bucket}",
                            message=f"{label} frame_layouts 缺 {bucket} 桶",
                        )
                    )

            # === 检查 2+3：ff / lf 每项 6 字段必填 + 类型错 ===
            for bucket in _BUCKETS_TOP:
                items = fl.get(bucket)
                if items is None:
                    continue
                if not isinstance(items, list):
                    ctx.record_violation(
                        Violation(
                            constraint_name=self.name,
                            severity=Severity.HARD,
                            shot_index=shot_idx,
                            field=f"frame_layouts.{bucket}",
                            message=f"{label} frame_layouts.{bucket} 不是数组（{type(items).__name__}）",
                        )
                    )
                    continue
                for j, item in enumerate(items):
                    if not isinstance(item, dict):
                        ctx.record_violation(
                            Violation(
                                constraint_name=self.name,
                                severity=Severity.HARD,
                                shot_index=shot_idx,
                                field=f"frame_layouts.{bucket}[{j}]",
                                message=f"{label} frame_layouts.{bucket}[{j}] 不是 dict",
                            )
                        )
                        continue
                    # 6 必填字段缺一
                    for fld in _REQUIRED_FIELDS:
                        if fld not in item:
                            ctx.record_violation(
                                Violation(
                                    constraint_name=self.name,
                                    severity=Severity.HARD,
                                    shot_index=shot_idx,
                                    field=f"frame_layouts.{bucket}[{j}].{fld}",
                                    message=f"{label} frame_layouts.{bucket}[{j}] 缺 {fld} 字段",
                                )
                            )
                    # 类型错（on_screen 必须是 bool）
                    if "on_screen" in item and not isinstance(item.get("on_screen"), bool):
                        ctx.record_violation(
                            Violation(
                                constraint_name=self.name,
                                severity=Severity.WARN,
                                shot_index=shot_idx,
                                field=f"frame_layouts.{bucket}[{j}].on_screen",
                                message=(
                                    f"{label} frame_layouts.{bucket}[{j}].on_screen "
                                    f"应为 bool，得到 {type(item.get('on_screen')).__name__}"
                                ),
                            )
                        )

            # === 检查 2+3：kfs 二维数组 ===
            kfs = fl.get("kfs")
            if kfs is None:
                continue
            if not isinstance(kfs, list):
                ctx.record_violation(
                    Violation(
                        constraint_name=self.name,
                        severity=Severity.HARD,
                        shot_index=shot_idx,
                        field="frame_layouts.kfs",
                        message=f"{label} frame_layouts.kfs 不是数组（{type(kfs).__name__}）",
                    )
                )
                continue

            # === 检查 4：kfs 数量 vs keyframes 数量 ===
            shot_kfs = shot.get("keyframes") or []
            if isinstance(shot_kfs, list) and len(shot_kfs) != len(kfs):
                ctx.record_violation(
                    Violation(
                        constraint_name=self.name,
                        severity=Severity.HARD,
                        shot_index=shot_idx,
                        field="frame_layouts.kfs",
                        message=(
                            f"{label} kfs 数量 {len(kfs)} 与 keyframes 数量 {len(shot_kfs)} 不匹配"
                        ),
                    )
                )

            # kfs 内部每项 6 字段
            for i, kf_items in enumerate(kfs):
                if not isinstance(kf_items, list):
                    ctx.record_violation(
                        Violation(
                            constraint_name=self.name,
                            severity=Severity.HARD,
                            shot_index=shot_idx,
                            field=f"frame_layouts.kfs[{i}]",
                            message=f"{label} frame_layouts.kfs[{i}] 不是数组（应为每关键帧一项出镜表）",
                        )
                    )
                    continue
                for j, item in enumerate(kf_items):
                    if not isinstance(item, dict):
                        continue
                    for fld in _REQUIRED_FIELDS:
                        if fld not in item:
                            ctx.record_violation(
                                Violation(
                                    constraint_name=self.name,
                                    severity=Severity.HARD,
                                    shot_index=shot_idx,
                                    field=f"frame_layouts.kfs[{i}][{j}].{fld}",
                                    message=f"{label} kfs[{i}][{j}] 缺 {fld} 字段",
                                )
                            )

            # === 检查 5：on_screen=True 角色在 desc 体现退场（lf 互斥根因） ===
            # ff → ff_desc; lf → lf_desc（不检 motion_desc/prompt 以免误报）
            self._check_onscreen_consistency(ctx, fl, shot, shot_idx, label)

        return repairs

    def _check_onscreen_consistency(
        self,
        ctx,
        fl: dict,
        shot: dict,
        shot_idx,
        label: str,
    ) -> None:
        """on_screen=True 角色若对应 desc 体现退场/没入/倒下 → HARD 违例.

        治 lf 互斥根因：spec 标在场 + desc 写「已没入水中」 = 数据矛盾 → regen.
        """
        for bucket, desc_field in (("ff", "ff_desc"), ("lf", "lf_desc")):
            items = fl.get(bucket) or []
            desc = shot.get(desc_field) or ""
            if not isinstance(items, list) or not isinstance(desc, str) or not desc:
                continue
            for item in items:
                if not isinstance(item, dict) or item.get("on_screen") is not True:
                    continue
                name = str(item.get("name", "")).strip()
                if not name:
                    continue
                # 在 desc 中找 name 出现位置，检其后 30 字符内是否有退场词
                for m in re.finditer(re.escape(name), desc):
                    tail = desc[m.end(): m.end() + 30]
                    if any(w in tail for w in _EXIT_WORDS):
                        ctx.record_violation(
                            Violation(
                                constraint_name=self.name,
                                severity=Severity.HARD,
                                shot_index=shot_idx,
                                field=desc_field,
                                message=(
                                    f"{label} {desc_field} 含 on_screen=true 角色「{name}」"
                                    f"但 desc 体现退场（命中：{next(w for w in _EXIT_WORDS if w in tail)}）——"
                                    f"frame_layouts.{bucket} 应把 {name} 标 on_screen=false"
                                ),
                            )
                        )
                        break  # 一条违例一个角色
