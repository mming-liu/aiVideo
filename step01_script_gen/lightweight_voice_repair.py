"""Bounded edits to the voices of one frozen lightweight outline.

Each round edits the current baseline (the first answer, or the last accepted partial edit).
Voice IDs are fixed by the first answer's (event, beat) positions, so deleting a voice never
renumbers the others between rounds.

Candidate handling (S10, 2026-11-24): every replacement keeps a **record** — source line,
absolute span, enclosing quote, origin (own text vs bound evidence) and the attribution
basis used to accept it. The prompt menu and the full pool are views of the same records,
so "the menu has no budget solution" cannot be confused with "no legal candidate exists".
"""
import copy
import json
import os
import re

from step01_script_gen import plan_feasibility as pf
from step01_script_gen.timecode import _estimate_duration, active_shot_cap

MAX_LINES, MAX_HAN, MAX_LINE_HAN = 10, 280, 42
TARGET_SEC, REPLACEMENT_SEC = 80, 11
MAX_OPTIONS, MIN_OPTION_HAN = 4, 4


def line_limit() -> int:
    """声音条数上限；默认 10。S13 v2 隔离实验可经环境显式放宽到 11（不静默改默认）."""
    raw = os.environ.get("STEP01_VOICE_ALLOCATION_MAX_LINES")
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return MAX_LINES
    return max(MAX_LINES, min(v, 12))
#: Menu slots reserved for options inside the voice's own bound evidence (S07, 2026-11-23).
EVIDENCE_SLOTS = 2
#: Acceptance codes a voice edit can resolve; anything else is outside this protocol.
VOICE_CODES = frozenset({"budget_infeasible", "budget_unrepresentable", "lightweight_voice_budget"})
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f\U00030000-\U000323af]")
_SENTENCE_END = "。！？…!?"
_CLAUSE_END = "，、；：,;:"
_CLOSE_QUOTE = "”」』"
_OPEN_QUOTE = "“「『"
_ADDRESSEE = "对向朝冲"
_CONJUNCTION = "和与跟"
SCHEMA = {"name": "lightweight_voice_edits", "strict": True, "schema": {
    "type": "object", "additionalProperties": False, "required": ["edits", "unfixable"],
    "properties": {
        "edits": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "text"],
            "properties": {"id": {"type": "string"}, "text": {"type": "string"}}}},
        "unfixable": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "reason"],
            "properties": {"id": {"type": "string"}, "reason": {"type": "string"}}}}}}}


class DeclaredUnfixable(ValueError):
    """The model declared listed voices unfixable and proposed no edit."""

    def __init__(self, items):
        self.items = items
        super().__init__("模型声明不可修：" + "；".join(
            "%s：%s" % (x["id"], x["reason"]) for x in items))


def _check(kind, text, cap):
    # Same single-shot entry as evaluate_plan (dialogue gets the speaker-prefix allowance).
    return pf.voice_entry_representability(kind, text, cap)


def _han(text):
    return len(_HAN.findall(text))


def voices(answer, ids=None):
    """Voice rows in answer order. `ids` maps (event, beat) to the first answer's IDs."""
    rows = []
    counts = {"dialogue": 0, "narration": 0}
    for ei, event in enumerate(answer["events"]):
        for bi, beat in enumerate(event["beats"]):
            kind = str(beat.get("voice_kind") or "").strip()
            if kind not in counts:
                continue
            counts[kind] += 1
            text = str(beat.get("voice_text") or "").strip()
            check = _check(kind, text, active_shot_cap())
            bound = [f for f in answer["facts"] if f["event"] == ei + 1
                     and f["beat"] == bi + 1 and f.get("carrier") == "voice"]
            uid = (ids or {}).get((ei, bi)) or ("D" if kind == "dialogue" else "N") + str(counts[kind])
            rows.append({"id": uid, "event": ei, "beat": bi, "kind": kind,
                         "speaker": str(beat.get("speaker") or "").strip(),
                         "text": text, "han": _han(text),
                         "beat_source": str(beat.get("source") or "").strip(),
                         "claims": [f["claim"] for f in bound],
                         "evidence": [str(f.get("evidence") or "") for f in bound],
                         "sec": round(_estimate_duration(text), 2),
                         "single_sec": check["est_sec"], "playable": check["representable"]})
    return rows


