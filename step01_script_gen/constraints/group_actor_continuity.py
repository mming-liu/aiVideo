"""Keep explicitly mentioned crowd actors continuous across keyframes."""
from __future__ import annotations

import re

from shared.frame_spec import is_on_screen
from .base import Constraint
from .types import RepairRecord, Severity, get_repair_effects
# §14（2026-10-03）：群体别名口径与「已授权删除」判据都收在 `repair_effects` 单一来源。
# 此前本模块自带一份 `_group_aliases()`——与效果账本各写一份别名表，正是
# 「同一事实两处实现」的形状（改一处漏一处）。
from ..repair_effects import group_alias_names, is_group_name


# 明确的退场姿态词。**只用于「写 False」这一个方向**（v6.106，2026-09-24）.
#
# 实测（第二十七次重跑）：本约束对**已有条目**也做双向赋值
# `on_screen = pose not in (退下/退场/离场)`——非退场 pose 被当成「在场证据」，
# 把描述里已写明离场的群体**翻回 true**：
#
#   shot 1：`frame_presence_consistency` 报「描述称老农已走远，布局仍 on_screen=true」
#   → 修复轮改成 false → 本轮后处理又被本约束翻回 true
#   → 同一处修复连做 4 轮、`resolved=0`，阻塞永远清不掉（BLOCK 的直接成因之一）
#
# 事实源优先级（总计划 §1.2）：**帧描述 > frame_layouts**。布局是描述派生出来的，
# 拿一个 3 词的姿态词表去推翻描述，等于把这个优先级倒过来。
#
# 所以：pose 明确是退场词 → 可以安全写 False；pose 不是退场词 → **不构成在场
# 证据**，已有 false 原样保留。缺条目时的新建仍按原逻辑（描述里提到了该群体），
# 是否与描述矛盾交由跨字段检测 + 修复轮判定。
_EXIT_POSES: tuple = ("退下", "退场", "离场")


def _apply_presence(existing: dict, pose: str) -> bool:
    """按「只降不升」写已有条目的 `on_screen`；返回是否发生改动."""
    if pose in _EXIT_POSES and is_on_screen(existing):
        existing["on_screen"] = False
        return True
    return False


def _group_aliases() -> set[str]:
    return group_alias_names()


# ---------------------------------------------------------------------------
# §14 P3 第 1/6 行：**本轮已授权删除的条目不得按类别别名重建**
#
# 缺陷（批次 `20261003T182636-d26992-001-564d9b` ch2）：审计说「小厮 是张十三的
# 重复实体」→ 模型在 `frame_layouts.kfs[0]/[1]` 上删对了（授权与合并都通过）→ 本
# 约束按「文本提到群体」把它加回来 → 验收看到与基线相同 ⇒ `no_target_resolved`
# ⇒ 整轮回退 ⇒ BLOCK。**没有一步是模型没改**。
#
# 判据（只按帧、按条目生效，缺省 None 时行为**逐字不变**）：
#   · 该帧上有本轮的效果记录；
#   · 那条效果**删掉的条目**与已在场人物的关系**明确是同一实体**
#     （生产者给了证据 locator，见 `repair_effects.resolve_frame_actor_binding`）；
#   · 正要重建的条目名正是它。
# 三者同时成立 ⇒ **本次不再重建**。类别别名只说明类别，不能单独证明「这是另一个
# 人」——关系没给出来（或明确是另一实体）时本判据不生效，行为与之前逐字一致，
# 由驱动层按 `actor_binding_ambiguous` / `actor_presence_conflict` 反馈原生产者。
# **不全局停用本约束**（第 6 行：非目标帧、无效果记录的帧、合法群体补齐全部照旧）。
# ---------------------------------------------------------------------------

def _target_name(shot: dict, is_hook: bool, idx) -> str:
    if is_hook:
        return "hook"
    return "shot_%s" % shot.get("index", idx)


