"""Stage fact projection for prompt assembly (G01).

This module selects what the current task is allowed to know and records
where each fact came from. It does not call a model and does not write prompts.
"""
from __future__ import annotations

import re


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


_PROP_ABSENCE_RE_TMPL = (
    r"(?:^|[，,、\s])(?:没有|无|不见|未见|丢失|已无|缺少|缺乏|不存在)\s*(?:任何|携带|佩戴)?{pat}(?:$|[，,、\s]|的)"
)
_PROP_HELD_NEG_WORDS = ("徒手", "空手", "双手空空", "双手空着", "未持任何物品", "双手不持任何物品", "两手空空")
_PROP_HELD_VERBS = ("手持", "手握", "握着", "握持", "拿", "拿着", "捏", "捏着", "擎", "擎着", "拎", "拎着", "托", "托着", "捧", "捧着", "举着", "握", "持")
_PROP_WORN_VERBS = ("佩戴", "佩带", "佩", "腰间佩", "腰悬", "挂着", "挂有", "别着", "背着", "背负", "身背", "斜挎", "戴着", "放在", "置于", "摆在", "留在", "带在")
_PROP_ACTIVE_NEG_PREFIXES = ("未拔", "没拔", "未出鞘", "尚未拔", "未挥", "没有挥", "尚未使用", "未使用", "未启用", "未打开", "未点亮", "未触发")
_PROP_ACTIVE_NEG_SUFFIXES = ("未拔", "未出鞘", "尚未使用", "入鞘", "合上", "关闭", "熄灭", "未开启", "未亮")
_PROP_ACTIVE_POS_VERBS = ("拔出", "抽出", "挥舞", "挥动", "劈砍", "刺向", "刺出", "拉开", "点燃", "点亮", "开启", "打开")
_TEMPORAL_SHIFT_MARKERS = ("随后", "接着", "然后", "起初", "先前", "之后", "后来", "原本")

_GENERIC_HEAD_SUFFIXES = ("剑", "刀", "刃", "枪", "棒", "棍", "鞭", "弓", "弩", "斧", "锤", "戟", "灯", "扇", "伞", "杯", "钟", "铃", "佩")


def _prop_terms_for_match(prop: str) -> list[str]:
    terms = [prop]
    if len(prop) >= 2 and prop[-1] in _GENERIC_HEAD_SUFFIXES:
        terms.append(prop[-1])
    return terms


def _has_negation_before(clause: str, idx: int) -> bool:
    prefix = clause[max(0, idx - 3):idx]
    return any(w in prefix for w in ("未", "不", "没", "无", "非", "莫", "别", "勿"))


def _split_temporal_clause(clause: str) -> bool:
    hits = sum(1 for m in _TEMPORAL_SHIFT_MARKERS if m in clause)
    return hits >= 1 and any(sep in clause for sep in ("，", ",", "；", ";"))


