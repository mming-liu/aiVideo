"""Offscreen action consistency: on_screen=false must not demand visible action.

The quality checker reports ``offscreen_character_action`` when a frame spec marks
an entity as off-screen while the corresponding description still contains an
attack verb for that entity.  This constraint resolves the contradiction from
the frame description:

* if the description already contains an exit/absence marker for the entity,
  remove the stale action-only sentence (the exit fact wins);
* otherwise flip the spec to ``on_screen=true`` because the description says the
  entity is actually present.

It never invents a new plot action.
"""
from __future__ import annotations

import re

from shared.frame_spec import is_on_screen

from .base import Constraint
from .types import RepairRecord, Severity, Violation


_ACTION_WORDS = (
    "攻击", "持刀", "持匕首", "刺出", "前刺", "劈砍", "挥刀", "举刀",
    "举匕首", "扑向", "冲出", "砍向", "刺向", "杀向",
)
_EXIT_WORDS = (
    "已没入", "没入水中", "没入水下", "倒纵入水", "已退场", "退场",
    "离场", "不见踪影", "已消失", "已倒下", "倒地不起", "沉入水底",
    "沉入水中", "已死", "死去",
)


def _split_sentences(text: str) -> list[str]:
    return [part for part in re.split(r"(?<=[。！？；])", text or "") if part]


def _remove_stale_action_sentences(text: str, name: str) -> str:
    kept: list[str] = []
    changed = False
    for sentence in _split_sentences(text):
        has_action = any(word in sentence for word in _ACTION_WORDS)
        has_exit = any(word in sentence for word in _EXIT_WORDS)
        if name in sentence and has_action and not has_exit:
            changed = True
            continue
        kept.append(sentence)
    if not changed:
        return text
    out = "".join(kept).strip()
    return out or f"{name}已离场，不在画面中。"


class OffscreenActionConsistencyConstraint(Constraint):
    """Resolve off-screen spec vs visible-action description contradictions."""

    name = "offscreen_action_consistency"
    severity = Severity.HARD
    fields = ("ff_desc", "lf_desc")

    def _apply_to_text(self, shot: dict, text_field: str, entry: dict,
                       repairs: list[RepairRecord], ctx, idx) -> None:
        if is_on_screen(entry):
            return
        name = str(entry.get("name") or "").strip()
        text = str(shot.get(text_field) or "")
        if not name or name not in text:
            return
        has_exit = any(word in text for word in _EXIT_WORDS)
        has_action = any(word in text for word in _ACTION_WORDS)
        if not has_action:
            return
        if has_exit:
            new_text = _remove_stale_action_sentences(text, name)
            if new_text == text:
                return
            shot[text_field] = new_text
            repairs.append(RepairRecord(
                constraint_name=self.name,
                shot_index=idx,
                action="rewrite",
                target=text_field,
                before=text[-120:],
                after=new_text[-120:],
                detail=f"{name} 已离场，删除同帧残留攻击动作分句",
            ))
            ctx.record_violation(Violation(
                constraint_name=self.name,
                severity=Severity.HARD,
                shot_index=idx,
                field=text_field,
                message=f"{name} 已离场但同帧描述仍有攻击动作，已删除矛盾分句",
                evidence=text[-120:],
                repaired=True,
            ))
        else:
            entry["on_screen"] = True
            repairs.append(RepairRecord(
                constraint_name=self.name,
                shot_index=idx,
                action="set_field",
                target=f"{text_field} spec.on_screen",
                before="false",
                after="true",
                detail=f"{name} 帧描述仍有可见动作，同步修正 on_screen",
            ))
            ctx.record_violation(Violation(
                constraint_name=self.name,
                severity=Severity.HARD,
                shot_index=idx,
                field=f"{text_field} spec.on_screen",
                message=f"{name} 标记离场但同帧描述仍有动作，已同步为在场",
                evidence=text[-120:],
                repaired=True,
            ))

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, _is_hook, idx in self.iter_shots_and_hook(ctx):
            layouts = shot.get("frame_layouts")
            if not isinstance(layouts, dict):
                continue
            for bucket in ("ff", "lf"):
                for entry in layouts.get(bucket) or []:
                    if isinstance(entry, dict):
                        self._apply_to_text(shot, f"{bucket}_desc", entry, repairs, ctx, idx)
            kfs = shot.get("keyframes") or []
            kf_specs = layouts.get("kfs") or []
            for i, spec in enumerate(kf_specs):
                if i >= len(kfs) or not isinstance(spec, list):
                    continue
                kf = kfs[i]
                if not isinstance(kf, dict):
                    continue
                for entry in spec:
                    if not isinstance(entry, dict):
                        continue
                    name = str(entry.get("name") or "").strip()
                    text = str(kf.get("desc") or "")
                    if is_on_screen(entry) or not name or name not in text:
                        continue
                    has_exit = any(word in text for word in _EXIT_WORDS)
                    has_action = any(word in text for word in _ACTION_WORDS)
                    if not has_action:
                        continue
                    if has_exit:
                        new_text = _remove_stale_action_sentences(text, name)
                        if new_text == text:
                            continue
                        kf["desc"] = new_text
                        repairs.append(RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx,
                            action="rewrite",
                            target=f"keyframes[{i}].desc",
                            before=text[-120:],
                            after=new_text[-120:],
                            detail=f"{name} 已离场，删除关键帧残留攻击动作分句",
                        ))
                    else:
                        entry["on_screen"] = True
                        repairs.append(RepairRecord(
                            constraint_name=self.name,
                            shot_index=idx,
                            action="set_field",
                            target=f"frame_layouts.kfs[{i}][{name}].on_screen",
                            before="false",
                            after="true",
                            detail=f"{name} 关键帧描述仍有可见动作，同步修正 on_screen",
                        ))
        return repairs


__all__ = ["OffscreenActionConsistencyConstraint"]