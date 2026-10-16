"""Prepend a concrete role anchor when the model omitted it."""
from __future__ import annotations

import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from .base import Constraint
from .types import RepairRecord, Severity


_GENERIC_SUBJECTS = ("群演", "人群", "路人", "某男子", "某女子", "主角")


class RoleAnchorConstraint(Constraint):
    """Add `[name: feature]` to prompt and replace generic subjects."""

    name = "role_anchor"
    severity = Severity.WARN
    fields = ("prompt",)

    @staticmethod
    def _anchor_map(ctx) -> dict[str, str]:
        bank: dict[str, object] = dict(ctx.characters_bank or {})
        characters = ctx.script.get("characters", []) if isinstance(ctx.script, dict) else []
        for character in characters:
            if not isinstance(character, dict):
                continue
            name = str(character.get("identifier") or character.get("name") or "").strip()
            if name:
                bank[name] = character

        anchors: dict[str, str] = {}
        for name, character in bank.items():
            if isinstance(character, dict):
                features = (
                    character.get("static_features")
                    or character.get("appearance")
                    or character.get("desc")
                )
            else:
                features = getattr(character, "static_features", None) or getattr(
                    character, "appearance", None
                )
            features = str(features or "").strip()
            if features:
                # Identity descriptions must retain complete source phrases.
                anchors[str(name)] = features
        return anchors

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        anchors = self._anchor_map(ctx)
        if not anchors:
            return repairs

        names = sorted(anchors, key=len, reverse=True)
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            subject = str(shot.get("main_subject") or "")
            keywords = shot.get("core_keywords")
            if isinstance(keywords, list):
                keywords = ",".join(str(k) for k in keywords)
            blob = "\n".join(
                [
                    subject,
                    str(keywords or ""),
                    str(shot.get("narration_segment") or ""),
                    str(shot.get("subtitle") or ""),
                ]
            )
            name = next((n for n in names if n in blob), None)
            if not name:
                continue
            features = anchors[name]
            prompt = str(shot.get("prompt") or "")
            if not re.search(rf"\[{re.escape(name)}[:：]", prompt):
                anchor = f"[{name}: {features}]"
                shot["prompt"] = f"{anchor} {prompt}".strip()
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=shot.get("index", idx),
                        action="prepend",
                        target="prompt",
                        before=prompt,
                        after=shot["prompt"],
                    )
                )
            generic_subject = subject and name not in subject and any(
                token in subject for token in _GENERIC_SUBJECTS
            )
            if generic_subject:
                subject_before = subject
                shot["main_subject"] = f"{name}，{subject}"
                repairs.append(
                    RepairRecord(
                        constraint_name=self.name,
                        shot_index=shot.get("index", idx),
                        action="rewrite",
                        target="main_subject",
                        before=subject_before,
                        after=shot["main_subject"],
                    )
                )
        return repairs