def _classify_prop_clause(clause: str, prop: str) -> list[tuple[str, str]]:
    """Classify one clause into layered prop polarity events: (layer, '+' | '-')."""
    events: list[tuple[str, str]] = []
    if not clause or _split_temporal_clause(clause):
        return events
    if clause in _PROP_HELD_NEG_WORDS or any(w in clause for w in _PROP_HELD_NEG_WORDS):
        events.append(("held", "-"))

    terms = _prop_terms_for_match(prop)
    matched_any = False
    for term in terms:
        esc = re.escape(term)
        exact_only = term == prop

        # 1) Whole-prop absence vs presence
        # Check hands-scoped negative first so 「手中无剑」 stays in held, not present!
        hand_neg_pats = (
            rf"手中无\s*{esc}", rf"手里无\s*{esc}", rf"双手(?:未|不|无|没)(?:持|握|拿)\s*{esc}",
            rf"(?:未|不|没)(?:持|握|拿)(?:着|有)?\s*{esc}",
        )
        is_hand_neg = any(re.search(pat, clause) for pat in hand_neg_pats)
        if is_hand_neg:
            events.append(("held", "-"))
            matched_any = True

        # Active use negatives / positives (e.g. 未拔出长剑 vs 拔出长剑)
        for neg_pref in _PROP_ACTIVE_NEG_PREFIXES:
            if re.search(rf"{neg_pref}(?:出|着|了)?[^，。；]{{0,3}}{esc}", clause):
                events.append(("active_use", "-"))
                matched_any = True
        for neg_suf in _PROP_ACTIVE_NEG_SUFFIXES:
            if re.search(rf"{esc}(?:已|仍|均)?{neg_suf}", clause):
                events.append(("active_use", "-"))
                matched_any = True
        for act_v in _PROP_ACTIVE_POS_VERBS:
            m = re.search(rf"{act_v}(?:了|着)?[^，。；]{{0,3}}{esc}", clause)
            if m and not _has_negation_before(clause, m.start()):
                events.append(("active_use", "+"))
                matched_any = True

        # Held positives (only if not negated before verb)
        for hold_v in _PROP_HELD_VERBS:
            for m in re.finditer(rf"{hold_v}(?:了|着|有)?[^，。；]{{0,3}}{esc}", clause):
                if not _has_negation_before(clause, m.start()):
                    events.append(("held", "+"))
                    matched_any = True

        # Worn / placed / present positives
        for worn_v in _PROP_WORN_VERBS:
            for m in re.finditer(rf"{worn_v}(?:了|着|有)?[^，。；]{{0,3}}{esc}", clause):
                if not _has_negation_before(clause, m.start()):
                    events.append(("present", "+"))
                    matched_any = True

        # Explicit present absence (only when NOT a held-only or active-use-only phrase)
        if not is_hand_neg and not any(ly in ("active_use", "held") for ly, _ in events):
            if re.search(_PROP_ABSENCE_RE_TMPL.format(pat=esc), clause) or (
                exact_only and re.search(rf"^{esc}(?:已)?(?:消失|不见|不在场|不存在)", clause)
            ):
                events.append(("present", "-"))
                matched_any = True

    if not matched_any and prop in clause:
        # Unresolved negative mention naming the prop (neither clearly absent nor clearly held/active)
        if any(clause.startswith(p) for p in ("没有", "禁止", "未", "无", "非", "不")):
            events.append(("unknown_neg", "?"))
    # Deduplicate preserving order
    return list(dict.fromkeys(events))


