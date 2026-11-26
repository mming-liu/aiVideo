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


def _character_lookup(characters: list[dict] | None) -> tuple[dict[str, dict], dict[str, list[str]]]:
    """Index characters by canonical identifier and explicit aliases."""
    try:
        from shared.alias_map import CHARACTER_ALIASES, THREAT_CANONICAL
        threat_synonyms = [
            k for k, v in CHARACTER_ALIASES.items() if v == THREAT_CANONICAL
        ] + [THREAT_CANONICAL]
    except Exception:
        CHARACTER_ALIASES = {}
        THREAT_CANONICAL = ""
        threat_synonyms = []

    by_id: dict[str, dict] = {}
    alias_index: dict[str, list[str]] = {}
    for c in (characters or []):
        if not isinstance(c, dict):
            continue
        cid = str(c.get("identifier") or "").strip()
        if not cid:
            continue
        by_id[cid] = c
        aliases_to_add = list(c.get("aliases") or [])
        if threat_synonyms and (cid == THREAT_CANONICAL or CHARACTER_ALIASES.get(cid) == THREAT_CANONICAL):
            aliases_to_add.extend(threat_synonyms)
        for raw_alias in aliases_to_add:
            alias = str(raw_alias or "").strip()
            if alias and alias != cid:
                alias_index.setdefault(alias, [])
                if cid not in alias_index[alias]:
                    alias_index[alias].append(cid)
    return by_id, alias_index


def _resolve_character(
    raw_name: str,
    chars: dict[str, dict],
    alias_index: dict[str, list[str]],
) -> tuple[str, dict, str, dict | None]:
    """Resolve raw spec name via exact id or unambiguous alias; never guess."""
    if raw_name in chars:
        return raw_name, chars[raw_name], "exact", None
    candidates = alias_index.get(raw_name) or []
    if len(candidates) == 1:
        cid = candidates[0]
        return cid, chars.get(cid) or {}, "alias", None
    if len(candidates) > 1:
        return raw_name, {}, "ambiguous_alias", {
            "code": "ambiguous_character_alias",
            "name": raw_name,
            "candidates": list(candidates),
        }
    return raw_name, {}, "unregistered", None


def _inspect_spec(shot: dict, frame_kind: str, kf_index: int | None = None) -> dict:
    from shared.frame_spec import inspect_frame_spec
    return inspect_frame_spec(shot, frame_kind, kf_index)


def _detect_entity_contradictions(
    cid: str,
    pose: str,
    props: list[str],
    states: list[str] | None,
) -> list[dict]:
    """Detect deterministic state/prop contradictions on one entity without dropping either side."""
    issues: list[dict] = []
    clauses: list[str] = []
    if pose:
        clauses.append(pose)
    if isinstance(states, list):
        clauses.extend(states)
    neg_prefixes = ("没有", "禁止", "未", "无", "非", "不")
    negated: list[tuple[str, str]] = []
    for cl in clauses:
        for pref in neg_prefixes:
            if cl.startswith(pref) and len(cl) > len(pref):
                negated.append((cl, cl[len(pref):].strip()))
                break
    for neg_cl, body in negated:
        if len(body) < 1:
            continue
        for other in clauses:
            if other == neg_cl:
                continue
            if body in other and not any(other.startswith(p) for p in neg_prefixes):
                issues.append({
                    "code": "contradictory_entity_states",
                    "entity": cid,
                    "items": [neg_cl, other],
                })
    bare_hands = any(m in clauses for m in ("徒手", "空手", "双手空空", "未持任何物品"))
    for prop in props:
        if bare_hands:
            issues.append({
                "code": "contradictory_entity_prop",
                "entity": cid,
                "prop": prop,
                "conflict": "徒手/空手",
            })
        for neg_cl, body in negated:
            if prop in neg_cl or (body and body in prop):
                issues.append({
                    "code": "contradictory_entity_prop",
                    "entity": cid,
                    "prop": prop,
                    "conflict": neg_cl,
                })
    return issues


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
    from shared.frame_spec import is_on_screen

    chars, alias_index = _character_lookup(characters)
    inspected = _inspect_spec(shot, frame_kind, kf_index)
    spec_source = inspected["source"]
    spec_status = inspected["status"]
    spec_issues = list(inspected["issues"])
    raw_items = [s for s in inspected["entries"] if isinstance(s, dict)]
    items = [s for s in raw_items if is_on_screen(s)]

    entities = []
    used: set[str] = set()
    for item in items:
        raw_name = str(item.get("name") or "").strip()
        if not raw_name:
            continue
        cid, c, match_kind, alias_issue = _resolve_character(raw_name, chars, alias_index)
        if alias_issue:
            spec_issues.append(alias_issue)
            spec_status = "invalid"
        if cid in used:
            spec_issues.append({
                "code": "duplicate_resolved_entity",
                "name": cid,
                "spec_name": raw_name,
            })
            spec_status = "invalid"
            continue
        used.add(cid)

        raw_props = item.get("props")
        if raw_props is None:
            props: list[str] = []
            props_source = "missing"
        elif isinstance(raw_props, list):
            props = [str(p).strip() for p in raw_props if str(p or "").strip()]
            props_source = f"{spec_source}.props"
        else:
            props = []
            props_source = "invalid"
            spec_issues.append({
                "code": "invalid_props_type",
                "entity": cid,
                "actual_type": type(raw_props).__name__,
            })
            spec_status = "invalid"

        raw_states = item.get("states")
        if "states" in item and raw_states is not None:
            if isinstance(raw_states, list):
                states: list[str] | None = [
                    str(s).strip() for s in raw_states if str(s or "").strip()
                ]
                states_source = f"{spec_source}.states"
            else:
                states = None
                states_source = "invalid"
                spec_status = "invalid"
        else:
            states = None
            states_source = "missing"

        sup = _support(item)
        support_source = f"{spec_source}.support" if sup else "missing"
        pose_text = str(item.get("pose") or "").strip()
        contradictions = _detect_entity_contradictions(cid, pose_text, props, states)
        if contradictions:
            spec_issues.extend(contradictions)
            spec_status = "invalid"
        identity_text = str(c.get("static_features") or "").strip()
        dynamic_text = str(c.get("dynamic_features") or "").strip()

        entities.append({
            "name": cid,
            "spec_name": raw_name,
            "character_id": cid if c else None,
            "identity_match": match_kind,
            "identity": identity_text,
            "current": {
                "position": str(item.get("position") or "").strip(),
                "pose": pose_text,
                "props": props,
                "support": sup,
                "states": states,
            },
            "dynamic_bank": dynamic_text,
            "sources": {
                "entity": f"{spec_source}.on_screen",
                "name": f"{spec_source}.name:{match_kind}",
                "identity": f"characters[{cid}].static_features" if c else "unregistered",
                "current": f"{spec_source}.position/pose/props/support/states",
                "props": props_source,
                "support": support_source,
                "states": states_source,
                "dynamic_bank": f"characters[{cid}].dynamic_features" if c else "unregistered",
            },
        })
    omitted = []
    for name in chars:
        if name and name not in used:
            omitted.append({"name": name, "reason": "off_screen"})
    return {
        "stage": "image_frame",
        "frame_kind": frame_kind,
        "kf_index": kf_index,
        "spec_source": spec_source,
        "spec_status": spec_status,
        "spec_issues": spec_issues,
        "entities": entities,
        "spec_present": bool(raw_items),
        "omitted": omitted,
        "state_contract": {
            "explicit": all(e["current"]["states"] is not None for e in entities) if entities else True,
            "note": "states 缺失时不得用关键词猜时间范围；由上游补合同或显式报告缺口。",
        },
        "support_contract": {
            "explicit": all(bool(e["current"]["support"]) for e in entities) if entities else True,
            "note": "support 缺失时不得从 position/人数推断承载关系。",
        },
    }


