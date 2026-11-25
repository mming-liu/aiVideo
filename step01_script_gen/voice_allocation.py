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
        "dropped_facts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "reason"],
            "properties": {"id": {"type": "string"}, "reason": {"type": "string"}}}},
        "unfixable": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "reason"],
            "properties": {"id": {"type": "string"}, "reason": {"type": "string"}}}}}}}

#: 自动修订主链用 schema：不允许模型通过 dropped_facts 擅自删事实。
SCHEMA_NO_DROP = {"name": "lightweight_voice_allocation", "strict": True, "schema": {
    "type": "object", "additionalProperties": False, "required": ["voices", "facts"],
    "properties": {k: v for k, v in SCHEMA["schema"]["properties"].items()
                   if k != "dropped_facts"}}}


def _problem(code, ref, reason):
    return {"code": code, "ref": str(ref or ""), "reason": str(reason or "")}


def _evidence_list(fact) -> list:
    ev = fact.get("evidence")
    if isinstance(ev, str):
        return [ev] if ev else []
    return [str(x) for x in (ev or []) if str(x)]


def effective_limits(limits=None) -> dict:
    """补齐本次声音事务的完整预算读数；调用方只覆盖它显式知道的键.

    条数/汉字/单条/硬上限/软目标/单镜能力**全部来自** `voice_allocation_limits()`；
    调用方传 `current_lines` 等上下文键时不会丢掉其余配置。
    """
    out = dict(vr.voice_limits())
    for key, value in dict(limits or {}).items():
        if value is not None:
            out[key] = value
    return out


def build_voice_choices(answer, novel_text, *, limits=None,
                        allow_drop_facts: bool = True) -> dict:
    """冻结回答 → 候选目录（声音、事实、受限动作）；不调用模型、不写文件."""
    limits = effective_limits(limits)
    rows = vr.voices(answer)
    characters = [str(x).strip() for x in (answer.get("characters") or []) if str(x).strip()]
    cap = float(limits["single_shot_sec"])
    catalog = {}
    for r in rows:
        recs, notes = vr._candidate_records(r, novel_text, characters, cap)
        units = []
        for i, c in enumerate(recs, 1):
            units.append({"unit": "%s:c%d" % (r["id"], i), "text": c["text"],
                          "line": c["line"], "start": c["start"], "end": c["end"],
                          "source": "own" if c["origin"] == "own" else "evidence",
                          "in_evidence": bool(c["claim_aligned"]),
                          "sec": c["sec"], "han": c["han"]})
        catalog[r["id"]] = {"id": r["id"], "kind": r["kind"], "speaker": r["speaker"],
                            "text": r["text"], "claims": r["claims"],
                            "evidence": r["evidence"], "units": units,
                            "event": r["event"], "beat": r["beat"],
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
            "allow_drop_facts": bool(allow_drop_facts),
            "prompt": prompt_text(catalog, facts, limits, allow_drop_facts=allow_drop_facts)}


def prompt_text(catalog, facts, limits, *, allow_drop_facts: bool = True) -> str:
    drop_rule = ("" if allow_drop_facts else
                 "本入口冻结全部必保留事实：不得删除任何事实（禁止返回 dropped_facts）。")
    return ("只提交一次受限声音分配，不要重抄整稿。可用动作：1) 把一条声音换成候选目录里的"
            "一条（units 只填一项）；2) 拆成多条有序单位（units 填多项，同一说话人、每条都在"
            "该声音的候选目录里，顺序即播出顺序）；3) 用 facts 指定某条事实由哪些单位共同承载"
            "（单位写成 `<声音id>:<序号>`，序号按 units 的顺序从 1 起）。\n"
            "约束：不得新增声音种类/说话人；不得把不同角色的发言拼接；每条承载正文必须逐字"
            "来自该声音自己的正文（unit.source=own）或它绑定事实的证据（unit.source=evidence）。"
            "**own 的 in_evidence=false 不是越界**——它本来就是这条声音的原文，可直接替换/拆分；"
            "完整主张由后续内容核验判定，不要因此判不可修。%s条数策略 %s、总汉字策略 %s、"
            "单条策略 %s 字、总量硬上限 %s 秒、软目标约 %s 秒（高于软目标不等于不合法）、"
            "单镜上限 %s 秒。"
            "当前稿已有 %s 条声音；超出策略时先减无事实绑定的声音，不能删必保留事实。"
            "超出预算或确实无法保住内容时，写进 unfixable 并说明缺哪一层。\n目录："
            % (drop_rule, limits["max_lines"], limits["max_han"], limits["max_line_han"],
               limits["max_sec"], limits["target_sec"], limits["single_shot_sec"],
               limits.get("current_lines", "?"))
            + json.dumps({"voices": catalog, "facts": facts}, ensure_ascii=False))