def _detect_entity_contradictions(
    cid: str,
    pose: str,
    props: list[str],
    states: list[str] | None,
    *,
    advisories: list[dict] | None = None,
) -> list[dict]:
    """Detect deterministic same-entity, same-moment, same-layer contradictions."""
    issues: list[dict] = []
    clauses: list[str] = []
    if pose:
        clauses.append(pose)
    if isinstance(states, list):
        clauses.extend(states)
    neg_prefixes = ("没有", "禁止", "未", "无", "非", "不")

    prop_names = [str(p).strip() for p in (props or []) if str(p or "").strip()]
    prop_rel_terms = {t for p in prop_names for t in _prop_terms_for_match(p)}

    # Non-prop / same-clause state contradiction check (skip clauses strictly differing by prop layers).
    negated: list[tuple[str, str]] = []
    for cl in clauses:
        if _split_temporal_clause(cl):
            continue
        for pref in neg_prefixes:
            if cl.startswith(pref) and len(cl) > len(pref):
                negated.append((cl, cl[len(pref):].strip()))
                break
    for neg_cl, body in negated:
        if len(body) < 1:
            continue
        for other in clauses:
            if other == neg_cl or _split_temporal_clause(other):
                continue
            if any(other.startswith(p) for p in neg_prefixes):
                continue
            if body in other:
                # If both clauses concern a tracked prop, only flag when layered check confirms same layer.
                if any(t in neg_cl and t in other for t in prop_rel_terms):
                    continue
                issues.append({
                    "code": "contradictory_entity_states",
                    "entity": cid,
                    "items": [neg_cl, other],
                })

    # Layered prop contradiction check:
    # - present/worn vs -present -> conflict
    # - +held vs -held -> conflict (NOT props/present vs -held!)
    # - +active_use vs -active_use -> conflict (NOT props/present vs -active_use!)
    seen_prop_conflicts: set[tuple[str, str]] = set()
    for prop in prop_names:
        layer_sources: dict[tuple[str, str], list[str]] = {
            ("present", "+"): ["props:" + prop],
            ("present", "-"): [],
            ("held", "+"): [],
            ("held", "-"): [],
            ("active_use", "+"): [],
            ("active_use", "-"): [],
        }
        unresolved_clauses: list[str] = []
        for cl in clauses:
            for layer, pol in _classify_prop_clause(cl, prop):
                if pol == "?":
                    unresolved_clauses.append(cl)
                elif (layer, pol) in layer_sources:
                    layer_sources[(layer, pol)].append(cl)

        for layer in ("present", "held", "active_use"):
            pos_srcs = layer_sources[(layer, "+")]
            neg_srcs = layer_sources[(layer, "-")]
            if pos_srcs and neg_srcs:
                neg_label = neg_srcs[0]
                key = (prop, f"{layer}:{neg_label}")
                if key not in seen_prop_conflicts:
                    seen_prop_conflicts.add(key)
                    issues.append({
                        "code": "contradictory_entity_prop",
                        "entity": cid,
                        "prop": prop,
                        "layer": layer,
                        "conflict": neg_label,
                    })
                # Also record state-level pair when both conflicting items are explicit text clauses
                pos_text = [s for s in pos_srcs if not s.startswith("props:")]
                if pos_text and neg_srcs:
                    pair_items = [neg_srcs[0], pos_text[0]]
                    if not any(
                        it.get("code") == "contradictory_entity_states"
                        and set(it.get("items") or []) == set(pair_items)
                        for it in issues
                    ):
                        issues.append({
                            "code": "contradictory_entity_states",
                            "entity": cid,
                            "items": pair_items,
                        })

        if advisories is not None:
            for ucl in unresolved_clauses:
                advisories.append({
                    "code": "unverified_relation",
                    "entity": cid,
                    "prop": prop,
                    "detail": ucl,
                })
            # Bare-hands/empty-hands with implicit prop (no worn/held confirmation either way) -> advisory only
            if (
                layer_sources[("held", "-")]
                and not layer_sources[("held", "+")]
                and not [s for s in layer_sources[("present", "+")] if not s.startswith("props:")]
                and not issues
            ):
                advisories.append({
                    "code": "unverified_relation",
                    "entity": cid,
                    "prop": prop,
                    "detail": layer_sources[("held", "-")][0],
                })
    return issues


_TEXT_CARRIER_WORDS = (
    "告示牌", "公告栏", "指示牌", "路牌", "门牌", "站牌", "布告", "告示",
    "榜文", "通缉令", "牌匾", "匾额", "招牌", "木牌", "石碑", "碑文",
    "墓碑", "横幅", "标语", "锦旗", "条幅", "对联", "春联", "铭牌",
    "信纸", "信封", "书信", "信件", "纸条", "字条", "纸张", "纸页",
    "纸上", "纸面", "纸", "文书", "契约", "账本", "账册", "书页",
    "封面", "封皮", "卷轴", "竹简", "圣旨", "请帖", "名片", "标签",
    "价签", "吊牌", "液晶屏", "显示屏", "显示器", "电脑屏幕", "手机屏幕",
    "监控屏", "字幕屏", "屏幕", "平板", "仪表盘", "钟面", "表盘",
    "计时器", "黑板", "白板", "墙面", "墙上", "门板", "门上", "窗上",
    "旗帜", "旗面",
)
_TEXT_CARRIER_ALT = "|".join(sorted(_TEXT_CARRIER_WORDS, key=len, reverse=True))
_TEXT_QUOTED_RE = re.compile(
    rf"({_TEXT_CARRIER_ALT})(?:[上中里面边旁处]|\s)*"
    rf"(?:赫然|清晰|醒目)?(?:写着|写有|印着|刻着|题着|挂着|显示着|显示|浮现|标注着|标着|写|印|刻|题)?"
    rf"[：:\s]*[“\"「『《]([^”\"」』》\n]{{1,40}})[”\"」』》]"
)
_TEXT_DISPLAY_UNQUOTED_RE = re.compile(
    rf"({_TEXT_CARRIER_ALT})(?:[上中里面边旁处])?\s*"
    rf"(?:赫然|清晰|醒目)?(?:写着|写有|印着|刻着|题着|显示着|显示|标注着|标着)"
    rf"[：:\s]*([^\s，。；！？、,\.;!?“\"」』》]{{1,20}})"
)
_DIALOGUE_QUOTED_RE = re.compile(
    r"(?:说道|问道|答道|喝道|叹道|念道|叫道|喊道|回应道|低声道|厉声道|冷笑道|轻声道|笑着道|"
    r"(?:(?<![街巷路楼馆廊桥栈车])道)|说|问|喊|叫|喝问|回应|喃喃|口中念)"
    r"[：:，,\s]*[“\"「『][^”\"」』\n]{0,200}[”\"」』]"
)
_TEXT_POS_HINTS = (
    "画左", "画右", "左侧", "右侧", "中央", "正中", "上方", "下方",
    "墙上", "墙面", "门上", "桌面", "案上", "手中", "纸上", "屏幕", "钟面", "表盘",
)
_TEXT_UNQUOTED_STOP = frozenset({
    "无", "没有", "空白", "空着", "正常", "清晰", "模糊", "字", "文字", "字迹", "字样",
    "信息", "内容", "画面", "着", "出", "出来", "着字", "了", "着呢",
})


