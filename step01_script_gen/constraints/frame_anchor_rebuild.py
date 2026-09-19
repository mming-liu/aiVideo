"""Rebuild legacy 【角色锚定】 blocks from the current frame spec.

Old blocks were appended repeatedly.  Even when the new sentence was correct,
the stale sentence remained, producing contradictions such as
``听香仰卧……（蹲坐）``.  This constraint discards the old block and rebuilds it
from frame_layouts for the exact frame/keyframe.
"""
from __future__ import annotations

from .base import Constraint
from .types import RepairRecord, Severity


_MARKER = "【角色锚定】"


def _replace_anchor(desc: str, anchor: str) -> str:
    if not desc:
        return desc
    base = desc.split(_MARKER, 1)[0].rstrip("。！？，,;；")
    if not anchor:
        return base
    return f"{base}。{anchor}" if base else anchor


class FrameAnchorRebuildConstraint(Constraint):
    name = "frame_anchor_rebuild"
    severity = Severity.WARN

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        characters = ctx.script.get("characters") or []
        from shared.vimax_bridge import _extract_char_layout
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            layouts = shot.get("frame_layouts")
            if not isinstance(layouts, dict):
                continue
            for field, frame_kind in (("ff_desc", "first_frame"), ("lf_desc", "last_frame")):
                desc = str(shot.get(field) or "")
                if _MARKER not in desc:
                    continue
                anchor = _extract_char_layout(
                    shot, characters, frame_kind=frame_kind,
                )
                new_desc = _replace_anchor(desc, anchor)
                if new_desc != desc:
                    shot[field] = new_desc
                    repairs.append(RepairRecord(
                        constraint_name=self.name,
                        shot_index=None if is_hook else shot.get("index", idx),
                        action="rewrite",
                        target=field,
                        before=desc[-160:],
                        after=new_desc[-160:],
                        detail="按当前帧 spec 重建角色锚定块",
                    ))
            kfs = shot.get("keyframes") or []
            kf_specs = layouts.get("kfs") or []
            for i, kf in enumerate(kfs):
                if not isinstance(kf, dict):
                    continue
                desc = str(kf.get("desc") or "")
                if _MARKER not in desc:
                    continue
                anchor = _extract_char_layout(
                    shot, characters, frame_kind="keyframe", kf_index=i,
                )
                new_desc = _replace_anchor(desc, anchor)
                if new_desc != desc:
                    kf["desc"] = new_desc
                    repairs.append(RepairRecord(
                        constraint_name=self.name,
                        shot_index=None if is_hook else shot.get("index", idx),
                        action="rewrite",
                        target=f"keyframes[{i}].desc",
                        before=desc[-160:],
                        after=new_desc[-160:],
                        detail="按当前关键帧 spec 重建角色锚定块",
                    ))
        return repairs


__all__ = ["FrameAnchorRebuildConstraint"]