def total(rows):
    # Match evaluate_plan: sum each voice kind, then combine the two subtotals.
    return round(sum(pf.voice_seconds(rows, kind) for kind in ("dialogue", "narration")), 2)


# ---------------------------------------------------------------------------
# Source identity and attribution
# ---------------------------------------------------------------------------
def _find_all(haystack, needle):
    out, i = [], 0
    if not needle:
        return out
    while True:
        i = haystack.find(needle, i)
        if i < 0:
            return out
        out.append(i)
        i += 1


def _quote_span(paragraph, start):
    """`start` 所在引语的 `(open, close)`；不在引语内返回 `None`（不猜归属）."""
    depth, open_i = 0, None
    for i, ch in enumerate(paragraph[:start + 1]):
        if ch in _OPEN_QUOTE:
            if depth == 0:
                open_i = i
            depth += 1
        elif ch in _CLOSE_QUOTE and depth:
            depth -= 1
    if not depth or open_i is None:
        return None
    depth = 0
    for j in range(open_i, len(paragraph)):
        ch = paragraph[j]
        if ch in _OPEN_QUOTE:
            depth += 1
        elif ch in _CLOSE_QUOTE:
            depth -= 1
            if depth == 0:
                return (open_i, j)
    return (open_i, len(paragraph) - 1)


def _names_in(text, characters):
    """Known character names occurring in `text`, longest match first (no alias guessing)."""
    spans = []
    for name in sorted(set(characters), key=len, reverse=True):
        for i in _find_all(text, name):
            if not any(a <= i < b for a, b in spans):
                spans.append((i, i + len(name)))
    return [text[a:b] for a, b in sorted(spans)]


def _sentence_owner(paragraph, quote_open):
    head = paragraph[:quote_open]
    cut = max(head.rfind(ch) for ch in "。！？!?\n")
    return head[cut + 1:].strip()


def _resolve_speaker(paragraph, quote, characters):
    """引语归属 → `(speaker, basis, status)`；`status` = ok / unresolved / ambiguous.

    - 「甲对乙说」：`对/向/朝/冲` 之前的是主体，之后是受话人——受话人名字**不能**当说话人；
    - 「甲和乙道」：两个名字都是主体 ⇒ ambiguous；
    - 只有代词/头衔（「他低声道」「冯检校道」）或没有已知角色名 ⇒ unresolved
      （本声音正文可依赖计划自己的说话人声明；绑定证据则不用 unresolved 来源）。
    """
    owner = _sentence_owner(paragraph, quote[0])
    if not owner:
        return "", "", "unresolved"
    for mark in _ADDRESSEE:
        j = owner.find(mark)
        if j > 0:
            left = list(dict.fromkeys(_names_in(owner[:j], characters)))
            if len(left) == 1:
                return left[0], owner, "ok"
            return "", owner, "ambiguous" if left else "unresolved"
    uniq = list(dict.fromkeys(_names_in(owner, characters)))
    if any(mark in owner for mark in _CONJUNCTION):
        return (uniq[0], owner, "ok") if len(uniq) == 1 else ("", owner, "ambiguous")
    if len(uniq) == 1:
        return uniq[0], owner, "ok"
    return "", owner, "ambiguous" if uniq else "unresolved"


def _closed_at(paragraph, end):
    nxt = paragraph[end:end + 1]
    return (paragraph[end - 1:end] in _SENTENCE_END
            or nxt in _SENTENCE_END + _CLOSE_QUOTE or nxt == "")