def _strip_dialogue_quotes(text: str) -> str:
    return _DIALOGUE_QUOTED_RE.sub("", str(text or ""))


def _infer_text_position(clause: str, carrier: str) -> str:
    for pos in _TEXT_POS_HINTS:
        if pos in clause:
            return pos
    return carrier


def extract_frame_text_spec(
    shot: dict | None,
    frame_kind: str,
    kf_index: int | None = None,
) -> dict:
    """Project provenance-tracked diegetic on-screen text contract for one frame.

    Strictly inspects ONLY visual fields (never narration/dialogue/subtitle fields),
    strips speech-verb dialogue quotes inside visual prose, and keeps ``mode``
    conservative when implicit diegetic text cannot be ruled out.
    """
    if not isinstance(shot, dict):
        return {
            "mode": "conservative",
            "allowed_texts": [],
            "uncertain_carriers": [],
            "unconstrained_carriers": [],
        }

    fk = {"ff": "first_frame", "lf": "last_frame", "kf": "keyframe"}.get(
        str(frame_kind or "").strip(), str(frame_kind or "").strip()
    )
    allowed: list[dict] = []
    seen_entries: set[tuple[str, str]] = set()
    uncertain: list[str] = []
    unconstrained: list[dict] = []
    seen_unconstrained: set[tuple[str, str]] = set()
    explicit_mode = ""

    def _add_unconstrained(*, source: str, carrier: str, position: str) -> None:
        raw_c = str(carrier or "").strip()
        c = raw_c[:-1] if len(raw_c) >= 2 and raw_c.endswith(("上", "中", "里")) else raw_c
        p = str(position or raw_c or c or "").strip()
        if not c:
            return
        if c not in uncertain:
            uncertain.append(c)
        uk = (c, p)
        if uk in seen_unconstrained:
            return
        seen_unconstrained.add(uk)
        unconstrained.append({
            "source": source,
            "carrier": c,
            "position": p,
            "exact_text": None,
            "text_known": False,
        })

    def _add_item(*, source: str, carrier: str, position: str, exact_text: str | None) -> None:
        raw_c = str(carrier or "").strip()
        c = raw_c[:-1] if len(raw_c) >= 2 and raw_c.endswith(("上", "中", "里")) else raw_c
        p = str(position or raw_c or c or "").strip()
        txt = str(exact_text).strip() if exact_text is not None else ""
        if not txt:
            if c:
                _add_unconstrained(source=source, carrier=c, position=p)
            return
        k = (c, txt)
        if k in seen_entries:
            return
        seen_entries.add(k)
        allowed.append({
            "source": source,
            "carrier": c,
            "position": p,
            "exact_text": txt,
            "text_known": True,
        })

    # 1. Explicit structured shot["frame_text_spec"] (frame-specific or shared dict/list)
    raw_spec = shot.get("frame_text_spec")
    spec_node = None
    if isinstance(raw_spec, dict):
        slot_key = {"first_frame": "ff", "last_frame": "lf"}.get(fk)
        if fk == "keyframe" and kf_index is not None and isinstance(raw_spec.get("kfs"), list):
            kfs_node = raw_spec["kfs"]
            if 0 <= kf_index < len(kfs_node):
                spec_node = kfs_node[kf_index]
        elif slot_key and slot_key in raw_spec:
            spec_node = raw_spec.get(slot_key)
        elif fk in raw_spec:
            spec_node = raw_spec.get(fk)
        elif any(k in raw_spec for k in ("mode", "allowed_texts", "forbid_all")):
            spec_node = raw_spec
    elif isinstance(raw_spec, list):
        spec_node = {"allowed_texts": raw_spec}

    if isinstance(spec_node, dict):
        raw_mode = str(spec_node.get("mode") or "").strip().lower()
        if spec_node.get("forbid_all") or raw_mode in ("forbid_all", "strict_none", "explicit_none"):
            explicit_mode = "forbid_all"
        elif raw_mode in ("explicit_diegetic", "conservative"):
            explicit_mode = raw_mode
        for entry in (spec_node.get("allowed_texts") or []):
            if isinstance(entry, dict):
                _add_item(
                    source=str(entry.get("source") or "frame_text_spec"),
                    carrier=str(entry.get("carrier") or "载体"),
                    position=str(entry.get("position") or entry.get("carrier") or ""),
                    exact_text=str(entry.get("exact_text") or entry.get("text") or ""),
                )
    elif isinstance(spec_node, list):
        for entry in spec_node:
            if isinstance(entry, dict):
                _add_item(
                    source=str(entry.get("source") or "frame_text_spec"),
                    carrier=str(entry.get("carrier") or "载体"),
                    position=str(entry.get("position") or entry.get("carrier") or ""),
                    exact_text=str(entry.get("exact_text") or entry.get("text") or ""),
                )

    # 2. Frame-scoped visual fields ONLY (explicitly excluding narration/dialogue/subtitle)
    visual_sources: list[tuple[str, str]] = []
    if fk == "first_frame":
        txt = str(shot.get("ff_desc") or "").strip() or str(shot.get("prompt") or "").strip()
        if txt:
            visual_sources.append(("shot.ff_desc" if shot.get("ff_desc") else "shot.prompt", txt))
    elif fk == "last_frame":
        txt = str(shot.get("lf_desc") or "").strip() or str(shot.get("prompt") or "").strip()
        if txt:
            visual_sources.append(("shot.lf_desc" if shot.get("lf_desc") else "shot.prompt", txt))
    elif fk == "keyframe":
        kfs = shot.get("keyframes") or []
        kf_txt = ""
        if kf_index is not None and 0 <= kf_index < len(kfs) and isinstance(kfs[kf_index], dict):
            kf_txt = str(kfs[kf_index].get("desc") or "").strip()
        if kf_txt:
            visual_sources.append((f"shot.keyframes[{kf_index}].desc", kf_txt))
        elif shot.get("prompt"):
            visual_sources.append(("shot.prompt", str(shot.get("prompt") or "").strip()))

    if shot.get("visible_text") or shot.get("on_screen_text"):
        v_txt = str(shot.get("visible_text") or shot.get("on_screen_text") or "").strip()
        _add_item(source="shot.visible_text", carrier="画面载体", position="画面", exact_text=v_txt)

    # Frame layout on-screen props/states/visible_text
    inspected = _inspect_spec(shot, fk, kf_index)
    for ent_idx, item in enumerate(inspected.get("entries") or []):
        if not isinstance(item, dict):
            continue
        if item.get("on_screen") is False:
            continue
        for k_field in ("visible_text", "on_screen_text", "text"):
            val = str(item.get(k_field) or "").strip()
            if val:
                _add_item(
                    source=f"frame_layouts[{ent_idx}].{k_field}",
                    carrier=str((item.get("props") or ["画面载体"])[0] if isinstance(item.get("props"), list) and item.get("props") else "画面载体"),
                    position=str(item.get("position") or "人物附近"),
                    exact_text=val,
                )
        for fld in ("pose", "props", "states"):
            raw_v = item.get(fld)
            joined = " ".join(str(x) for x in raw_v) if isinstance(raw_v, list) else str(raw_v or "")
            if joined.strip():
                visual_sources.append((f"frame_layouts.{fld}", joined.strip()))

    kw = shot.get("core_keywords") or []
    kw_txt = "、".join(str(x) for x in kw) if isinstance(kw, list) else str(kw or "")
    if kw_txt.strip():
        visual_sources.append(("shot.core_keywords", kw_txt.strip()))

    for src_name, raw_text in visual_sources:
        cleaned = _strip_dialogue_quotes(raw_text)
        if not cleaned:
            continue
        for clause in re.split(r"[。；;!\n]+", cleaned):
            cl = clause.strip()
            if not cl:
                continue
            matched_here = False
            for m in _TEXT_QUOTED_RE.finditer(cl):
                carrier = m.group(1)
                exact = m.group(2).strip()
                if exact:
                    _add_item(
                        source=src_name,
                        carrier=carrier,
                        position=_infer_text_position(cl, carrier),
                        exact_text=exact,
                    )
                    matched_here = True
            for m in _TEXT_DISPLAY_UNQUOTED_RE.finditer(cl):
                carrier = m.group(1)
                exact = m.group(2).strip("，。；！？、 ")
                if exact and exact not in _TEXT_UNQUOTED_STOP and not any(exact.startswith(s) for s in ("无", "没有", "空白")):
                    _add_item(
                        source=src_name,
                        carrier=carrier,
                        position=_infer_text_position(cl, carrier),
                        exact_text=exact,
                    )
                    matched_here = True
            if not matched_here:
                for cw in _TEXT_CARRIER_WORDS:
                    if cw in cl:
                        _add_unconstrained(
                            source=src_name,
                            carrier=cw,
                            position=_infer_text_position(cl, cw),
                        )

    if explicit_mode == "forbid_all":
        mode = "forbid_all"
    elif allowed:
        mode = "explicit_diegetic"
    else:
        mode = "conservative"

    return {
        "mode": mode,
        "allowed_texts": allowed,
        "uncertain_carriers": uncertain,
        "unconstrained_carriers": unconstrained,
    }


