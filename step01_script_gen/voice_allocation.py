"""S13：受限声音分配（实验协议 v2）.

在**冻结事实与原文**范围内，把一条声音拆成多条有序声音单位，或把一条事实交给多条
声音共同承载；模型只选候选与分配关系，编号、节拍位置、事实绑定与派生字段由代码
生成。与 v1 `edits` 并列、显式版本化；默认入口仍走 v1。

事务：冻结基线 → 一次受限响应 → 副本应用 → 投影 → 结构/引用/预算/来源重验 →
接受候选或保留基线。不做内容语义判定；"完整主张"由每条事实的**每段绑定证据都必须
被某条承载正文覆盖**做确定性下限，语义仍由后续内容核验负责。
"""
from __future__ import annotations

import copy
import json

from step01_script_gen import lightweight_voice_repair as vr

ENV = "STEP01_VOICE_ALLOCATION"
PROTOCOL = "voice_allocation.v2"
MAX_UNITS_PER_VOICE = 4

SCHEMA = {"name": "lightweight_voice_allocation", "strict": True, "schema": {
    "type": "object", "additionalProperties": False, "required": ["voices", "facts"],
    "properties": {
        "voices": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "units"],
            "properties": {"id": {"type": "string"},
                           "units": {"type": "array", "items": {"type": "string"}}}}},
        "facts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "units"],
            "properties": {"id": {"type": "string"},
                           "units": {"type": "array", "items": {"type": "string"}}}}},
        "unfixable": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "reason"],
            "properties": {"id": {"type": "string"}, "reason": {"type": "string"}}}}}}}


def _problem(code, ref, reason):
    return {"code": code, "ref": str(ref or ""), "reason": str(reason or "")}


def _evidence_list(fact) -> list:
    ev = fact.get("evidence")
    if isinstance(ev, str):
        return [ev] if ev else []
    return [str(x) for x in (ev or []) if str(x)]


def build_voice_choices(answer, novel_text, *, limits=None) -> dict:
    """冻结回答 → 候选目录（声音、事实、受限动作）；不调用模型、不写文件."""
    limits = dict(limits or {})
    rows = vr.voices(answer)
    characters = [str(x).strip() for x in (answer.get("characters") or []) if str(x).strip()]
    cap = float(limits.get("single_shot_sec") or 11)
    catalog = {}
    for r in rows:
        recs, notes = vr._candidate_records(r, novel_text, characters, cap)
        units = []
        for i, c in enumerate(recs, 1):
            units.append({"unit": "%s:c%d" % (r["id"], i), "text": c["text"],
                          "line": c["line"], "start": c["start"], "end": c["end"],
                          "in_evidence": bool(c["claim_aligned"]),
                          "sec": c["sec"], "han": c["han"]})
        catalog[r["id"]] = {"id": r["id"], "kind": r["kind"], "speaker": r["speaker"],
                            "text": r["text"], "claims": r["claims"],
                            "evidence": r["evidence"], "units": units,
                            "notes": notes, "deletable": not r["claims"]}
    by_pos = {(r["event"] + 1, r["beat"] + 1): r["id"] for r in rows}
    facts = []
    for i, f in enumerate(answer["facts"], 1):
        pos = (f.get("event"), f.get("beat"))
        voices = [by_pos[pos]] if pos in by_pos else []
        voices += [by_pos[(c.get("event"), c.get("beat"))]
                   for c in (f.get("carriers") or []) if (c.get("event"), c.get("beat")) in by_pos]
        facts.append({"id": "f%02d" % i, "claim": str(f.get("claim") or ""),
                      "evidence": _evidence_list(f), "carrier": str(f.get("carrier") or ""),
                      "voices": list(dict.fromkeys(voices))})
    return {"protocol": PROTOCOL, "voices": catalog, "facts": facts, "limits": limits,
            "prompt": prompt_text(catalog, facts, limits)}


def prompt_text(catalog, facts, limits) -> str:
    return ("只提交一次受限声音分配，不要重抄整稿。可用动作：1) 把一条声音换成候选目录里的"
            "一条（units 只填一项）；2) 拆成多条有序单位（units 填多项，同一说话人、每条都在"
            "该声音的候选目录里，顺序即播出顺序）；3) 用 facts 指定某条事实由哪些单位共同承载"
            "（单位写成 `<声音id>:<序号>`，序号按 units 的顺序从 1 起）。\n"
            "约束：不得新增声音种类/说话人；不得把不同角色的发言拼接；每条事实的**每段证据**"
            "都必须被某条承载正文逐字覆盖；条数上限 %s、汉字上限 %s、单条 %s 秒。"
            "超出预算或无法保住证据时，写进 unfixable 并说明缺哪一层。\n目录："
            % (limits.get("max_lines", 10), limits.get("max_han", 280),
               limits.get("single_shot_sec", 12))
            + json.dumps({"voices": catalog, "facts": facts}, ensure_ascii=False))


def _unit_text(catalog, voice_id, unit_ref):
    """`D2:c3` → 目录候选文本；`D2:1` 不是候选（用于事实绑定）."""
    body, _, tail = str(unit_ref or "").partition(":")
    if body != voice_id or not tail.startswith("c"):
        return None
    try:
        idx = int(tail[1:]) - 1
    except ValueError:
        return None
    units = catalog[voice_id]["units"]
    return units[idx]["text"] if 0 <= idx < len(units) else None