def _sentence_start_at(paragraph, start):
    prev = paragraph[start - 1:start] if start > 0 else ""
    return prev == "" or prev in _SENTENCE_END + _OPEN_QUOTE + "：:"


def _prefix_spans(paragraph, quote):
    """引语内按句末收尾的前缀 `(start, end)`（含引号内起点）."""
    if quote is None:
        return []
    qs, qe = quote
    out = []
    for k, ch in enumerate(paragraph[qs + 1:qe]):
        if ch in _SENTENCE_END:
            out.append((qs + 1, qs + 2 + k))
    return out


def _window_spans(text, cap):
    """All contiguous clause windows (every clause start, sentence start included), ≤ cap."""
    bounds, i, n = [0], 0, len(text)
    while i < n:
        if text[i] in _SENTENCE_END + _CLAUSE_END:
            j = i
            while j < n and text[j] in _SENTENCE_END + _CLAUSE_END:
                j += 1
            if j != bounds[-1]:
                bounds.append(j)
            i = j
            continue
        i += 1
    if bounds[-1] != n:
        bounds.append(n)
    out = []
    for a in range(len(bounds) - 1):
        for b in range(a + 1, len(bounds)):
            s, e = bounds[a], bounds[b]
            w = text[s:e].strip()
            if not w:
                continue
            if float(_estimate_duration(w)) > float(cap) + 0.01:
                break
            out.append((s, e))
    return out


def _verbatim_pieces(anchor, novel):
    """Split `anchor` into maximal pieces that each occur verbatim on one line of `novel`."""
    pieces, i = [], 0
    anchor = anchor.replace("\n", "")
    while i < len(anchor):
        j = i
        while j < len(anchor) and anchor[i:j + 1] in novel:
            j += 1
        if j == i:
            i += 1
            continue
        piece = anchor[i:j].strip()
        if _han(piece) >= MIN_OPTION_HAN:
            pieces.append(piece)
        i = j
    return pieces


def _source_note(row, notes, code, detail):
    item = {"code": code, "detail": str(detail or "")[:160]}
    if item not in notes:
        notes.append(item)


def _sources(row, novel, characters):
    """Candidate sources as records + ambiguity notes.

    Source order: the voice's **own** text (all occurrences, attributed to the declared
    speaker, disambiguated by the beat's 原文依据) and, only when the own text cannot be
    located, the bound facts' evidence (same speaker for dialogue, outside quotes for
    narration). Nothing is resolved by `find()` after the fact.
    """
    lines = novel.splitlines()
    characters = list(dict.fromkeys([c for c in characters if c]))
    own, notes = str(row.get("text") or ""), []
    occurrences, conflict, ambiguous = [], "", ""
    for ln, paragraph in enumerate(lines):
        for start in _find_all(paragraph, own):
            quote = _quote_span(paragraph, start)
            if row["kind"] == "dialogue":
                if quote is None:
                    continue
                speaker, basis, status = _resolve_speaker(paragraph, quote, characters)
                if status == "ok" and speaker != row["speaker"]:
                    conflict = "原文引语归属 %r，与计划声明的说话人 %r 不一致" % (
                        speaker, row["speaker"])
                    continue
                if status == "ambiguous":
                    ambiguous = "引语前有多个已知角色名（%s），主体无法唯一确定" % basis
                    continue
                # status == ok (same speaker) or unresolved (title/pronoun): own text is
                # authoritative for the voice; the basis records what was checkable.
                occurrences.append({"line": ln, "start": start, "end": start + len(own),
                                    "quote": quote, "origin": "own", "anchor": own,
                                    "basis": basis if status == "ok" else "归属未核对（%s）" % basis})
            else:
                if quote is not None:
                    continue
                occurrences.append({"line": ln, "start": start, "end": start + len(own),
                                    "quote": None, "origin": "own", "anchor": own,
                                    "basis": ""})
    if len(occurrences) > 1:
        src = row.get("beat_source") or ""
        hit = [o for o in occurrences if src and src in lines[o["line"]]]
        if len(hit) == 1:
            occurrences = hit
    if len(occurrences) == 1:
        return occurrences, notes
    if len(occurrences) > 1:
        if row["kind"] == "narration":
            return occurrences[:1], notes          # same text; position is equivalent
        _source_note(row, notes, "attribution_ambiguous",
                     "本声音正文在原文出现 %d 次，无法用原文依据唯一定位" % len(occurrences))
        return [], notes
    if ambiguous:
        _source_note(row, notes, "attribution_ambiguous", ambiguous)
        return [], notes
    if conflict:
        _source_note(row, notes, "attribution_conflict", conflict)
        return [], notes
    out = []
    for evidence in (str(x or "").strip() for x in (row.get("evidence") or [])):
        if not evidence:
            continue
        pieces = ([evidence] if evidence in novel and "\n" not in evidence
                  else _verbatim_pieces(evidence, novel))
        for piece in pieces:
            for ln, paragraph in enumerate(lines):
                for start in _find_all(paragraph, piece):
                    quote = _quote_span(paragraph, start)
                    if row["kind"] == "dialogue":
                        if quote is None:
                            continue
                        speaker, basis, status = _resolve_speaker(paragraph, quote, characters)
                        if status != "ok" or speaker != row["speaker"]:
                            if status == "ambiguous":
                                _source_note(row, notes, "attribution_ambiguous",
                                             "绑定证据的引语归属无法核对到 %r" % row["speaker"])
                            continue
                    elif quote is not None:
                        continue
                    item = {"line": ln, "start": start, "end": start + len(piece),
                            "quote": quote, "origin": "evidence", "anchor": piece,
                            "basis": basis if row["kind"] == "dialogue" else ""}
                    if item not in out:
                        out.append(item)
    return out, notes


