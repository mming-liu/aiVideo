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


def _in_quote(paragraph, start):
    before = paragraph[:start]
    return sum(before.count(c) for c in _OPEN_QUOTE) > sum(before.count(c) for c in _CLOSE_QUOTE)


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
    """(paragraph, anchor) pairs: the voice itself, then its bound evidence (verbatim pieces)."""
    paragraphs = novel.splitlines()
    out = []
    # The voice's own text seeds candidates only when it is verbatim; otherwise only the bound
    # facts' evidence does, so a replacement keeps to what the fact actually cites.
    anchors = [(row["text"], False)] + [(e, True) for e in row["evidence"]]
    for anchor, split in anchors:
        if not anchor:
            continue
        verbatim = anchor in novel and "\n" not in anchor
        for piece in ([anchor] if verbatim else _verbatim_pieces(anchor, novel) if split else []):
            p = next((p for p in paragraphs if piece in p), None)
            if p is not None and (p, piece) not in out:
                out.append((p, piece))
    return out


def _options(row, novel, cap):
    """Short verbatim replacements that keep the voice kind; complete sentences first."""
    kind = row["kind"]
    valid = []
    for paragraph, anchor in _sources(row, novel):
        quoted = _in_quote(paragraph, paragraph.find(anchor))
        cands = [anchor] if anchor != row["text"] else []
        cands += pf.representable_replacements(anchor, paragraph)
        cands += _utterance_windows(anchor, cap)
        for t in dict.fromkeys(cands):
            t = t.strip()
            pos = paragraph.find(t)
            # Stay on the anchor's side of the quotes: never turn narration into someone's
            # speech or the reverse, and never mix another speaker's quote in.
            if (not t or t == row["text"] or pos < 0 or t in [v[0] for v in valid]
                    or any(c in t for c in _OPEN_QUOTE + _CLOSE_QUOTE)
                    or _in_quote(paragraph, pos) != quoted
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
    return [v[0] for v in ranked[:MAX_OPTIONS]]


def _best_case(rows, ctx_trim):
    """Lines / han / seconds after the strongest legal edit of every editable row."""
    kept = []
    for r in rows:
        if r.get("editable") and r.get("deletable") and ctx_trim:
            continue
        text = min(r["replacements"], key=_estimate_duration) if (
            r.get("editable") and r.get("replacements")) else r["text"]
        kept.append(dict(r, text=text, sec=round(_estimate_duration(text), 2), han=_han(text)))
    return {"lines": len(kept), "han": sum(r["han"] for r in kept), "seconds": total(kept)}


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
            r["replacements"] = _options(r, novel, cap)
            r["deletable"] = trim and not r["claims"]
    goal = min(TARGET_SEC, ceiling) if over_time else seconds
    blocked = [{"id": r["id"], "reason": "超单镜或超 %d 字，被事实引用不能删，原文同段与绑定证据里都没有"
                "可表示的逐字短句" % MAX_LINE_HAN}
               for r in bad if not r["replacements"] and not (trim and r["deletable"])]
    best = _best_case(rows, trim)
    if trim and not blocked and (best["seconds"] > min(goal, ceiling) + 0.01
                                 or best["lines"] > MAX_LINES or best["han"] > MAX_HAN):
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
