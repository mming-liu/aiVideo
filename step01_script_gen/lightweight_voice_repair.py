"""Bounded edits to the voices of one frozen lightweight outline.

Each round edits the current baseline (the first answer, or the last accepted partial edit).
Voice IDs are fixed by the first answer's (event, beat) positions, so deleting a voice never
renumbers the others between rounds.
"""
import copy
import json
import re

from step01_script_gen import plan_feasibility as pf
from step01_script_gen.plan_content_repair import _utterance_windows
from step01_script_gen.timecode import _estimate_duration, active_shot_cap

MAX_LINES, MAX_HAN, MAX_LINE_HAN = 10, 280, 42
TARGET_SEC, REPLACEMENT_SEC = 80, 11
MAX_OPTIONS, MIN_OPTION_HAN = 4, 4
#: Acceptance codes a voice edit can resolve; anything else is outside this protocol.
VOICE_CODES = frozenset({"budget_infeasible", "budget_unrepresentable", "lightweight_voice_budget"})
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f\U00030000-\U000323af]")
_SENTENCE_END = "。！？…!?"
_CLOSE_QUOTE = "”」』"
_OPEN_QUOTE = "“「『"
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
                         "speaker": beat.get("speaker", ""), "text": text, "han": _han(text),
                         "claims": [f["claim"] for f in bound],
                         "evidence": [str(f.get("evidence") or "") for f in bound],
                         "sec": round(_estimate_duration(text), 2),
                         "single_sec": check["est_sec"], "playable": check["representable"]})
    return rows


def total(rows):
    # Match evaluate_plan: sum each voice kind, then combine the two subtotals.
    return round(sum(pf.voice_seconds(rows, kind) for kind in ("dialogue", "narration")), 2)


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


def _quote_owner(paragraph, span):
    """引语前的**归属叙述**（同一句内、开引号之前的文字）."""
    head = paragraph[:span[0]]
    cut = max(head.rfind(ch) for ch in "。！？!?\n")
    return head[cut + 1:].strip()


def _same_speaker(paragraph, span, speaker):
    """引语的归属是否对得上这条声音声明的说话人（对不上就不许当候选）."""
    name = str(speaker or "").strip()
    return bool(name) and name in _quote_owner(paragraph, span)


def _ends_sentence(paragraph, t):
    i = paragraph.find(t)
    nxt = paragraph[i + len(t):i + len(t) + 1] if i >= 0 else ""
    return t[-1:] in _SENTENCE_END or nxt in _SENTENCE_END + _CLOSE_QUOTE or nxt == ""


def _starts_sentence(paragraph, t):
    i = paragraph.find(t)
    prev = paragraph[i - 1:i] if i > 0 else ""
    return prev == "" or prev in _SENTENCE_END + _OPEN_QUOTE + "：:"


def _verbatim_pieces(anchor, novel):
    """Split `anchor` into maximal pieces that each occur verbatim on one line of `novel`.

    A planner often joins two source paragraphs or swaps a comma for a full stop; the pieces
    are still the author's words and can seed candidates.
    """
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


def _sources(row, novel):
    """`(paragraph, anchor, quote_span)` 候选来源：**本声音自身优先**，证据只作兜底.

    - 本声音正文在原文里逐字可定位 ⇒ 只从它所在的引语取候选，不再拿绑定证据
      扩展：证据可能是**另一个角色**的原话（复核 2026-11-23 P1），而应用器保留
      旧 `speaker`，跨说话人替换会把别人的话安到旧说话人身上。
    - 本声音正文定位不到（如旁白拼接了原文两段）⇒ 才用绑定证据；对白证据必须
      落在**归属同一说话人**的引语里，归属核对不了就不给候选（fail-closed）。
    """
    paragraphs = novel.splitlines()
    own = str(row.get("text") or "")
    if own and "\n" not in own:
        for p in paragraphs:
            pos = p.find(own)
            if pos >= 0:
                span = _quote_span(p, pos)
                if row["kind"] == "dialogue" and span is None:
                    return []
                return [(p, own, span)]
    out = []
    for anchor in (str(x or "").strip() for x in (row.get("evidence") or [])):
        if not anchor:
            continue
        verbatim = anchor in novel and "\n" not in anchor
        for piece in ([anchor] if verbatim else _verbatim_pieces(anchor, novel)):
            p = next((x for x in paragraphs if piece in x), None)
            if p is None:
                continue
            span = _quote_span(p, p.find(piece))
            if row["kind"] == "dialogue":
                if span is None or not _same_speaker(p, span, row.get("speaker")):
                    continue
            elif span is not None:
                continue          # 旁白不得借用某人的引语
            if (p, piece, span) not in out:
                out.append((p, piece, span))
    return out