def _fact_positions(answer) -> set:
    out = set()
    for f in answer.get("facts") or []:
        out.add((int(f["event"]) - 1, int(f["beat"]) - 1))
        for c in f.get("carriers") or []:
            out.add((int(c["event"]) - 1, int(c["beat"]) - 1))
    return out


def _drop_one_fact_free(answer):
    """清理一条无事实绑定的**声音**，保留节拍（画面/来源/目的不动）.

    没有事实绑定不等于这个节拍可以删除；删除画面需要独立的允许范围。
    """
    used = _fact_positions(answer)
    for r in vr.voices(answer):
        if r["claims"] or (r["event"], r["beat"]) in used:
            continue
        beat = answer["events"][r["event"]]["beats"][r["beat"]]
        if str(beat.get("voice_kind") or "none") == "none":
            continue
        text = str(beat.get("voice_text") or "")
        beat["voice_kind"] = "none"
        beat["voice_text"] = ""
        beat["speaker"] = ""
        return {"id": r["id"], "event": r["event"], "beat": r["beat"], "text": text}
    return None


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


def apply_voice_allocation(answer, response, ctx, *, novel_text, limits=None,
                           allow_drop_facts: bool = True):
    """应用一次 v2 分配；返回 `(candidate_answer, problems)`，永不修改基线.

    位置由**基线稳定坐标**（event, beat）一次性计算，不依赖模型数组的排列顺序；
    拆分只改变节拍布局，不改说话人/声音种类，也不删除画面节拍。
    """
    limits = effective_limits(limits or ctx.get("limits") or {})
    if (not isinstance(response, dict)
            or not set(response) <= {"voices", "facts", "unfixable", "dropped_facts"}
            or not isinstance(response.get("voices"), list)
            or not isinstance(response.get("facts", []), list)):
        return None, [_problem("allocation_response_invalid", "", "只接受 voices/facts/unfixable")]
    catalog = ctx["voices"]
    problems = []
    planned = {}
    for item in response.get("voices") or []:
        if not isinstance(item, dict) or set(item) != {"id", "units"}:
            return None, [_problem("allocation_response_invalid", "", "voices 项只能有 id/units")]
        vid = str(item["id"])
        units = item.get("units")
        if vid not in catalog or not isinstance(units, list)                 or not 1 <= len(units) <= MAX_UNITS_PER_VOICE:
            problems.append(_problem("allocation_voice_invalid", vid, "声音不存在或单位数非法"))
            continue
        if vid in planned:
            problems.append(_problem("allocation_voice_duplicate", vid, "同一声音只能出现一次"))
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
        if entry["claims"] and any(
                not (t in entry["text"] or any(t in ev for ev in entry["evidence"]))
                for t in texts):
            problems.append(_problem("allocation_unit_outside_evidence", vid,
                                     "拆分单位必须逐字来自该声音正文或其事实证据"))
            continue
        event, beat = entry["event"], entry["beat"]
        base = answer["events"][event]["beats"][beat]
        if (str(base.get("voice_kind") or "") != str(entry.get("kind") or "")
                or str(base.get("speaker") or "") != str(entry.get("speaker") or "")):
            problems.append(_problem("allocation_voice_identity_mismatch", vid,
                                     "候选来源与基线声音的说话人/种类不一致"))
            continue
        planned[vid] = {"texts": texts, "event": event, "beat": beat}
    if problems:
        return None, problems

    # 1) 一次性重排每个事件的节拍，同时记录基线坐标 → 新坐标。
    result = copy.deepcopy(answer)
    newpos = {}
    positions = {}
    by_base = {(p["event"], p["beat"]): vid for vid, p in planned.items()}
    for e, ev in enumerate(answer["events"]):
        out_beats = []
        for b, base in enumerate(ev["beats"]):
            vid = by_base.get((e, b))
            if vid is None:
                newpos[(e, b)] = (e, len(out_beats))
                out_beats.append(copy.deepcopy(base))
                continue
            texts = planned[vid]["texts"]
            first = copy.deepcopy(base)
            first["voice_text"] = texts[0]
            newpos[(e, b)] = (e, len(out_beats))
            out_beats.append(first)
            made = [(e, len(out_beats) - 1)]
            for text in texts[1:]:
                nxt = copy.deepcopy(base)
                nxt["voice_text"] = text
                nxt["source"] = text
                out_beats.append(nxt)
                made.append((e, len(out_beats) - 1))
            positions[vid] = made
        result["events"][e]["beats"] = out_beats

    # 2) 事实绑定按稳定坐标映射；显式 facts[].units 优先。
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
            base_pos = (int(fact["event"]) - 1, int(fact["beat"]) - 1)
            vid = by_base.get(base_pos)
            if vid is not None:
                targets = positions[vid]
            elif fact.get("carriers"):
                targets = [newpos.get((int(c["event"]) - 1, int(c["beat"]) - 1), base_pos)
                           for c in fact["carriers"]]
            else:
                targets = [newpos.get(base_pos, base_pos)]
        fact["event"], fact["beat"] = targets[0][0] + 1, targets[0][1] + 1
        if len(targets) > 1:
            fact["carriers"] = [{"event": e + 1, "beat": b + 1} for e, b in targets]
        else:
            fact.pop("carriers", None)
    if problems:
        return None, problems

    # 3) 明确的事实取舍。
    drop_rows = response.get("dropped_facts") or []
    if not isinstance(drop_rows, list):
        return None, [_problem("allocation_dropped_facts_invalid", "", "dropped_facts 必须是数组")]
    if drop_rows and not allow_drop_facts:
        return None, [_problem("allocation_dropped_facts_forbidden", "",
                               "自动修订入口禁止未经授权删除必保留事实")]
    drop_ids = []
    for row in drop_rows:
        if (not isinstance(row, dict) or set(row) != {"id", "reason"}
                or not str(row.get("reason") or "").strip()):
            return None, [_problem("allocation_dropped_facts_invalid", "", "每项须 {id, reason}")]
        drop_ids.append(str(row.get("id")))
    if len(set(drop_ids)) != len(drop_ids):
        return None, [_problem("allocation_dropped_facts_invalid", "", "事实 id 重复")]
    by_id = {"f%02d" % (i + 1): f for i, f in enumerate(result["facts"])}
    missing = [x for x in drop_ids if x not in by_id]
    if missing:
        return None, [_problem("allocation_dropped_facts_invalid", ",".join(missing), "事实不存在")]
    dropped_info = [{"id": x, "claim": by_id[x].get("claim"), "reason": r["reason"]}
                    for x, r in zip(drop_ids, drop_rows)]
    if drop_ids:
        result["facts"] = [f for i, f in enumerate(result["facts"], 1)
                           if "f%02d" % i not in set(drop_ids)]
        result["allocation_dropped_facts"] = dropped_info

    # 4) 预算清理：只清声音字段，保留节拍；不移动任何事实坐标。
    dropped = []
    while True:
        rows_now = vr.voices(result)
        if (len(rows_now) <= int(limits["max_lines"])
                and vr.total(rows_now) <= float(limits["max_sec"]) + 0.01
                and sum(r["han"] for r in rows_now) <= int(limits["max_han"])):
            break
        one = _drop_one_fact_free(result)
        if not one:
            break
        dropped.append(one)
    if dropped:
        result["allocation_dropped"] = dropped
    rows = vr.voices(result)
    seconds, han = vr.total(rows), sum(r["han"] for r in rows)
    if (seconds > float(limits["max_sec"]) + 0.01
            or han > int(limits["max_han"])
            or len(rows) > int(limits["max_lines"])):
        return None, [_problem("allocation_budget_conflict", "",
                               "分配后仍超限：%d 条 / %d 字 / %.2fs（策略上限 %d 条 / %d 字 / 硬上限 %ss）"
                               % (len(rows), han, seconds, int(limits["max_lines"]),
                                  int(limits["max_han"]), limits["max_sec"]))]
    return result, []


__all__ = ["ENV", "PROTOCOL", "SCHEMA", "SCHEMA_NO_DROP", "build_voice_choices",
           "prompt_text", "apply_voice_allocation"]
