"""Keep explicitly mentioned crowd actors continuous across keyframes."""
from __future__ import annotations

import re

from shared.alias_map import CHARACTER_ALIASES
from shared.frame_spec import is_on_screen
from .base import Constraint
from .types import RepairRecord, Severity


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
    out = {"群演", "佃仆"}
    out.update(alias for alias, canonical in CHARACTER_ALIASES.items() if canonical == "群演")
    return out


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
    return str(name or "") in aliases or CHARACTER_ALIASES.get(str(name or "")) == "群演"


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
                    existing = {
                        "name": source.get("name", label),
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
                    existing = {
                        "name": source.get("name", label),
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