def _must_not_rebuild(ctx, shot: dict, is_hook: bool, idx, locator: str,
                      entry_name: str) -> bool:
    """该条目是否因「本轮已授权删除且**关系明确是同一实体**」而不得重建."""
    view = get_repair_effects(ctx)
    if view is None:
        return False
    target = _target_name(shot, is_hook, idx)
    from ..repair_effects import entity_surface_names, frame_entry_names

    script = getattr(ctx, "script", None) or {}
    honored = view.honored_removals(
        target, locator,
        remaining=frame_entry_names(shot, locator),
        entity_names=lambda entity: entity_surface_names(
            entity, script.get("characters"), script.get("chapter_outline")))
    if not honored:
        return False
    return str(entry_name or "") in honored


def _find_group_label(shot: dict) -> str:
    aliases = _group_aliases()
    texts = [
        str(shot.get("motion_desc") or ""),
        str(shot.get("ff_desc") or ""),
        str(shot.get("lf_desc") or ""),
    ]
    for kf in shot.get("keyframes") or []:
        if isinstance(kf, dict):
            texts.append(str(kf.get("desc") or ""))
    found = []
    for text_index, text in enumerate(texts):
        for alias in aliases:
            pos = text.find(alias)
            if pos >= 0:
                found.append((text_index, pos, alias))
    if not found:
        return ""
    return min(found, key=lambda item: (item[0], item[1]))[2]


def _same_group(name: str, aliases: set[str]) -> bool:
    # 唯一实现：别名口径收在 `repair_effects.is_group_name`（与效果账本同源）。
    # `aliases` 参数保留只为调用点形状不变。
    return is_group_name(name)


def _restore_label(text: str, label: str) -> str:
    text = str(text or "")
    if label and label != "群演":
        # Targeted migration from the previous faulty repair:
        # 佃仆群演们 -> 佃仆们
        # 佃仆佃仆们 -> 佃仆们
        text = text.replace(f"{label}群演们", f"{label}们")
        text = text.replace(f"{label}{label}们", f"{label}们")
        text = text.replace("群演", label)
    return text


def _restore_subject(text: str, label: str) -> str:
    text = str(text or "")
    for action in ("哄笑", "退下", "张望", "七嘴八舌"):
        # Only bare "们动作" is missing a subject.  “佃仆们退下” and
        # “群演们哄笑” already carry their subject and must be preserved.
        text = re.sub(
            rf"(?<![\u4e00-\u9fff])们{re.escape(action)}",
            f"{label}们{action}",
            text,
        )
    return text


def _pose_for(text: str) -> str:
    for word in ("张望", "哄笑", "退下", "转身欲走", "退至岸边", "站立岸边", "七嘴八舌"):
        if word in text:
            return word
    return ""