def _options(row, novel, cap):
    """Short verbatim replacements that keep the voice kind; complete sentences first.

    Returns the **full** ranked list; `prepare` decides the prompt menu, and the budget
    precheck (`_best_case`) must not judge on the compressed menu alone.
    """
    kind = row["kind"]
    valid = []
    for paragraph, anchor, span in _sources(row, novel):
        cands = [anchor] if anchor != row["text"] else []
        cands += pf.representable_replacements(anchor, paragraph)
        cands += _utterance_windows(anchor, cap)
        for t in dict.fromkeys(cands):
            t = t.strip()
            pos = paragraph.find(t)
            # Stay inside the anchor's **own quote**: never turn narration into someone's
            # speech or the reverse, and never borrow another speaker's utterance (the
            # transaction only changes text, not `speaker`).
            if (not t or t == row["text"] or pos < 0 or t in [v[0] for v in valid]
                    or any(c in t for c in _OPEN_QUOTE + _CLOSE_QUOTE)
                    or _quote_span(paragraph, pos) != span
                    or not MIN_OPTION_HAN <= _han(t) <= MAX_LINE_HAN
                    or _estimate_duration(t) >= row["sec"]
                    or _check(kind, t, cap)["est_sec"] > cap):
                continue
            valid.append((t, paragraph, anchor != row["text"]))
    # Rank whole sentences, then sentence-final tails, then clause fragments. Within one class,
    # drop options wholly contained in another (same words, less content).
    def _cls(v):
        return (0 if _starts_sentence(v[1], v[0]) else 1) if _ends_sentence(v[1], v[0]) else 2
    by_cls = {}
    for v in valid:
        by_cls.setdefault(_cls(v), []).append(v)
    ranked = []
    for c in sorted(by_cls):
        texts = [v[0] for v in by_cls[c]]
        keep = [v for v in by_cls[c] if not any(v[0] != o and v[0] in o for o in texts)]
        ranked += sorted(keep, key=lambda v: -_han(v[0]))
    # `ranked` 是给提示的压缩菜单；`valid` 的完整集合留给预算预判与最短候选兜底
    # （包含被「更长候选包含」而没进菜单的短句，复核 2026-11-23 P2）。
    return [v[0] for v in ranked], [v[0] for v in valid]


def _menu(options):
    """Default prompt menu: the first `MAX_OPTIONS` ranked options."""
    return list(options[:MAX_OPTIONS])


def _budget_menu(options):
    """Menu that must contain a **budget-feasible short option** (complete sentence first).

    The prompt is capped at `MAX_OPTIONS`; dropping every contained short sentence made
    the model unable to reach the budget even when the full set had a legal path
    (复核 2026-11-23 P2). Keep the shortest option and fill the rest from the ranking.
    """
    if len(options) <= MAX_OPTIONS:
        return list(options)
    closed = [t for t in options if t.rstrip(_CLOSE_QUOTE).endswith(tuple(_SENTENCE_END))]
    short = min(closed or options, key=lambda t: (_estimate_duration(t), -_han(t)))
    return [short] + [t for t in options if t != short][:MAX_OPTIONS - 1]


def _best_case(rows, ctx_trim, *, full=True):
    """Lines / han / seconds after the strongest legal edit of every editable row.

    `full=True` uses the **full** option set (the only valid basis for "unfixable");
    `full=False` answers "can the prompt menu itself reach the budget?".
    """
    kept = []
    for r in rows:
        if r.get("editable") and r.get("deletable") and ctx_trim:
            continue
        opts = ((r.get("_all_options") if full else r.get("replacements")) or [])
        text = min(opts, key=_estimate_duration) if (r.get("editable") and opts) else r["text"]
        kept.append(dict(r, text=text, sec=round(_estimate_duration(text), 2), han=_han(text)))
    return {"lines": len(kept), "han": sum(r["han"] for r in kept), "seconds": total(kept)}


def _fits_budget(best, goal, ceiling):
    return (best["seconds"] <= min(goal, ceiling) + 0.01
            and best["lines"] <= MAX_LINES and best["han"] <= MAX_HAN)


def prepare(answer, novel, ids=None):
    rows = voices(answer, ids)
    seconds = total(rows)
    _, ceiling = pf.budget_band()
    over_time = seconds > ceiling + 0.01
    trim = over_time or len(rows) > MAX_LINES or sum(r["han"] for r in rows) > MAX_HAN
    bad = [r for r in rows if r["single_sec"] > active_shot_cap() or r["han"] > MAX_LINE_HAN]
    if not trim and not bad:
        return None
    cap = active_shot_cap() if trim else min(REPLACEMENT_SEC, active_shot_cap())
    for r in rows:
        r["editable"] = trim or r in bad
        if r["editable"]:
            ranked, r["_all_options"] = _options(r, novel, cap)
            r["replacements"] = _menu(ranked)
            r["deletable"] = trim and not r["claims"]
    goal = min(TARGET_SEC, ceiling) if over_time else seconds
    blocked = [{"id": r["id"], "reason": "超单镜或超 %d 字，被事实引用不能删，原文同段与绑定证据里都没有"
                "可表示的逐字短句" % MAX_LINE_HAN}
               for r in bad if not r["replacements"] and not (trim and r["deletable"])]
    best = _best_case(rows, trim)
    if trim and not blocked:
        if not _fits_budget(best, goal, ceiling):
            # 预判说「装不下」时先给每条补一条最短的合法候选再复算：提示菜单被压缩过，
            # 但**不可修**只能由完整候选集判定（复核 2026-11-23 P2）。
            for r in rows:
                if r.get("editable") and r.get("_all_options"):
                    r["replacements"] = _budget_menu(r["_all_options"])
            best = _best_case(rows, trim)
        elif not _fits_budget(_best_case(rows, trim, full=False), goal, ceiling):
            # 完整候选集能装下、压缩后的菜单装不下 ⇒ 菜单必须补最短候选，
            # 否则模型根本拿不到能达标的选项。
            for r in rows:
                if r.get("editable") and r.get("_all_options"):
                    r["replacements"] = _budget_menu(r["_all_options"])
    if trim and not blocked and not _fits_budget(best, goal, ceiling):
        blocked.append({"id": "*", "reason": "删光可删声音并全部换最短候选后仍超限："
                        "%d 条 / %d 字 / %.2fs" % (best["lines"], best["han"], best["seconds"])})
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
            f"声音上限：{MAX_LINES} 条、{MAX_HAN} 个汉字、每条 {MAX_LINE_HAN} 字。"
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
