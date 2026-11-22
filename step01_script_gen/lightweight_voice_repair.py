"""Bounded edits to the voices of one frozen lightweight outline."""
import copy
import json
import re

from step01_script_gen import plan_feasibility as pf
from step01_script_gen.plan_content_repair import _utterance_windows
from step01_script_gen.timecode import _estimate_duration, active_shot_cap

MAX_LINES, MAX_HAN, MAX_LINE_HAN = 10, 280, 42
TARGET_SEC, REPLACEMENT_SEC = 80, 11
_HAN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f\U00030000-\U000323af]")
SCHEMA = {"name": "lightweight_voice_edits", "strict": True, "schema": {
    "type": "object", "additionalProperties": False, "required": ["edits"],
    "properties": {"edits": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "text"],
        "properties": {"id": {"type": "string"}, "text": {"type": "string"}}}}}}}


def _check(kind, text, cap):
    return pf.check_dialogue_representable(
        text, cap, prefix_allowance_chars=5 if kind == "dialogue" else 0)


def voices(answer):
    """IDs refer to the input, even when earlier voices are removed."""
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
            claims = [f["claim"] for f in answer["facts"] if f["event"] == ei + 1
                      and f["beat"] == bi + 1 and f.get("carrier") == "voice"]
            rows.append({"id": ("D" if kind == "dialogue" else "N") + str(counts[kind]),
                         "event": ei, "beat": bi, "kind": kind, "speaker": beat.get("speaker", ""),
                         "text": text, "han": len(_HAN.findall(text)), "claims": claims,
                         "sec": round(_estimate_duration(text), 2),
                         "single_sec": check["est_sec"], "playable": check["representable"]})
    return rows


def total(rows):
    # Match evaluate_plan: sum each voice kind, then combine the two subtotals.
    return round(sum(pf.voice_seconds(rows, kind) for kind in ("dialogue", "narration")), 2)


def _options(row, novel, cap):
    text, kind = row["text"], row["kind"]
    if not text or "\n" in text or text not in novel:
        return []
    # Anchor to the approved utterance, not another speaker's adjacent quote.
    paragraph = next((p for p in novel.splitlines() if text in p), "")
    candidates = pf.representable_replacements(text, paragraph)
    candidates += _utterance_windows(text, cap)
    valid = []
    for t in dict.fromkeys(candidates):
        if (t != text and t in paragraph and len(_HAN.findall(t)) <= MAX_LINE_HAN
                and _estimate_duration(t) < row["sec"]
                and _check(kind, t, cap)["est_sec"] <= cap):
            valid.append(t)
    return valid


def prepare(answer, novel):
    rows = voices(answer)
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
    return {"scope": "trim" if trim else "replace", "seconds": seconds,
            "goal": min(TARGET_SEC, ceiling) if over_time else seconds,
            "rows": rows}


def prompt(ctx):
    reduction = round(ctx["seconds"] - ctx["goal"], 2)
    return ("基于上一版，只提交 edits（id 和新 text），不要重抄整稿。所有未点名的字段保持原样。"
            "替换必须从对应 replacements 中逐字选择，保持原说话人、语意、事实、节拍。"
            "仅 deletable=true 可用空串删声，不得新增声音或删节拍。不能保留主张时返回空 edits。"
            f"声音上限：{MAX_LINES} 条，{MAX_HAN} 个汉字，每条 {MAX_LINE_HAN} 字。"
            f"原稿正文合计 {ctx['seconds']} 秒，必须净缩短"
            + (f"至少 {reduction} 秒（到 {ctx['goal']} 秒以内）。" if reduction else "，锁定未超限项。")
            + "\n原稿声音清单（sec 为正文估时，single_sec 含对白前缀余量）：\n"
            + json.dumps(ctx["rows"], ensure_ascii=False))


def apply(answer, ctx, response):
    """Return a candidate only for a valid transaction. Never mutate the baseline."""
    if not isinstance(response, dict) or set(response) != {"edits"}:
        raise ValueError("修订只接受 edits，不接受整稿")
    edits = response["edits"]
    if not isinstance(edits, list) or not edits:
        raise ValueError("没有可应用的声音修订")
    result = copy.deepcopy(answer)
    by_id = {r["id"]: r for r in ctx["rows"]}
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
    if after >= ctx["seconds"] or after > ctx["goal"]:
        raise ValueError(f"净减量不足：{ctx['seconds']} → {after}s，目标 {ctx['goal']}s")
    return result