class GroupActorContinuityConstraint(Constraint):
    name = "group_actor_continuity"
    severity = Severity.WARN
    fields = ()

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            label = _find_group_label(shot)
            if not label:
                continue
            shot_index = None if is_hook else shot.get("index", idx)
            for field in ("motion_desc", "ff_desc", "lf_desc"):
                text = str(shot.get(field) or "")
                fixed = _restore_subject(_restore_label(text, label), label)
                if fixed != text:
                    shot[field] = fixed
                    repairs.append(RepairRecord(
                        constraint_name=self.name,
                        shot_index=shot_index,
                        action="rewrite",
                        target=field,
                        before=text[-120:],
                        after=fixed[-120:],
                        detail="恢复群体动作主体",
                    ))
            for kf_i, kf in enumerate(shot.get("keyframes") or []):
                if not isinstance(kf, dict):
                    continue
                text = str(kf.get("desc") or "")
                fixed = _restore_subject(_restore_label(text, label), label)
                if fixed != text:
                    kf["desc"] = fixed
                    repairs.append(RepairRecord(
                        constraint_name=self.name,
                        shot_index=shot_index,
                        action="rewrite",
                        target=f"keyframes[{kf_i}].desc",
                        before=text[-120:],
                        after=fixed[-120:],
                        detail="恢复关键帧群体动作主体",
                    ))

            layouts = shot.get("frame_layouts")
            if not isinstance(layouts, dict):
                continue
            aliases = _group_aliases()
            source = None
            for key in ("ff", "lf"):
                for item in layouts.get(key) or []:
                    if isinstance(item, dict) and _same_group(item.get("name", ""), aliases):
                        source = item
                        break
                if source:
                    break
            if source is None:
                source = {
                    "name": label, "on_screen": True, "position": "",
                    "pose": "", "gaze_target": "无", "props": [],
                }
            for frame_key, field in (("ff", "ff_desc"), ("lf", "lf_desc")):
                spec = layouts.get(frame_key)
                if not isinstance(spec, list):
                    continue
                text = str(shot.get(field) or "")
                if not any(alias in text for alias in aliases):
                    continue
                existing = next(
                    (item for item in spec if isinstance(item, dict) and _same_group(item.get("name", ""), aliases)),
                    None,
                )
                pose = _pose_for(text)
                on_screen = pose not in _EXIT_POSES
                if existing is None:
                    _new_name = source.get("name", label)
                    if _must_not_rebuild(ctx, shot, is_hook, idx,
                                         f"frame_layouts.{frame_key}", _new_name):
                        continue
                    existing = {
                        "name": _new_name,
                        "on_screen": on_screen,
                        "position": source.get("position", ""),
                        "pose": pose or source.get("pose", ""),
                        "gaze_target": source.get("gaze_target", "无"),
                        "props": list(source.get("props") or []),
                    }
                    spec.append(existing)
                    repairs.append(RepairRecord(
                        constraint_name=self.name,
                        shot_index=shot_index,
                        action="append",
                        target=f"frame_layouts.{frame_key}",
                        after=str(existing),
                        detail="补齐首/尾帧群体在场状态",
                    ))
                else:
                    changed = _apply_presence(existing, pose)
                    if pose and existing.get("pose") != pose:
                        existing["pose"] = pose
                        changed = True
                    if changed:
                        repairs.append(RepairRecord(
                            constraint_name=self.name,
                            shot_index=shot_index,
                            action="rewrite",
                            target=f"frame_layouts.{frame_key}.pose",
                            before=str(existing.get("pose", "")),
                            after=str(existing.get("pose", "")),
                            detail="同步首/尾帧群体在场/动作状态",
                        ))

            kfs = shot.get("keyframes") or []
            kf_specs = layouts.get("kfs")
            if not isinstance(kf_specs, list):
                continue
            for i, spec in enumerate(kf_specs):
                if not isinstance(spec, list):
                    continue
                text = str(kfs[i].get("desc") or "") if i < len(kfs) and isinstance(kfs[i], dict) else ""
                if not any(alias in text for alias in aliases):
                    continue
                existing = next(
                    (item for item in spec if isinstance(item, dict) and _same_group(item.get("name", ""), aliases)),
                    None,
                )
                pose = _pose_for(text)
                on_screen = pose not in _EXIT_POSES
                if existing is None:
                    _new_name = source.get("name", label)
                    if _must_not_rebuild(ctx, shot, is_hook, idx,
                                         f"frame_layouts.kfs[{i}]", _new_name):
                        continue
                    existing = {
                        "name": _new_name,
                        "on_screen": on_screen,
                        "position": source.get("position", ""),
                        "pose": pose,
                        "gaze_target": source.get("gaze_target", "无"),
                        "props": list(source.get("props") or []),
                    }
                    spec.append(existing)
                    repairs.append(RepairRecord(
                        constraint_name=self.name,
                        shot_index=shot_index,
                        action="append",
                        target=f"frame_layouts.kfs[{i}]",
                        after=str(existing),
                        detail="补齐关键帧群体在场状态",
                    ))
                else:
                    changed = _apply_presence(existing, pose)
                    if pose and existing.get("pose") != pose:
                        before = existing.get("pose", "")
                        existing["pose"] = pose
                        changed = True
                    if changed:
                        repairs.append(RepairRecord(
                            constraint_name=self.name,
                            shot_index=shot_index,
                            action="rewrite",
                            target=f"frame_layouts.kfs[{i}].pose",
                            before=str(existing.get("pose", "")),
                            after=str(existing["pose"]),
                            detail="同步群体关键帧动作/在场状态",
                        ))
        return repairs


__all__ = ["GroupActorContinuityConstraint"]
