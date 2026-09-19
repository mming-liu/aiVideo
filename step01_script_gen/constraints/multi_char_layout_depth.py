"""Normalize illegal position strings without rearranging composition.

This constraint no longer assigns foreground/background or forces unique
positions.  Those choices require camera and staging evidence and belong to a
separate planning step or semantic audit.
"""
from __future__ import annotations

from .base import Constraint
from .types import RepairRecord, Severity


def _normalize_bad_position(pos: str) -> str:
    import re as _re
    pos = str(pos or "").strip()
    m = _re.match(r"^画[左右](画中|前景|后景|中景|船中|船头|船尾|水面)(\d+)?$", pos)
    if m:
        return m.group(1)
    return pos


class MultiCharLayoutDepthConstraint(Constraint):
    """Normalize illegal concatenated positions only.

    Composition, depth and camera framing are intentionally not inferred here.
    """

    name = "multi_char_layout_depth"
    severity = Severity.WARN
    skip_hook = True

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            if not isinstance(shot, dict):
                continue
            layout = shot.get("char_layout")
            if not isinstance(layout, list):
                continue
            layouts = shot.get("frame_layouts")
            if isinstance(layouts, dict):
                frame_buckets = []
                for key in ("ff", "lf"):
                    if isinstance(layouts.get(key), list):
                        frame_buckets.append(layouts[key])
                if isinstance(layouts.get("kfs"), list):
                    frame_buckets.extend(
                        bucket for bucket in layouts.get("kfs") if isinstance(bucket, list)
                    )
                for bucket in frame_buckets:
                    for item in bucket:
                        if not isinstance(item, dict):
                            continue
                        old_pos = str(item.get("position", "")).strip()
                        fixed_pos = _normalize_bad_position(old_pos)
                        if fixed_pos != old_pos:
                            item["position"] = fixed_pos
                            repairs.append(RepairRecord(
                                constraint_name=self.name,
                                shot_index=idx,
                                action="rewrite",
                                target="frame_layouts[*].position",
                                before=old_pos,
                                after=fixed_pos,
                                detail="只清理非法拼接位置，不重新安排构图",
                            ))

            for item in layout:
                if not isinstance(item, dict):
                    continue
                old_pos = str(item.get("position", "")).strip()
                fixed_pos = _normalize_bad_position(old_pos)
                if fixed_pos != old_pos:
                    item["position"] = fixed_pos
                    repairs.append(RepairRecord(
                        constraint_name=self.name,
                        shot_index=idx,
                        action="rewrite",
                        target="char_layout[*].position",
                        before=old_pos,
                        after=fixed_pos,
                        detail="清理拼接位置和数字后缀，保留合法空间词",
                    ))
        return repairs
