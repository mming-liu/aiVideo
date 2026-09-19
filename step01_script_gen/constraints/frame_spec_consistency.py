"""S. frame_spec_consistency（v6.57+ per-frame spec 一致性约束）.

LLM 输出的 frame_layouts（ff/lf/kfs 三桶）作为 QC + prompt 组装的事实源。
本约束 7 类违例：

  1. frame_layouts 缺失 / 三桶不全 → HARD（HARD 未修触发 LLM regen 修复轮）
  2. 每项 6 必填字段缺一 → HARD
  3. 字段类型错（on_screen 非 bool / items 非 list）→ WARN
  4. kfs 数量 ≠ keyframes 数量 → HARD（治 kf 时序塌缩 + spec-keyframes 错位）
  5. on_screen=True 角色在对应 desc 中体现「退场/没入/倒下」→ HARD
     （治 lf 互斥根因：spec 标在场 + desc 写已没入水中 = 矛盾 → regen）
  6. 散文×spec 道具状态交叉校验（desc 离手 vs spec 仍持有）→ WARN
  7. desc 提及角色不在该帧 spec → 确定性补条目 + HARD（已修）
     （治 09-13 hook 残 spec 根因：QC 名单按残表打架）

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
from shared.frame_spec import is_on_screen


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

# 道具「脱手/掉落」状态词（散文命中 + spec props 仍含该道具 → 矛盾）
# 用「道具名 + 状态词」或「状态词 + 道具名」双向匹配
_PROP_DROP_WORDS = (
    "已脱手", "脱手", "滑落", "倾倒", "坠落", "掉落", "掉下", "掉在",
    "放下", "放下了", "垂于水面", "垂于", "滑落水中", "跌入水中",
)

# 持有动词（pose 命中 + props 有该道具 → 正常；props 没有 → 可能漏写，WARN）
_HOLD_VERBS = (
    "握", "提", "执", "拿", "举", "持", "抓", "攥", "捧", "抱",
    "扛", "背", "挎", "叼", "含",
)

# 否定提及前缀（desc 写「已无X」类退场叙事 → 不是 spec 缺标，检查 7 不补）
_NEGATION_PREFIX = ("已无", "没有", "不见", "再无", "失去", "已逝")


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

            # === 检查 6：散文×spec 道具状态交叉校验（治道具时序倒流） ===
            # 该帧散文命中「X已脱手/滑落/倾倒/…」+ spec props 仍含 X → WARN（进修复轮）
            self._check_props_consistency(ctx, fl, shot, shot_idx, label)

            # === 检查 7：desc 提及角色必须在 spec 出镜表内（09-13 hook 残 spec 根因） ===
            # desc 在场但 spec 缺人 → 确定性补条目（desc 是画面事实源，v6.62 原则）
            self._check_roster_coverage(ctx, shot, is_hook, shot_idx, label)

        # 09-13 修正：返回 drain 而非恒空列表——此前 record_repair 的记录滞留
        # ctx._pending_repairs，report.repairs/[CONSTRAINT] 日志永远看不到本约束
        # 的修复（检查 5/7 都中招）
        return ctx.drain_repairs()

    def _check_onscreen_consistency(
        self,
        ctx,
        fl: dict,
        shot: dict,
        shot_idx,
        label: str,
    ) -> None:
        """on_screen=True 角色若对应 desc 体现退场/没入/倒下 → 确定性修复 + HARD 违例.

        治 lf 互斥根因：spec 标在场 + desc 写「已没入水中」 = 数据矛盾。

        v6.62（09-06）：desc 是画面事实源（图片模型按它画），spec 漏标是
        LLM 记账错误 → 直接把 on_screen 降为 False（确定性修复，消除 HARD
        循环：09-05/09-06 实测每轮跑完都剩 frame_spec@shotN「已没入」挂着，
        LLM regen 也修不掉）。修复后仍记录 HARD 违例（供日志审计），但
        violations 不再进 hard_unresolved（record_violation → repaired=True）。
        """
        for bucket, desc_field in (("ff", "ff_desc"), ("lf", "lf_desc")):
            items = fl.get(bucket) or []
            desc = shot.get(desc_field) or ""
            if not isinstance(items, list) or not isinstance(desc, str) or not desc:
                continue
            for item in items:
                if not is_on_screen(item):
                    continue
                name = str(item.get("name", "")).strip()
                if not name:
                    continue
                # 在 desc 中找 name 出现位置，检其后 30 字符内是否有退场词
                for m in re.finditer(re.escape(name), desc):
                    tail = desc[m.end(): m.end() + 30]
                    hit = next((w for w in _EXIT_WORDS if w in tail), None)
                    if not hit:
                        continue
                    ctx.record_violation(
                        Violation(
                            constraint_name=self.name,
                            severity=Severity.HARD,
                            shot_index=shot_idx,
                            field=desc_field,
                            repaired=False,
                            message=(
                                f"{label} {desc_field} 含 on_screen=true 角色「{name}」"
                                f"但 desc 体现退场（命中：{hit}）——保留原 spec，"
                                "仅报告冲突并交 LLM regen，禁止自动翻转"
                            ),
                        )
                    )
                    break  # 一条违例一个角色

    def _check_props_consistency(
        self,
        ctx,
        fl: dict,
        shot: dict,
        shot_idx,
        label: str,
    ) -> None:
        """散文×spec 道具状态交叉校验（治道具时序倒流）.

        检测逻辑（WARN 级别，进修复轮）：
          - 该帧散文命中「道具名 + 状态词」或「状态词 + 道具名」→ 道具已离手
          - 同时 spec.props 含该道具名 或 spec.pose 含「[握提执拿举持]…道具名」 → 矛盾

        覆盖 ff / lf / 所有 kf。
        通用机制：状态词表 + 持有动词表封闭可扩展，对任何道具成立。
        """
        # 收集所有帧：(bucket名, desc文本, spec_items列表)
        frames = []
        # ff / lf
        for bucket, desc_field in (("ff", "ff_desc"), ("lf", "lf_desc")):
            items = fl.get(bucket)
            desc = shot.get(desc_field) or ""
            if isinstance(items, list) and isinstance(desc, str):
                frames.append((f"frame_layouts.{bucket}", desc_field, desc, items))
        # kfs
        kfs_spec = fl.get("kfs") or []
        kfs_desc = shot.get("keyframes") or []
        if isinstance(kfs_spec, list) and isinstance(kfs_desc, list):
            for i, (spec_items, kf) in enumerate(zip(kfs_spec, kfs_desc)):
                desc = kf.get("desc", "") if isinstance(kf, dict) else ""
                if isinstance(spec_items, list) and isinstance(desc, str):
                    frames.append((
                        f"frame_layouts.kfs[{i}]",
                        f"keyframes[{i}].desc",
                        desc,
                        spec_items,
                    ))

        for spec_path, desc_field, desc, items in frames:
            if not desc:
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if not name:
                    continue
                props = item.get("props") or []
                pose = str(item.get("pose", "") or "")
                if not isinstance(props, list):
                    continue

                # 遍历所有道具名，检查 desc 中是否有该道具的"离手"状态
                for prop_name in props:
                    prop_str = str(prop_name).strip()
                    if not prop_str:
                        continue
                    # 检查 desc 中是否有「prop + 状态词」或「状态词 + prop」
                    dropped = self._is_prop_dropped(desc, prop_str)
                    if dropped:
                        ctx.record_violation(
                            Violation(
                                constraint_name=self.name,
                                severity=Severity.WARN,
                                shot_index=shot_idx,
                                field=f"{spec_path}.props",
                                message=(
                                    f"{label} {desc_field} 体现「{prop_str}已离手」"
                                    f"（命中：{dropped}），但 {spec_path} "
                                    f"「{name}」props 仍含「{prop_str}」"
                                    f"——props 应为空（道具不在手中）"
                                ),
                                evidence=dropped,
                            )
                        )

                # 检查 pose 中的持有动作与 desc 的离手状态矛盾
                # 道具名来源：① props 列表中明确列出的；② 从 pose 文本中用
                # 「持有动词 + 2~4 字名词」正则提取的候选（治 props 已清空但
                # pose 还写"握酒壶"的场景）
                prop_candidates: set[str] = set()
                for p in props:
                    ps = str(p).strip()
                    if ps:
                        prop_candidates.add(ps)
                # 从 pose 中提取持有动词+道具名候选（2~4 个汉字）
                if pose:
                    hold_pat = re.compile(
                        f"({'|'.join(re.escape(v) for v in _HOLD_VERBS)})"
                        r"([一-龥]{2,4})"
                    )
                    for m in hold_pat.finditer(pose):
                        prop_candidates.add(m.group(2))

                for prop_str in prop_candidates:
                    if not prop_str:
                        continue
                    # 找到 pose 中该道具对应的持有动词（用于报错信息）
                    matched_verb = ""
                    for hold_v in _HOLD_VERBS:
                        if f"{hold_v}{prop_str}" in pose:
                            matched_verb = hold_v
                            break
                    # desc 中该道具已离手？
                    dropped = self._is_prop_dropped(desc, prop_str)
                    if dropped and matched_verb:
                        ctx.record_violation(
                            Violation(
                                constraint_name=self.name,
                                severity=Severity.WARN,
                                shot_index=shot_idx,
                                field=f"{spec_path}.pose",
                                message=(
                                    f"{label} {desc_field} 体现「{prop_str}已离手」"
                                    f"（命中：{dropped}），但 {spec_path} "
                                    f"「{name}」pose 写「{matched_verb}{prop_str}」"
                                    f"——时间倒流，应删除持有动作"
                                ),
                                evidence=dropped,
                            )
                        )

    def _is_prop_dropped(self, desc: str, prop_name: str) -> str:
        """检查 desc 中 prop_name 是否处于"离手"状态.

        匹配模式：
          - prop_name + 状态词（如"酒壶已脱手"、"钓杆滑落"）
          - 状态词 + prop_name（"的"字结构，如"滑落的酒壶"）

        返回命中的完整片段（用于 evidence）；未命中返回空字符串。
        """
        # prop_name + 状态词
        for w in _PROP_DROP_WORDS:
            pattern = f"{prop_name}{w}"
            if pattern in desc:
                return pattern
        # 状态词 + prop_name（"的"字结构，如"滑落的酒壶"）
        for w in _PROP_DROP_WORDS:
            pattern = f"{w}的{prop_name}"
            if pattern in desc:
                return pattern
        return ""

    def _check_roster_coverage(
        self,
        ctx,
        shot: dict,
        is_hook: bool,
        shot_idx,
        label: str,
    ) -> None:
        """Report desc/spec roster conflicts without changing ``on_screen``.

        A name appearing in prose does not prove that the character is visually
        present.  The name may be a memory, dialogue reference, negation,
        off-screen subject, or shared alias.  Therefore this check preserves
        the existing spec and emits a conflict for the repair round.
        """
        fl = shot.get("frame_layouts")
        if not isinstance(fl, dict):
            return
        if is_hook:
            chars = [str(c.get("identifier", "")).strip()
                     for c in (ctx.script.get("characters") or [])
                     if isinstance(c, dict)]
        else:
            chars = [str(e.get("name", "")).strip()
                     for e in (shot.get("char_layout") or [])
                     if isinstance(e, dict)]
        chars = [c for c in chars if c]
        if not chars:
            return

        frames = [("ff", "ff_desc", shot.get("ff_desc", "") or "", fl.get("ff")),
                  ("lf", "lf_desc", shot.get("lf_desc", "") or "", fl.get("lf"))]
        kfs_spec = fl.get("kfs") or []
        for i, kf in enumerate(shot.get("keyframes") or []):
            if i >= len(kfs_spec) or not isinstance(kfs_spec[i], list):
                continue
            desc = kf.get("desc", "") if isinstance(kf, dict) else ""
            frames.append(
                (f"kfs[{i}]", f"keyframes[{i}].desc", desc or "", kfs_spec[i]))

        for bucket, desc_field, desc, items in frames:
            if not desc or not isinstance(items, list):
                continue
            present = {str(e.get("name", "")).strip() for e in items
                       if isinstance(e, dict) and is_on_screen(e)}
            for name in chars:
                if name in present or name not in desc:
                    continue
                offscreen = next(
                    (e for e in items
                     if isinstance(e, dict)
                     and str(e.get("name", "")).strip() == name
                     and not is_on_screen(e)),
                    None,
                )
                state = "on_screen=false" if offscreen is not None else "无条目"
                ctx.record_violation(
                    Violation(
                        constraint_name=self.name,
                        severity=Severity.HARD,
                        shot_index=shot_idx,
                        field=f"frame_layouts.{bucket}",
                        repaired=False,
                        message=(
                            f"{label} {desc_field} 提及角色「{name}」，"
                            f"但 frame_layouts.{bucket} 为 {state}。名字出现不等于"
                            "角色在场；保留原 spec 并交 LLM regen，禁止自动翻转/补条目"
                        ),
                        evidence=desc[:180],
                    )
                )