def _make_candidate(paragraph, line_no, start, end, row, quote, origin, anchor, cap, evidence_texts):
    raw = paragraph[start:end]
    lead = len(raw) - len(raw.lstrip())
    trail = len(raw) - len(raw.rstrip())
    start, end = start + lead, end - trail
    text = paragraph[start:end]
    if (not text or text == row["text"] or "\n" in text
            or any(ch in text for ch in _OPEN_QUOTE + _CLOSE_QUOTE)
            or not MIN_OPTION_HAN <= _han(text) <= MAX_LINE_HAN
            or _estimate_duration(text) >= row["sec"]):
        return None
    check = _check(row["kind"], text, cap)
    if check["est_sec"] > cap:
        return None
    return {"text": text, "line": line_no, "start": start, "end": end, "quote": quote,
            "origin": origin, "anchor": anchor,
            "claim_aligned": any(text in ev for ev in evidence_texts),
            "han": _han(text), "sec": round(_estimate_duration(text), 2),
            "single_sec": round(float(check["est_sec"]), 2),
            "closed": _closed_at(paragraph, end),
            "sentence_start": _sentence_start_at(paragraph, start)}


def _ends_clause(text):
    return bool(text) and text[-1] in _CLAUSE_END


def _trimmed_end(paragraph, start, end):
    """Span variants: the raw slice and the slice with trailing clause punctuation removed."""
    out = [(start, end)]
    raw = paragraph[start:end]
    stripped = raw.rstrip(_CLAUSE_END)
    if stripped and len(stripped) < len(raw):
        out.append((start, start + len(stripped)))
    return out


