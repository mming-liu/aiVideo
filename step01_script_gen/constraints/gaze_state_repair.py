"""Deterministically fix gaze_target for characters that cannot gaze."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.frame_spec import gaze_blocked_for, is_on_screen
from .base import Constraint
from .types import RepairRecord, Severity


class GazeStateRepairConstraint(Constraint):
    """Set gaze_target to 无 when pose says the character cannot gaze."""

    name = "gaze_state_repair"
    severity = Severity.WARN
    fields = ()

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            fl = shot.get("frame_layouts") or {}
            if not isinstance(fl, dict):
                continue
            kfs_data = shot.get("keyframes") or []
            frames = [
                ("ff", fl.get("ff") or [], shot.get("ff_desc", "") or ""),
                ("lf", fl.get("lf") or [], shot.get("lf_desc", "") or ""),
            ]
            kfs_spec = fl.get("kfs") or []
            for ki, kf in enumerate(kfs_data):
                spec = kfs_spec[ki] if ki < len(kfs_spec) else []
                desc = kf.get("desc", "") if isinstance(kf, dict) else ""
                frames.append((f"kfs[{ki}]", spec if isinstance(spec, list) else [], desc or ""))

            for frame_kind, entries, frame_text in frames:
                if not isinstance(entries, list):
                    continue
                names = [
                    str(entry.get("name", "")).strip()
                    for entry in entries
                    if isinstance(entry, dict) and is_on_screen(entry)
                ]
                for entry in entries:
                    if not isinstance(entry, dict) or not is_on_screen(entry):
                        continue
                    name = str(entry.get("name", "")).strip()
                    gaze = str(entry.get("gaze_target", "")).strip()
                    if not name or not gaze or gaze in ("无", "无视线"):
                        continue
                    blocked = gaze_blocked_for(name, entry, frame_text, names)
                    if is_hook and not blocked:
                        pose = str(entry.get("pose", "") or "")
                        if "仰卧" in pose and any(w in frame_text for w in ("洇痕", "暗红", "血迹")):
                            blocked = True
                    if not blocked:
                        continue
                    entry["gaze_target"] = "无"
                    repairs.append(
                        RepairRecord(
                            constraint_name=self.name,
                            shot_index=None if is_hook else shot.get("index", idx),
                            action="rewrite",
                            target=f"frame_layouts.{frame_kind}[{name}].gaze_target",
                            before=gaze,
                            after="无",
                        )
                    )
        return repairs
