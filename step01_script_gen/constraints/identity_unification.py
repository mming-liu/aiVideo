"""Canonicalize character aliases and rebuild char_layout from frame specs.

Threat aliases (刺客/神秘人/黑衣人...) often appear as separate frame_layout
entries for the same person.  Collective placeholders such as 群演 can also be
mistaken for the main role.  This constraint unifies identities *before*
anchor/roster consumers run, merges duplicate frame entries, and rebuilds
char_layout from the canonical ff roster.
"""
from __future__ import annotations

from shared.frame_spec import is_on_screen

from .base import Constraint
from .types import RepairRecord, Severity, Violation


_THREAT_ALIASES = ("神秘人", "黑衣人", "杀手", "蒙面人", "蒙面黑影", "黑影")
_COLLECTIVES = ("群演", "众佃仆", "佃仆", "庄仆", "村民", "路人")


def _canonical(name: str, identifiers: set[str]) -> str:
    name = str(name or "").strip()
    if not name:
        return name
    if name in identifiers:
        return name
    threat_idents = {
        ident for ident in identifiers
        if any(ident == a or ident.startswith(a) for a in _THREAT_ALIASES)
        or ident in ("刺客", "神秘人", "杀手", "黑衣人")
    }
    # Ambiguous threat rosters must not be collapsed by generic aliases.
    if len(threat_idents) > 1:
        return name
    if name in _THREAT_ALIASES:
        for candidate in ("刺客", "神秘人", "杀手", "黑衣人"):
            if candidate in identifiers:
                return candidate
    return name


def _merge_entry(dst: dict, src: dict) -> list[str]:
    conflicts = []
    for key in ("position", "gaze_target", "surface"):
        if not str(dst.get(key) or "").strip() and str(src.get(key) or "").strip():
            dst[key] = src[key]
        elif (str(dst.get(key) or "").strip() and str(src.get(key) or "").strip()
              and str(dst.get(key)) != str(src.get(key))):
            conflicts.append(key)
    for key in ("pose", "facing"):
        if len(str(src.get(key) or "")) > len(str(dst.get(key) or "")):
            dst[key] = src[key]
    props = list(dst.get("props") or []) + list(src.get("props") or [])
    dst["props"] = list(dict.fromkeys(str(p) for p in props if str(p).strip()))
    if is_on_screen(dst) != is_on_screen(src):
        # Prefer the entry with stronger visible evidence; do not OR-guess.
        dst_strength = len(str(dst.get("pose") or "")) + len(dst.get("props") or [])
        src_strength = len(str(src.get("pose") or "")) + len(src.get("props") or [])
        if src_strength > dst_strength:
            dst["on_screen"] = is_on_screen(src)
        conflicts.append("on_screen")
    return conflicts