def render_frame_text_constraint(text_spec: dict | None) -> str:
    """Format prompt-side text constraint from unified frame_text_spec."""
    spec = text_spec if isinstance(text_spec, dict) else {}
    mode = str(spec.get("mode") or "conservative")
    allowed = [a for a in (spec.get("allowed_texts") or []) if isinstance(a, dict) and a.get("exact_text")]
    unconstrained = [u for u in (spec.get("unconstrained_carriers") or []) if isinstance(u, dict)]
    if mode == "forbid_all":
        return "纯视觉画面呈现，画面任意区域无任何文字、地名标题、字幕、牌匾题字或水印。"
    if mode == "explicit_diegetic" and allowed:
        parts = [f"{a.get('carrier') or '载体'}呈现文字“{a.get('exact_text')}”" for a in allowed[:4]]
        joined = "、".join(parts)
        return (
            f"仅允许剧情指定的画面文字（{joined}）在其对应载体上自然呈现；"
            "禁止在天空、水面、背景空白处或画面角落添加任何额外地名标题、书法大字、字幕、水印或无关题字。"
        )
    if unconstrained:
        return (
            "如有对应物理载体仅可自然呈现，禁止在天空、水面、背景或角落添加任何额外地名标题、"
            "书法大字、字幕、水印或无关题字。"
        )
    return (
        "纯视觉画面呈现，禁止在天空、水面、背景或角落添加任何额外地名标题、"
        "书法大字、字幕、水印或无关题字。"
    )


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
    advisories: list[dict] = []
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
        contradictions = _detect_entity_contradictions(
            cid, pose_text, props, states, advisories=advisories,
        )
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
        "spec_advisories": advisories,
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
