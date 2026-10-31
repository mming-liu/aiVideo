"""Remove stale, unmarked state clauses from first/last frame text.

The model often appends a second state sentence without the
``【角色锚定】`` marker, for example::

    神秘人已没入水中不见踪影，神秘人位于画左、踞蹲船舷

The structured layout correctly marks the character off-screen, but the prose
still asks the image model to draw a visible pose.  This constraint removes only
clauses whose state conflicts with the current frame layout or with an earlier
clause for the same character.  It does not rewrite the whole description.
"""
from __future__ import annotations

import re

from shared.alias_map import CHARACTER_ALIASES
from step01_script_gen.subject_attribution import leading_named_span
from .base import Constraint
from .types import RepairRecord, Severity


_SIT_WORDS = ("坐", "跪", "盘膝", "踞蹲", "蹲")
_LIE_WORDS = ("仰卧", "躺", "卧倒", "倒地", "倒伏", "平躺")
_VISIBLE_WORDS = ("位于", "立于", "站在", "站立", "站", "持", "握", "举")
_ABSENT_WORDS = ("没入", "不见踪影", "消失", "退场", "离开", "潜回", "沉入", "遁水")
_TRANSITION_WORDS = ("从", "转为", "随后", "继而", "起身", "坐起", "躺下", "倒下", "翻倒")


def _pose_categories(text: str) -> set[str]:
    cats: set[str] = set()
    if any(w in text for w in _SIT_WORDS):
        cats.add("sit")
    if any(w in text for w in _LIE_WORDS):
        cats.add("lie")
    return cats


def _name_variants(name: str) -> set[str]:
    name = str(name or "").strip()
    if not name:
        return set()
    canonical = CHARACTER_ALIASES.get(name, name)
    out = {name, canonical}
    for alias, target in CHARACTER_ALIASES.items():
        if target == canonical:
            out.add(alias)
    return {x for x in out if x}


def _layout_map(shot: dict, frame_kind: str) -> dict[str, dict]:
    layouts = shot.get("frame_layouts")
    out: dict[str, dict] = {}
    if isinstance(layouts, dict):
        for item in layouts.get(frame_kind) or []:
            if not isinstance(item, dict):
                continue
            for variant in _name_variants(item.get("name", "")):
                out[variant] = item
    for item in shot.get("char_layout") or []:
        if isinstance(item, dict):
            for variant in _name_variants(item.get("name", "")):
                out.setdefault(variant, item)
    return out


def _known_names(shot: dict) -> list[str]:
    names: list[str] = []
    for frame_kind in ("ff", "lf"):
        for name in _layout_map(shot, frame_kind):
            if name not in names:
                names.append(name)
    for item in shot.get("char_layout") or []:
        if isinstance(item, dict):
            for variant in _name_variants(item.get("name", "")):
                if variant not in names:
                    names.append(variant)
    return sorted(names, key=len, reverse=True)


def _split_clauses(text: str) -> list[tuple[str, str]]:
    parts = re.split(r"(，|。|；|、|……)", str(text or ""))
    out: list[tuple[str, str]] = []
    for i in range(0, len(parts), 2):
        clause = parts[i].strip()
        delimiter = parts[i + 1] if i + 1 < len(parts) else ""
        if clause:
            out.append((clause, delimiter))
    return out


def _strip_cross_time_reference(text: str) -> str:
    text = str(text or "")
    text = text.replace("（本帧各角色姿态位置见首帧静态定格）", "")
    text = text.replace("(本帧各角色姿态位置见首帧静态定格)", "")
    text = re.sub(r"[。]{2,}", "。", text)
    text = text.replace("，。", "。")
    return text.strip("，。； ")


