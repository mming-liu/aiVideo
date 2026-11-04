"""Stage fact projection for prompt assembly (G01).

This module selects what the current task is allowed to know and records
where each fact came from. It does not call a model and does not write prompts.
"""
from __future__ import annotations


def _alias_names(character: dict, characters: list[dict]) -> list[str]:
    name = str(character.get("identifier") or "").strip()
    if not name:
        return []
    aliases = [name]
    for a in character.get("aliases") or []:
        a = str(a).strip()
        if a and a not in aliases:
            aliases.append(a)
    return aliases


def _spec_items(shot: dict, frame_kind: str, kf_index: int | None = None):
    try:
        from shared.frame_spec import get_frame_spec, is_on_screen
        return [s for s in get_frame_spec(shot, frame_kind, kf_index)
                if isinstance(s, dict) and is_on_screen(s)]
    except Exception:
        return []


def _character_map(characters: list[dict] | None) -> dict[str, dict]:
    return {str(c.get("identifier") or "").strip(): c
            for c in (characters or []) if isinstance(c, dict)}


def _support(item: dict) -> dict:
    sup = item.get("support")
    if isinstance(sup, dict):
        return {k: str(sup.get(k) or "").strip()
                for k in ("id", "kind", "surface") if str(sup.get(k) or "").strip()}
    out = {}
    for src, dst in (("support_id", "id"), ("support_kind", "kind"),
                     ("support_surface", "surface")):
        v = str(item.get(src) or "").strip()
        if v:
            out[dst] = v
    return out


def project_frame_facts(shot: dict, frame_kind: str,
                        characters: list[dict] | None = None,
                        kf_index: int | None = None) -> dict:
    """Project one frame's allowed facts with provenance and omissions.

    Only on-screen spec entities enter the entities list. Characters that exist
    in the chapter bank but are not on this frame are recorded as omitted.
    State/time selection stays explicit: states is copied as a contract field;
    this function does not guess time scope from words.
    """
    chars = _character_map(characters)
    items = _spec_items(shot, frame_kind, kf_index)
    entities = []
    used: set[str] = set()
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        used.add(name)
        c = chars.get(name) or {}
        entities.append({
            "name": name,
            "identity": str(c.get("static_features") or "").strip(),
            "current": {
                "position": str(item.get("position") or "").strip(),
                "pose": str(item.get("pose") or "").strip(),
                "props": [str(p).strip() for p in (item.get("props") or []) if str(p).strip()],
                "support": _support(item),
                "states": list(item.get("states") or []) if isinstance(item.get("states"), list) else None,
            },
            "dynamic_bank": str(c.get("dynamic_features") or "").strip(),
            "sources": {
                "entity": "frame_layouts.on_screen",
                "identity": f"characters[{name}].static_features",
                "current": "frame_layouts.position/pose/props/support/states",
                "dynamic_bank": f"characters[{name}].dynamic_features",
            },
        })
    omitted = []
    for name, c in chars.items():
        if name and name not in used:
            omitted.append({"name": name, "reason": "off_screen"})
    return {
        "stage": "image_frame",
        "frame_kind": frame_kind,
        "entities": entities,
        "omitted": omitted,
        "state_contract": {
            "explicit": all(e["current"]["states"] is not None for e in entities) if entities else True,
            "note": "states 缺失时不得用关键词猜时间范围；由上游补合同或显式报告缺口。",
        },
    }


def audit_prompt_against_projection(prompt: str, projection: dict,
                                    characters: list[dict] | None = None) -> list[dict]:
    """Check final prompt for off-screen entity names. No semantic guessing."""
    text = str(prompt or "")
    chars = _character_map(characters)
    problems = []
    for o in projection.get("omitted") or []:
        name = str(o.get("name") or "").strip()
        if not name:
            continue
        names = _alias_names(chars.get(name, {"identifier": name}), characters or [])
        if any(n in text for n in names):
            problems.append({"code": "offscreen_entity_in_prompt", "entity": name,
                             "reason": o.get("reason", "off_screen")})
    return problems