def apply_voice_allocation(answer, response, ctx, *, novel_text, limits=None):
    """应用一次 v2 分配；返回 `(candidate_answer, problems)`，永不修改基线."""
    limits = dict(limits or ctx.get("limits") or {})
    if (not isinstance(response, dict) or not set(response) <= {"voices", "facts", "unfixable"}
            or not isinstance(response.get("voices"), list)
            or not isinstance(response.get("facts", []), list)):
        return None, [_problem("allocation_response_invalid", "", "只接受 voices/facts/unfixable")]
    catalog = ctx["voices"]
    result = copy.deepcopy(answer)
    problems = []
    pos_map = {(e + 1, b + 1): (e, b)
               for e, ev in enumerate(answer["events"])
               for b, _ in enumerate(ev["beats"])}
    positions = {}
    shifts = {}
    for item in response.get("voices") or []:
        if not isinstance(item, dict) or set(item) != {"id", "units"}:
            return None, [_problem("allocation_response_invalid", "", "voices 项只能有 id/units")]
        vid = str(item["id"])
        units = item.get("units")
        if vid not in catalog or not isinstance(units, list) \
                or not 1 <= len(units) <= MAX_UNITS_PER_VOICE:
            problems.append(_problem("allocation_voice_invalid", vid, "声音不存在或单位数非法"))
            continue
        texts = []
        for ref in units:
            text = _unit_text(catalog, vid, ref)
            if text is None:
                problems.append(_problem("allocation_unit_unknown", ref, "候选不在目录里"))
                break
            texts.append(text)
        if len(texts) != len(units):
            continue
        entry = catalog[vid]
        if entry["claims"] and any(not any(t in ev for ev in entry["evidence"]) for t in texts):
            problems.append(_problem("allocation_unit_outside_evidence", vid,
                                     "拆分单位必须逐字落在该声音绑定的事实证据内"))
            continue
        row = next((r for r in vr.voices(answer)
                    if r["id"] == vid and r["text"] == entry["text"]), None)
        if row is None:
            problems.append(_problem("allocation_voice_missing", vid, "当前稿里找不到该声音"))
            continue
        event = row["event"]
        # 当前稿里的位置 = 基线位置 + 本事件此前插入的条数
        cur = row["beat"] + shifts.get(event, 0)
        base_beat = result["events"][event]["beats"][cur]
        base_beat["voice_text"] = texts[0]
        made = [(event, cur)]
        insert_at = cur + 1
        for text in texts[1:]:
            result["events"][event]["beats"].insert(insert_at, {
                "text": base_beat.get("text") or "",
                "purpose": base_beat.get("purpose") or "",
                "source": text,
                "voice_kind": base_beat.get("voice_kind") or "",
                "voice_text": text,
                "speaker": base_beat.get("speaker") or "",
            })
            made.append((event, insert_at))
            insert_at += 1
        # 基线位置映射：本事件里位于该声音之后的节拍整体后移
        extra = len(texts) - 1
        if extra:
            for key in list(pos_map):
                if key[0] == row["event"] + 1 and key[1] > row["beat"] + 1:
                    pos_map[key] = (pos_map[key][0], pos_map[key][1] + extra)
            shifts[event] = shifts.get(event, 0) + extra
        positions[vid] = made
    fact_updates = {str(x.get("id")): x for x in (response.get("facts") or [])
                    if isinstance(x, dict) and set(x) == {"id", "units"}}
    for i, fact in enumerate(result["facts"]):
        fid = "f%02d" % (i + 1)
        if fid in fact_updates:
            refs = fact_updates[fid].get("units")
            if not isinstance(refs, list) or not refs:
                problems.append(_problem("allocation_fact_units_invalid", fid,
                                         "facts[].units 必须是非空数组"))
                continue
            targets = []
            for ref in refs:
                vid, _, idx = str(ref).partition(":")
                try:
                    targets.append(positions[vid][int(idx) - 1])
                except (KeyError, ValueError, IndexError):
                    targets = []
                    break
            if not targets:
                problems.append(_problem("allocation_fact_unit_unknown", fid,
                                         "事实单位引用不存在"))
                continue
        else:
            base_pos = (int(fact["event"]), int(fact["beat"]))
            vid = next((v for v, ps in positions.items()
                        if ps and ps[0] == pos_map.get(base_pos)), None)
            targets = positions[vid] if vid else [pos_map.get(base_pos, base_pos)]
        # 来源下限：每条承载正文都必须在某段绑定证据内（逐字）。完整主张由后续内容
        # 核验判定；这里不把"证据字数覆盖比例"当语义判据。
        if str(fact.get("carrier") or "") == "voice":
            evidence = _evidence_list(fact)
            texts = [result["events"][e]["beats"][b].get("voice_text") or ""
                     for e, b in targets]
            for text in texts:
                if text and evidence and not any(text in ev for ev in evidence):
                    problems.append(_problem("allocation_unit_outside_evidence", fid,
                                             "承载正文不在该事实的证据内：%s" % text[:40]))
        fact["event"], fact["beat"] = targets[0][0] + 1, targets[0][1] + 1
        if len(targets) > 1:
            fact["carriers"] = [{"event": e + 1, "beat": b + 1} for e, b in targets]
        else:
            fact.pop("carriers", None)
    if problems:
        return None, problems
    rows = vr.voices(result)
    seconds, han = vr.total(rows), sum(r["han"] for r in rows)
    if (seconds > float(limits.get("max_sec") or 90) + 0.01
            or han > int(limits.get("max_han") or 280)
            or len(rows) > int(limits.get("max_lines") or 10)):
        return None, [_problem("allocation_budget_conflict", "",
                               "分配后仍超限：%d 条 / %d 字 / %.2fs（上限 %d 条 / %d 字 / %ss）"
                               % (len(rows), han, seconds, int(limits.get("max_lines") or 10),
                                  int(limits.get("max_han") or 280),
                                  limits.get("max_sec") or 90))]
    return result, []


__all__ = ["ENV", "PROTOCOL", "SCHEMA", "build_voice_choices", "prompt_text",
           "apply_voice_allocation"]