def project_video_facts(shot: dict, characters: list[dict] | None = None) -> dict:
    """Aggregate ff/kfs/lf on-screen entities and whole-shot omissions."""
    frames = [("first_frame", None), ("last_frame", None)]
    for i in range(len(shot.get("keyframes") or [])):
        frames.append(("keyframe", i))
    merged: dict[str, dict] = {}
    omitted: dict[str, dict] = {}
    issues: list[dict] = []
    sources: list[str] = []
    any_spec = False
    has_invalid = False
    for kind, kf in frames:
        p = project_frame_facts(shot, kind, characters, kf)
        if p.get("spec_source") and p["spec_source"] != "missing":
            if p["spec_source"] not in sources:
                sources.append(p["spec_source"])
        if p.get("spec_status") == "invalid":
            has_invalid = True
        for issue in p.get("spec_issues") or []:
            issues.append({**issue, "frame_kind": kind, "kf_index": kf})
        if not p.get("spec_present"):
            continue
        any_spec = True
        for e in p.get("entities") or []:
            if e["name"] not in merged:
                merged[e["name"]] = e
        for o in p.get("omitted") or []:
            omitted.setdefault(o["name"], o)
    for name in merged:
        omitted.pop(name, None)
    spec_status = "invalid" if has_invalid else ("valid" if any_spec else "missing")
    return {
        "stage": "video_shot",
        "spec_source": sources[0] if len(sources) == 1 else ("mixed" if sources else "missing"),
        "spec_status": spec_status,
        "spec_issues": issues,
        "entities": list(merged.values()),
        "spec_present": any_spec,
        "omitted": list(omitted.values()),
        "support_contract": {
            "explicit": all(bool(e["current"]["support"]) for e in merged.values()) if merged else True,
            "note": "视频聚合 ff/kfs/lf；任一帧 support 缺失即视为不完整。",
        },
    }


def audit_prompt_against_projection(prompt: str, projection: dict,
                                    characters: list[dict] | None = None) -> list[dict]:
    """Check final prompt for off-screen entity names. No semantic guessing."""
    text = str(prompt or "")
    chars, alias_index = _character_lookup(characters)
    on_screen_ids = {
        str(e.get("character_id") or e.get("name") or "").strip()
        for e in (projection.get("entities") or [])
    }
    problems = []
    for o in projection.get("omitted") or []:
        name = str(o.get("name") or "").strip()
        if not name:
            continue
        char_obj = chars.get(name, {"identifier": name})
        names = [name]
        for a in _alias_names(char_obj, characters or []):
            # Skip shared/ambiguous aliases or aliases belonging to an on-screen entity.
            owners = alias_index.get(a) or []
            if a != name and (len(owners) != 1 or any(owner in on_screen_ids for owner in owners)):
                continue
            if a not in names:
                names.append(a)
        if any(n in text for n in names):
            problems.append({"code": "offscreen_entity_in_prompt", "entity": name,
                             "reason": o.get("reason", "off_screen")})
    return problems