def _candidate_records(row, novel, characters=None, cap=None):
    """All legal replacement records for one voice (source identity preserved)."""
    cap = active_shot_cap() if cap is None else cap
    characters = ([row.get("speaker")] if characters is None and row.get("speaker")
                  else list(characters or []))
    lines = novel.splitlines()
    sources, notes = _sources(row, novel, characters)
    evidence_texts = [str(e) for e in (row.get("evidence") or []) if e]
    out, seen = [], set()

    def _add(line_no, start, end, quote, origin, anchor):
        for s, e in _trimmed_end(lines[line_no], start, end):
            key = (line_no, s, e, origin)
            if key in seen:
                continue
            cand = _make_candidate(lines[line_no], line_no, s, e, row, quote, origin,
                                   anchor, cap, evidence_texts)
            if cand is not None:
                seen.add(key)
                out.append(cand)

    for src in sources:
        paragraph = lines[src["line"]]
        _add(src["line"], src["start"], src["end"], src["quote"], src["origin"], src["anchor"])
        for start, end in _prefix_spans(paragraph, src["quote"]):
            _add(src["line"], start, end, src["quote"], src["origin"], src["anchor"])
        base = src["start"]
        for start, end in _window_spans(paragraph[base:src["end"]], cap):
            _add(src["line"], base + start, base + end, src["quote"], src["origin"], src["anchor"])
    return out, notes


def _rank_records(records):
    """Whole sentences, then sentence-final tails, then clause fragments; containment dedupe."""
    def _cls(c):
        return (0 if c["sentence_start"] else 1) if c["closed"] else 2
    by_cls = {}
    for c in records:
        by_cls.setdefault(_cls(c), []).append(c)
    ranked = []
    for cls in sorted(by_cls):
        texts = [c["text"] for c in by_cls[cls]]
        keep = [c for c in by_cls[cls] if not any(c["text"] != o and c["text"] in o for o in texts)]
        ranked += sorted(keep, key=lambda c: (-c["han"], c["line"], c["start"]))
    return ranked


def _dedupe_text(records):
    out, seen = [], set()
    for c in records:
        if c["text"] in seen:
            continue
        seen.add(c["text"])
        out.append(c)
    return out


def _option_records(row, novel, cap, characters=None):
    records, notes = _candidate_records(row, novel, characters, cap)
    ranked = _rank_records(records)
    return ranked, _dedupe_text(records), notes


def _options(row, novel, cap, characters=None):
    """Compatibility view: `(ranked text list, full pool text list)`."""
    ranked, pool, _ = _option_records(row, novel, cap, characters)
    return [c["text"] for c in ranked], [c["text"] for c in pool]


def _menu(options):
    """Default prompt menu for a text list: the first `MAX_OPTIONS` entries."""
    return list(options[:MAX_OPTIONS])


def _menu_records(row, ranked, pool, *, trim=False):
    """Prompt menu from the **same records** as the pool (evidence slots + budget witness)."""
    picks = []

    def _add(rec):
        if rec is not None and rec["text"] not in [x["text"] for x in picks]:
            picks.append(rec)

    if row.get("claims"):
        used = 0
        # 证据位优先展示**不以逗号截断**的候选（句末收尾或去尾后的完整片段）；
        # 逗号结尾的窗口仍留在池/排名里，但不占证据展示位。
        aligned = [c for c in pool if c["claim_aligned"] and not _ends_clause(c["text"])]
        for rec in sorted(aligned, key=lambda c: (not c["closed"], -c["han"], c["line"], c["start"])):
            if used >= EVIDENCE_SLOTS:
                break
            if any(rec["text"] in x["text"] for x in picks if x is not rec):
                continue
            _add(rec)
            used += 1
    if trim and pool:
        # Budget witness only when the chapter is over budget: a replace-scope menu must not
        # push a shortest fragment to the front and invite content loss (S11 ch1 observed).
        _add(min(pool, key=lambda c: (c["sec"], not c["closed"], c["han"])))
    for rec in ranked:
        if len(picks) >= MAX_OPTIONS:
            break
        _add(rec)
    return [c["text"] for c in picks[:MAX_OPTIONS]]