class IdentityUnificationConstraint(Constraint):
    name = "identity_unification"
    severity = Severity.HARD

    def apply(self, ctx) -> list[RepairRecord]:
        identifiers = {
            str(c.get("identifier") or "").strip()
            for c in (ctx.script.get("characters") or [])
            if isinstance(c, dict) and str(c.get("identifier") or "").strip()
        }
        repairs: list[RepairRecord] = []
        for shot, is_hook, idx in self.iter_shots_and_hook(ctx):
            layouts = shot.get("frame_layouts")
            if not isinstance(layouts, dict):
                continue
            old_names = []
            for bucket in ("ff", "lf"):
                old_names.extend(
                    str(e.get("name") or "")
                    for e in (layouts.get(bucket) or [])
                    if isinstance(e, dict)
                )
            for kfs in (layouts.get("kfs") or []):
                if isinstance(kfs, list):
                    old_names.extend(
                        str(e.get("name") or "")
                        for e in kfs if isinstance(e, dict)
                    )
            changed_names = False
            for bucket in ("ff", "lf"):
                entries = layouts.get(bucket)
                if not isinstance(entries, list):
                    continue
                merged: dict[str, dict] = {}
                order: list[str] = []
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    canon = _canonical(entry.get("name", ""), identifiers)
                    if not canon:
                        continue
                    item = dict(entry)
                    item["name"] = canon
                    if canon in merged:
                        _conflicts = _merge_entry(merged[canon], item)
                        if _conflicts:
                            ctx.record_violation(Violation(
                                constraint_name=self.name,
                                severity=Severity.WARN,
                                shot_index=None if is_hook else shot.get("index", idx),
                                field="frame_layouts",
                                message=(
                                    f"身份 {canon} 合并字段冲突：{_conflicts}；"
                                    "已保留证据更强项，未拼接冲突字段"
                                ),
                                repaired=False,
                            ))
                    else:
                        merged[canon] = item
                        order.append(canon)
                named = [n for n in order if n not in _COLLECTIVES]
                cleaned = []
                for name in order:
                    item = merged[name]
                    if name in _COLLECTIVES:
                        pose = str(item.get("pose") or "").strip()
                        if len(named) >= 2 and (not pose or pose == name or item.get("props") in (None, [])):
                            changed_names = True
                            continue
                    cleaned.append(item)
                layouts[bucket] = cleaned
            kfs = layouts.get("kfs")
            if isinstance(kfs, list):
                new_kfs = []
                for spec in kfs:
                    if not isinstance(spec, list):
                        new_kfs.append(spec)
                        continue
                    merged: dict[str, dict] = {}
                    order: list[str] = []
                    for entry in spec:
                        if not isinstance(entry, dict):
                            continue
                        canon = _canonical(entry.get("name", ""), identifiers)
                        if not canon:
                            continue
                        item = dict(entry)
                        item["name"] = canon
                        if canon in merged:
                            _conflicts = _merge_entry(merged[canon], item)
                            if _conflicts:
                                ctx.record_violation(Violation(
                                    constraint_name=self.name,
                                    severity=Severity.WARN,
                                    shot_index=None if is_hook else shot.get("index", idx),
                                    field="frame_layouts.kfs",
                                    message=(
                                        f"身份 {canon} 合并字段冲突：{_conflicts}；"
                                        "已保留证据更强项，未拼接冲突字段"
                                    ),
                                    repaired=False,
                                ))
                        else:
                            merged[canon] = item
                            order.append(canon)
                    named = [n for n in order if n not in _COLLECTIVES]
                    cleaned = []
                    for name in order:
                        item = merged[name]
                        if name in _COLLECTIVES:
                            pose = str(item.get("pose") or "").strip()
                            if len(named) >= 2 and (not pose or pose == name or item.get("props") in (None, [])):
                                changed_names = True
                                continue
                        cleaned.append(item)
                    new_kfs.append(cleaned)
                layouts["kfs"] = new_kfs
            if old_names and changed_names:
                repairs.append(RepairRecord(
                    constraint_name=self.name,
                    shot_index=None if is_hook else shot.get("index", idx),
                    action="rewrite",
                    target="frame_layouts",
                    detail="统一威胁别名并移除无依据群演占位",
                ))
            # Rebuild legacy char_layout from canonical ff roster.
            ff_on = [e for e in (layouts.get("ff") or []) if isinstance(e, dict) and is_on_screen(e)]
            if ff_on:
                old_layout = [e for e in (shot.get("char_layout") or []) if isinstance(e, dict)]
                old_by_name = {str(e.get("name") or ""): e for e in old_layout}
                rebuilt = []
                for i, entry in enumerate(ff_on):
                    name = str(entry.get("name") or "").strip()
                    if not name:
                        continue
                    old = old_by_name.get(name) or {}
                    rebuilt.append({
                        "name": name,
                        "position": entry.get("position") or old.get("position") or "画中",
                        "facing": entry.get("pose") or old.get("facing") or "",
                        "role": "主" if i == 0 else "次",
                        "surface": old.get("surface") or entry.get("surface") or "",
                    })
                if rebuilt != old_layout:
                    shot["char_layout"] = rebuilt
            main_subject = str(shot.get("main_subject") or "")
            if main_subject:
                new_main = main_subject
                for alias in _THREAT_ALIASES:
                    new_main = new_main.replace(alias, _canonical(alias, identifiers))
                if any(x in new_main for x in _COLLECTIVES) and (layouts.get("ff") or []):
                    first = next((str(e.get("name") or "") for e in layouts["ff"]
                                  if isinstance(e, dict) and str(e.get("name") or "") not in _COLLECTIVES), "")
                    if first:
                        new_main = first
                shot["main_subject"] = new_main
        return repairs


__all__ = ["IdentityUnificationConstraint"]