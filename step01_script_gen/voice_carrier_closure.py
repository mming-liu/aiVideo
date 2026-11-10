# -*- coding: utf-8 -*-
"""声音—节拍承载闭环预检（R2-4；纯函数，无模型/IO）.

只回答一个问题：**每条选中的声音、每个承载节拍、每个事实引用、实际展示正文
是否闭合**。孤儿声音、重复占用、悬空引用、事实引用不一致分别给可执行修法；
共享引用（一条旁白承载多条事实）只在读数里登记，不自动判错——是否可接受仍由
内容核验/合同判定。

本模块不替代 `carrier_relation_records` / `plan_contract_records`：
它是**更早、更具体**的确定性预检，供 R2-6 决定是否接入主门。
"""
from __future__ import annotations

from step01_script_gen import plan_feasibility as pf

VERSION = "voice_carrier_closure.v1"


def _rec(code, kind, index, path, reason, fix="") -> dict:
    return {"code": code, "kind": kind, "index": index, "path": path,
            "reason": reason, "detail": reason, "fix": fix}


def closure(plan, novel_text: str = "") -> dict:
    """返回 `{"problems": [...], "voices": [...], "facts": [...], "shared": [...]}`."""
    facts = [str(f) for f in ((plan or {}).get("required_facts") or [])]
    uids = (plan or {}).get("fact_uids")
    if not (isinstance(uids, list) and len(uids) == len(facts)):
        uids = ["f%02d" % (i + 1) for i in range(len(facts))]
    beats = [str(b) for b in ((plan or {}).get("shot_beats") or [])]
    lines = pf.plan_dialogue_lines(plan)
    narr = pf.plan_narration_entries(plan)
    beat_recs = [pf.BeatRecord.parse(b) for b in beats]
    beat_carriers = [r.carrier() for r in beat_recs]
    placements: dict = {}
    for bi, (kind, k) in enumerate(beat_carriers, 1):
        if kind in ("dialogue", "narration") and k:
            placements.setdefault((kind, int(k)), []).append(bi)

    problems: list = []
    voices: list = []
    for kind, units, label in (("dialogue", lines, "台词"),
                               ("narration", narr, "旁白")):
        for i, unit in enumerate(units, 1):
            text = (unit.get("text") if isinstance(unit, dict) else str(unit)) or ""
            placed = placements.get((kind, i), [])
            voices.append({"kind": kind, "index": i, "uid": "%s%02d" % (
                "d" if kind == "dialogue" else "n", i), "text": str(text),
                "beats": placed})
            path = "%s_lines[%d]" % ("dialogue" if kind == "dialogue" else "narration", i - 1)
            if not placed:
                problems.append(_rec(
                    "voice_unplaced", kind, i, path,
                    "%s%d 被选中却没有任何节拍承载它（成片里没人念）"
                    % (label, i),
                    "给这条声音配一条标着 `｜载体：%s%d` 的节拍；一条节拍只能标一个载体。"
                    "预算不足/不再选用时删除该声音，并同步撤销引用" % (label, i)))
            elif len(placed) > 1:
                problems.append(_rec(
                    "voice_multiply_placed", kind, i, path,
                    "%s%d 被多条节拍同时声明承载：B%s——重复占用不是唯一记录"
                    % (label, i, "、B".join(str(x) for x in placed)),
                    "只保留一条承载节拍，其余节拍改标 `｜载体：画面` 或别的合法载体"))
    for bi, (kind, k) in enumerate(beat_carriers, 1):
        if kind in ("dialogue", "narration"):
            n = len(lines) if kind == "dialogue" else len(narr)
            if not k or int(k) < 1 or int(k) > n:
                problems.append(_rec(
                    "voice_carrier_dangling", "beat", bi,
                    "shot_beats[%d].载体" % (bi - 1),
                    "B%d 声明 `载体：%s%d`，但当前只有 %d 条%s"
                    % (bi, "台词" if kind == "dialogue" else "旁白", int(k or 0),
                       n, "台词" if kind == "dialogue" else "旁白"),
                    "改标到存在的编号，或删除这条载体段；不要留悬空编号"))

    facts_out: list = []
    for fi, (uid, fact) in enumerate(zip(uids, facts), 1):
        decl = pf.fact_carrier(fact)
        parsed = pf.parse_carrier_decl(decl)
        row = {"uid": str(uid), "index": fi, "carrier": decl,
               "actual_display": "", "beats": []}
        if not decl:
            problems.append(_rec(
                "fact_carrier_missing", "fact", fi,
                "required_facts[%d].承载" % (fi - 1),
                "F%d 没有声明承载" % fi,
                "写 `｜承载：Bn/画面|台词k|旁白k`，并与节拍上的载体互指"))
            facts_out.append(row)
            continue
        if parsed is None:
            problems.append(_rec(
                "fact_carrier_decl_invalid", "fact", fi,
                "required_facts[%d].承载" % (fi - 1),
                "F%d 的承载声明格式非法：%r" % (fi, decl),
                "只写 `Bn/画面`、`Bn/台词k`、`Bn/旁白k`（单值）"))
            facts_out.append(row)
            continue
        b, mode, k = parsed
        if not (1 <= b <= len(beats)):
            problems.append(_rec(
                "fact_carrier_beat_missing", "fact", fi,
                "required_facts[%d].承载" % (fi - 1),
                "F%d 声明的承载节拍 B%d 不存在（共 %d 条节拍）"
                % (fi, b, len(beats)),
                "改指存在的节拍；或为该事实补一条承载节拍"))
            facts_out.append(row)
            continue
        row["beats"] = [b]
        beat_kind, beat_idx = beat_carriers[b - 1]
        if mode == "画面":
            row["actual_display"] = beat_recs[b - 1].body
        elif mode == "台词":
            if k < 1 or k > len(lines):
                problems.append(_rec(
                    "fact_carrier_line_missing", "fact", fi,
                    "required_facts[%d].承载" % (fi - 1),
                    "F%d 声明的承载台词 D%d 不存在（共 %d 句）"
                    % (fi, k, len(lines)),
                    "改指存在的台词，或改由画面/旁白承载"))
            else:
                row["actual_display"] = str(lines[k - 1])
                if (beat_kind, beat_idx) != ("dialogue", k):
                    problems.append(_rec(
                        "voice_fact_ref_mismatch", "fact", fi,
                        "required_facts[%d].承载" % (fi - 1),
                        "F%d 声明 B%d/台词%d，但 B%d 实际标的是 %s"
                        % (fi, b, k, b, ("台词%d" % beat_idx) if beat_kind == "dialogue"
                           else ("旁白%d" % beat_idx) if beat_kind == "narration"
                           else ("画面" if beat_kind == "visual" else "无载体")),
                        "事实承载与节拍载体必须互指；改一侧并同步另一侧"))
        else:  # 旁白
            if k < 1 or k > len(narr):
                problems.append(_rec(
                    "fact_carrier_narration_missing", "fact", fi,
                    "required_facts[%d].承载" % (fi - 1),
                    "F%d 声明的承载旁白 N%d 不存在（共 %d 条）"
                    % (fi, k, len(narr)),
                    "改指存在的旁白，或改由画面/台词承载"))
            else:
                row["actual_display"] = str(narr[k - 1].get("text") or "")
                if (beat_kind, beat_idx) != ("narration", k):
                    problems.append(_rec(
                        "voice_fact_ref_mismatch", "fact", fi,
                        "required_facts[%d].承载" % (fi - 1),
                        "F%d 声明 B%d/旁白%d，但 B%d 实际标的是 %s"
                        % (fi, b, k, b, ("旁白%d" % beat_idx) if beat_kind == "narration"
                           else ("台词%d" % beat_idx) if beat_kind == "dialogue"
                           else ("画面" if beat_kind == "visual" else "无载体")),
                        "事实承载与节拍载体必须互指；改一侧并同步另一侧"))
                refs = narr[k - 1].get("fact_refs") or []
                if fi not in refs:
                    problems.append(_rec(
                        "voice_fact_ref_mismatch", "fact", fi,
                        "narration_lines[%d].fact_refs" % (k - 1),
                        "N%d 的 fact_refs 没有登记 F%d（两处必须互相登记）"
                        % (k, fi),
                        "在旁白条目的 fact_refs 里补 F%d（整数序号）" % fi))
        if not str(row["actual_display"]).strip():
            problems.append(_rec(
                "fact_display_empty", "fact", fi,
                "required_facts[%d].承载" % (fi - 1),
                "F%d 的实际展示正文为空（载体没有正文）" % fi,
                "补节拍正文/台词/旁白；空载体不能证明事实被表达"))
        facts_out.append(row)

    # 共享引用：一条旁白被多个事实引用；只登记，不判错。
    shared = []
    for i, unit in enumerate(narr, 1):
        refs = [int(x) for x in (unit.get("fact_refs") or []) if str(x).isdigit()]
        if len(refs) > 1:
            shared.append({"kind": "narration", "index": i,
                           "uid": "n%02d" % i, "fact_refs": refs,
                           "note": "同一条旁白承载多条事实；是否成立交内容核验"})
    return {"version": VERSION, "problems": problems, "voices": voices,
            "facts": facts_out, "shared": shared}


def closure_problems(plan, novel_text: str = "") -> list:
    return closure(plan, novel_text=novel_text)["problems"]


__all__ = ["VERSION", "closure", "closure_problems"]