def _clean_text(text: str, layout: dict[str, dict], names: list[str],
                ff_text: str = "", motion_text: str = "", *, is_lf: bool = False) -> str:
    kept: list[tuple[str, str]] = []
    last_subject = ""
    seen_pose: dict[str, set[str]] = {}

    for clause, delimiter in _split_clauses(text):
        mentioned = [name for name in names if name in clause]
        subject, actor_clause = leading_named_span(clause, names)
        if subject:
            last_subject = subject
        elif not mentioned:
            subject = last_subject
        else:
            last_subject = ""
        # An object/location mention does not identify the actor. Do not carry
        # the previous actor into an ambiguous clause containing another name.

        cats = _pose_categories(actor_clause) if subject else set()
        item = layout.get(subject) if subject else None
        drop = False

        if item and item.get("on_screen") is False:
            visible = bool(cats) or any(w in actor_clause for w in _VISIBLE_WORDS)
            absent = any(w in actor_clause for w in _ABSENT_WORDS)
            if visible and not absent:
                drop = True

        if item and not drop:
            layout_cats = _pose_categories(str(item.get("pose") or ""))
            if (("sit" in layout_cats and "lie" in cats)
                    or ("lie" in layout_cats and "sit" in cats)):
                drop = True

        if subject and cats and not drop and not any(w in actor_clause for w in _TRANSITION_WORDS):
            prior = seen_pose.get(subject)
            if prior and (("sit" in prior and "lie" in cats)
                          or ("lie" in prior and "sit" in cats)):
                drop = True

        if drop:
            continue

        if subject and cats:
            seen_pose[subject] = cats
        kept.append((clause, delimiter))

    if not kept:
        return ""
    out = ""
    for i, (clause, delimiter) in enumerate(kept):
        out += clause
        if i < len(kept) - 1:
            out += delimiter or "，"
    out = _strip_cross_time_reference(out)
    out = re.sub(r"[。]{2,}", "。", out)
    out = out.replace("，。", "。")
    out = out.replace("，画面落定一拍", "").replace("。画面落定一拍。", "。").replace("画面落定一拍", "")
    # Absence from the last motion clause is not a contradiction: other
    # actors and persistent props remain visible. Only the explicit same-actor
    # layout/pose contradictions above authorize removal.
    return out


class FrameTextStateCleanupConstraint(Constraint):
    name = "frame_text_state_cleanup"
    severity = Severity.WARN
    fields = ()

    def apply(self, ctx) -> list[RepairRecord]:
        repairs: list[RepairRecord] = []
        shots = [s for s in (ctx.shots or []) if isinstance(s, dict)]
        by_index = {str(s.get("index")): s for s in shots}
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            names = _known_names(shot)
            if not names:
                continue
            # Half-shots must start from the exact previous LF state.  Re-apply
            # this after all writers so a later anchor pass cannot reintroduce
            # an older FF state.
            try:
                f = float(shot.get("index"))
            except (TypeError, ValueError):
                f = None
            if f is not None and abs(f - round(f)) >= 1e-9:
                prev = by_index.get(str(round(f - 0.5, 4))) or by_index.get(str(int(f)))
                prev_lf = str(prev.get("lf_desc") or "").strip() if isinstance(prev, dict) else ""
                if prev_lf and str(shot.get("ff_desc") or "").strip() != prev_lf:
                    before = str(shot.get("ff_desc") or "")
                    shot["ff_desc"] = prev_lf
                    repairs.append(RepairRecord(
                        constraint_name=self.name,
                        shot_index=None if is_hook else shot.get("index", idx),
                        action="rewrite",
                        target="ff_desc",
                        before=before[-180:],
                        after=prev_lf[-180:],
                        detail="最终阶段再次对齐拆分镜首帧",
                    ))
            for kf_i, kf in enumerate(shot.get("keyframes") or []):
                if not isinstance(kf, dict):
                    continue
                text = str(kf.get("desc") or "")
                cleaned = _strip_cross_time_reference(text).strip("，。； ")
                if cleaned and cleaned != text:
                    kf["desc"] = cleaned
                    repairs.append(RepairRecord(
                        constraint_name=self.name,
                        shot_index=None if is_hook else shot.get("index", idx),
                        action="rewrite",
                        target=f"keyframes[{kf_i}].desc",
                        before=text[-180:],
                        after=cleaned[-180:],
                        detail="删除依赖首帧的跨时间姿态引用",
                    ))
            for field, frame_kind in (("ff_desc", "ff"), ("lf_desc", "lf")):
                text = shot.get(field)
                if not isinstance(text, str) or not text.strip():
                    continue
                cleaned = _clean_text(text, _layout_map(shot, frame_kind), names, ff_text=str(shot.get("ff_desc") or ""), motion_text=str(shot.get("motion_desc") or ""), is_lf=(field == "lf_desc"))
                if cleaned and cleaned != text:
                    shot[field] = cleaned
                    repairs.append(RepairRecord(
                        constraint_name=self.name,
                        shot_index=None if is_hook else shot.get("index", idx),
                        action="rewrite",
                        target=field,
                        before=text[-180:],
                        after=cleaned[-180:],
                        detail="清理与当前帧 layout/前序状态冲突的旧追加分句",
                    ))
        return repairs


__all__ = ["FrameTextStateCleanupConstraint"]