def _best_case(rows, ctx_trim, *, full=True):
    """Lines / han / seconds after the strongest legal edit of every editable row.

    `full=True` uses the **pool** (the only valid basis for "unfixable");
    `full=False` answers "can the prompt menu itself reach the budget?".
    """
    kept = []
    for r in rows:
        if r.get("editable") and r.get("deletable") and ctx_trim:
            continue
        if full:
            opts = r.get("_pool") or []
            text = (min(opts, key=lambda c: (c["sec"], -c["han"]))["text"]
                    if r.get("editable") and opts else r["text"])
        else:
            texts = r.get("replacements") or []
            text = (min(texts, key=_estimate_duration)
                    if r.get("editable") and texts else r["text"])
        kept.append(dict(r, text=text, sec=round(_estimate_duration(text), 2), han=_han(text)))
    return {"lines": len(kept), "han": sum(r["han"] for r in kept), "seconds": total(kept)}


def _fits_budget(best, goal, ceiling):
    return (best["seconds"] <= min(goal, ceiling) + 0.01
            and best["lines"] <= line_limit() and best["han"] <= MAX_HAN)


def _blocked_reason(row):
    for note in row.get("_candidate_notes") or []:
        if note.get("code") == "attribution_ambiguous":
            return "候选来源的发言归属无法唯一确定：%s；只有能核对到同一说话人的逐字短句才进菜单" % note["detail"]
        if note.get("code") == "attribution_conflict":
            return "本声音正文在原文里的归属与计划声明的说话人 %r 不一致：%s；不跨说话人替换" % (
                row.get("speaker"), note["detail"])
    return ("超单镜或超 %d 字，被事实引用不能删，原文同段与绑定证据里都没有"
            "可表示的逐字短句" % MAX_LINE_HAN)


def prepare(answer, novel, ids=None):
    rows = voices(answer, ids)
    characters = [str(x).strip() for x in (answer.get("characters") or []) if str(x).strip()]
    seconds = total(rows)
    _, ceiling = pf.budget_band()
    over_time = seconds > ceiling + 0.01
    trim = over_time or len(rows) > line_limit() or sum(r["han"] for r in rows) > MAX_HAN
    bad = [r for r in rows if r["single_sec"] > active_shot_cap() or r["han"] > MAX_LINE_HAN]
    if not trim and not bad:
        return None
    cap = active_shot_cap() if trim else min(REPLACEMENT_SEC, active_shot_cap())
    for r in rows:
        r["editable"] = trim or r in bad
        if r["editable"]:
            ranked, pool, notes = _option_records(r, novel, cap, characters)
            r["_ranked"], r["_pool"] = ranked, pool
            r["_candidate_notes"] = notes
            r["_all_options"] = [c["text"] for c in pool]
            r["replacements"] = _menu_records(r, ranked, pool, trim=trim)
            r["deletable"] = trim and not r["claims"]
    goal = min(TARGET_SEC, ceiling) if over_time else seconds
    blocked = []
    for r in bad:
        if r["replacements"] or (trim and r["deletable"]):
            continue
        blocked.append({"id": r["id"], "reason": _blocked_reason(r)})
    best = _best_case(rows, trim)
    if trim and not blocked:
        if not _fits_budget(best, goal, ceiling):
            blocked.append({"id": "*", "reason": "删光可删声音并全部换最短候选后仍超限："
                            "%d 条 / %d 字 / %.2fs" % (best["lines"], best["han"], best["seconds"])})
        elif not _fits_budget(_best_case(rows, trim, full=False), goal, ceiling):
            # The pool has a solution but the compressed menu does not: the menu must keep the
            # budget witness for every row (S10: "菜单压缩后必须再次验证").
            for r in rows:
                if r.get("editable") and r.get("_pool"):
                    r["replacements"] = _menu_records(r, r["_ranked"], r["_pool"], trim=True)
            if not _fits_budget(_best_case(rows, trim, full=False), goal, ceiling):
                blocked.append({"id": "*", "reason": "菜单无法同时保留预算解与身份约束："
                                "池内可行但展示菜单无解"})
    return {"scope": "trim" if trim else "replace", "seconds": seconds, "goal": goal,
            "rows": rows, "ids": {(r["event"], r["beat"]): r["id"] for r in rows},
            "best_case": best, "blocked": blocked}

