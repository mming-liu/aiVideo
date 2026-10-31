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

    @staticmethod
    def _on_screen_names(shot: dict) -> "tuple[set, bool]":
        """本镜**出镜**人物名（来自 frame_layouts；有布局才算“可判定”）.

        2026-10-28（M06/原意见 13）：角色只在旁白/字幕里被**提到**，不等于本镜
        出镜。旧实现把 "主语+关键词+台词" 里出现的名字都加完整外观段，于是
        "听香是杨旭买来的" 这种画外语义也会给杨旭加出镜外观段。布局明确时按
        on_screen 条目判定；没有布局数据时返回 `present=False`，调用方保留旧行为。
        """
        layouts = shot.get("frame_layouts")
        names: set = set()
        present = False
        if isinstance(layouts, dict):
            frames: list = []
            for key in ("ff", "lf"):
                value = layouts.get(key)
                if isinstance(value, list):
                    frames.append(value)
                    present = True
            kfs = layouts.get("kfs")
            if isinstance(kfs, list):
                for frame in kfs:
                    if isinstance(frame, list):
                        frames.append(frame)
                        present = True
            for frame in frames:
                for item in frame:
                    if isinstance(item, dict) and item.get("on_screen") is not False:
                        name = str(item.get("name") or "").strip()
                        if name:
                            names.add(name)
        return names, present

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
            candidates = [n for n in names if n in blob]
            on_screen, has_layout = self._on_screen_names(shot)
            if has_layout:
                # 出镜判定：布局条目 ∪ 本镜主语。仅被台词/旁白提到的人物不建外观段。
                candidates = [n for n in candidates if n in on_screen or n in subject]
            name = candidates[0] if candidates else None
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
