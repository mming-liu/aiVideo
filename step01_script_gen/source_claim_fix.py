"""S12：复合来源主张的受限修正（离线事务）.

只做两件事：把一条事实的 claim/evidence 更正为原文逐字证据内的自足主张，或把一条
复合事实拆成多条各自带**单段逐字证据**的子事实。身份由代码重新分配并保留父/子谱系；
不改事件/节拍、不改声音、不删除事实。语义支持由后续内容核验判定。
"""
from __future__ import annotations

import copy

SCHEMA = {"name": "lightweight_source_correction", "strict": True, "schema": {
    "type": "object", "additionalProperties": False, "required": ["splits"],
    "properties": {"splits": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "children"],
        "properties": {"id": {"type": "string"}, "children": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["claim", "evidence"],
            "properties": {"claim": {"type": "string"}, "evidence": {"type": "string"}}}}}}}}}}


def _problem(code, ref, reason):
    return {"code": code, "ref": str(ref or ""), "reason": str(reason or "")}


def apply_source_correction(answer, response, novel_text):
    """返回 `(candidate_answer, problems)`；永不修改基线."""
    if (not isinstance(response, dict) or set(response) != {"splits"}
            or not isinstance(response.get("splits"), list)):
        return None, [_problem("source_response_invalid", "", "只接受 splits 数组")]
    facts = answer.get("facts") or []
    problems = []
    by_id = {"f%02d" % (i + 1): f for i, f in enumerate(facts)}
    out = copy.deepcopy(answer)
    replaced = {}
    for item in response["splits"]:
        if (not isinstance(item, dict) or set(item) != {"id", "children"}
                or not isinstance(item.get("children"), list)
                or not 2 <= len(item["children"]) <= 4):
            return None, [_problem("source_response_invalid", "", "每项须 {id, children[2..4]}")]
        fid = str(item["id"])
        if fid not in by_id or fid in replaced:
            return None, [_problem("source_fact_unknown", fid, "事实不存在或重复拆分")]
        kids = []
        for child in item["children"]:
            if (not isinstance(child, dict) or set(child) != {"claim", "evidence"}
                    or not str(child.get("claim") or "").strip()
                    or not str(child.get("evidence") or "").strip()):
                return None, [_problem("source_child_invalid", fid, "子项须有非空 claim/evidence")]
            ev = str(child["evidence"]).strip()
            if ev not in novel_text:
                problems.append(_problem("source_evidence_not_verbatim", fid,
                                         "子项证据不是原文逐字片段：%s" % ev[:40]))
                continue
            kids.append({"claim": str(child["claim"]).strip(), "evidence": ev,
                         "event": by_id[fid]["event"], "beat": by_id[fid]["beat"],
                         "carrier": by_id[fid].get("carrier") or "voice",
                         "_source_parent": fid})
        if problems:
            continue
        replaced[fid] = kids
    if problems:
        return None, problems
    new_facts = []
    for i, fact in enumerate(out["facts"], 1):
        fid = "f%02d" % i
        if fid in replaced:
            new_facts.extend(replaced[fid])
        else:
            new_facts.append(fact)
    out["facts"] = new_facts
    out["source_splits"] = [{"parent": k, "children": [c["claim"] for c in v]}
                            for k, v in replaced.items()]
    return out, []


__all__ = ["SCHEMA", "apply_source_correction"]