def _row_view(r):
    if not r.get("editable"):
        return {"id": r["id"], "kind": r["kind"], "speaker": r["speaker"], "sec": r["sec"],
                "locked": True}
    return {k: r[k] for k in ("id", "kind", "speaker", "text", "han", "sec", "single_sec",
                              "claims", "replacements", "deletable")}


def prompt(ctx):
    reduction = round(ctx["seconds"] - ctx["goal"], 2)
    must = [r["id"] for r in ctx["rows"] if r.get("editable")
            and (r["single_sec"] > active_shot_cap() or r["han"] > MAX_LINE_HAN)]
    return ("基于当前稿，只提交 edits（id 和新 text），不要重抄整稿；未点名的字段保持原样。"
            "替换必须从该条 replacements 里逐字选一条，保持原说话人、语意、事实与节拍；"
            "仅 deletable=true 的条目可用空串删除；不得新增声音或删节拍。"
            + (f"必须处理的超限条目：{'、'.join(must)}。" if must else "")
            + "若某条在 replacements 里确实没有能保住其 claims 的选项，把它写进 unfixable 并说明缺了哪层内容，"
            "其余条目照常修订；unfixable 不能代替可做的修订。"
            f"声音上限：{line_limit()} 条、{MAX_HAN} 个汉字、每条 {MAX_LINE_HAN} 字。"
            f"当前稿正文合计 {ctx['seconds']} 秒，必须净缩短"
            + (f"至少 {reduction} 秒（到 {ctx['goal']} 秒以内）。" if reduction else "，锁定项不动。")
            + "\n声音清单（sec 为正文估时，single_sec 含对白前缀余量；locked 为锁定项）：\n"
            + json.dumps([_row_view(r) for r in ctx["rows"]], ensure_ascii=False))


def apply(answer, ctx, response):
    """Return a candidate for a valid transaction. Never mutate the baseline.

    A partial fix is a valid transaction as long as the total strictly decreases; whether the
    candidate is final is decided by re-running `prepare` on it.
    """
    if not isinstance(response, dict) or "edits" not in response or not set(response) <= {
            "edits", "unfixable"}:
        raise ValueError("修订只接受 edits/unfixable，不接受整稿")
    edits, unfixable = response["edits"], response.get("unfixable") or []
    by_id = {r["id"]: r for r in ctx["rows"]}
    if not isinstance(edits, list) or not isinstance(unfixable, list):
        raise ValueError("edits/unfixable 必须是数组")
    for item in unfixable:
        if (not isinstance(item, dict) or set(item) != {"id", "reason"}
                or item["id"] not in by_id or not by_id[item["id"]].get("editable")
                or not str(item["reason"]).strip()):
            raise ValueError("unfixable 项必须是可编辑条目的 id 加非空原因")
    if not edits:
        if unfixable:
            raise DeclaredUnfixable(unfixable)
        raise ValueError("没有可应用的声音修订，也没有说明不可修原因")
    result = copy.deepcopy(answer)
    seen = set()
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"id", "text"}:
            raise ValueError("修订项只能包含 id 和 text")
        uid, text = edit["id"], edit["text"]
        if not isinstance(uid, str) or uid in seen or uid not in by_id or not isinstance(text, str):
            raise ValueError("声音编号不存在、重复或正文类型错误")
        seen.add(uid)
        r = by_id[uid]
        if not r["editable"]:
            raise ValueError(f"{uid} 已锁定")
        if (text and text not in r["replacements"]) or (not text and not r["deletable"]):
            raise ValueError(f"{uid} 不允许此替换或删除")
        beat = result["events"][r["event"]]["beats"][r["beat"]]
        beat["voice_text"] = text
        if not text:
            beat.update(voice_kind="none", speaker="")
    after = total(voices(result))
    if after >= ctx["seconds"]:
        raise ValueError(f"总时长没有下降：{ctx['seconds']} → {after}s")
    return result
