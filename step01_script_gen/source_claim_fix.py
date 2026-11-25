"""S12：复合来源主张的受限修正（离线事务）.

只做两件事：把一条事实的 claim/evidence 更正为原文逐字证据内的自足主张，或把一条
复合事实拆成多条各自带**单段逐字证据**的子事实。身份由代码重新分配并保留父/子谱系；
不改事件/节拍、不改声音、不删除事实。语义支持由后续内容核验判定。
"""
from __future__ import annotations

import copy

from step01_script_gen.source_revision import PRESERVATION_SCHEMA

SCHEMA = {"name": "lightweight_source_correction", "strict": True, "schema": {
    "type": "object", "additionalProperties": False, "required": ["splits", "source_preservation"],
    "properties": {"splits": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "children"],
        "properties": {"id": {"type": "string"}, "children": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["claim", "evidence"],
            "properties": {"claim": {"type": "string"}, "evidence": {"type": "string"}}}}}}},
        "source_preservation": PRESERVATION_SCHEMA}}}


def _problem(code, ref, reason):
    return {"code": code, "ref": str(ref or ""), "reason": str(reason or "")}


_CONTENT_PUNCT = "，。！？；：、“”‘’（）《》〈〉【】…—·,.!?;:\"'()[]{}<>\n\r\t "


def _content_chars(text) -> set:
    return {ch for ch in str(text or "") if ch not in _CONTENT_PUNCT}


def _coverage(parent, children_text) -> float:
    want = _content_chars(parent)
    if not want:
        return 1.0
    return len(want & _content_chars(children_text)) / len(want)


def apply_source_correction(answer, response, novel_text, *, allowed_parents=None):
    """返回 `(candidate_answer, problems)`；永不修改基线.

    `allowed_parents` 非空时只允许改这些事实（来源修正入口应传当前负面结论授权的身份）。
    子项必须覆盖父主张/父证据的**内容字符**（确定性下限，不代替语义核验）。
    """
    if (not isinstance(response, dict) or not set(response) <= {"splits", "source_preservation"}
            or not isinstance(response.get("splits"), list)):
        return None, [_problem("source_response_invalid", "", "只接受 splits 与 source_preservation")]
    if not isinstance(response.get("source_preservation"), dict):
        return None, [_problem("source_preservation_missing", "",
                               "来源修正必须附独立的核心保留结论")]
    _sp = response["source_preservation"]
    _sp_keys = {"target_uids", "verdict", "evidence_spans", "reason"}
    if set(_sp) != _sp_keys or not isinstance(_sp.get("target_uids"), list) \
            or not isinstance(_sp.get("evidence_spans"), list) \
            or not str(_sp.get("reason") or "").strip():
        return None, [_problem("source_preservation_record", "",
                               "保留结论须含 target_uids/verdict/evidence_spans/reason")]
    _targets = [str(x["id"]) for x in response["splits"]]
    if list(_sp["target_uids"]) != _targets:
        return None, [_problem("source_preservation_identity", "",
                               "保留结论的 target_uids 必须与拆分事实同序一致")]
    if _sp.get("verdict") != "supported":
        return None, [_problem("source_preservation_negative", "",
                               "核心保留结论为 %r，拒绝写入" % _sp.get("verdict"))]
    _spans = [str(x) for x in _sp["evidence_spans"]]
    if not _spans or any(not x.strip() or x not in novel_text for x in _spans):
        return None, [_problem("source_preservation_evidence", "",
                               "保留结论的证据必须逐字来自原文且非空")]
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
        if allowed_parents is not None and fid not in set(allowed_parents):
            return None, [_problem("source_fact_not_authorized", fid,
                                   "该事实没有当前来源负面结论授权")]
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
        parent = by_id[fid]
        # 字符覆盖只作诊断（未经校准，不作生产硬门）；完整性由保留结论 + 内容核验负责。
        claim_cov = _coverage(parent.get("claim"), "".join(k["claim"] for k in kids))
        ev_cov = _coverage(parent.get("evidence"), "".join(k["evidence"] for k in kids))
        for k in kids:
            k["_diagnostic_parent_claim_coverage"] = round(claim_cov, 3)
            k["_diagnostic_parent_evidence_coverage"] = round(ev_cov, 3)
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
    out["source_preservation"] = _sp
    out["source_splits"] = [{"parent": k,
                             "parent_claim": by_id[k].get("claim"),
                             "parent_evidence": by_id[k].get("evidence"),
                             "children": [{"claim": c["claim"], "evidence": c["evidence"]}
                                          for c in v]}
                            for k, v in replaced.items()]
    return out, []


__all__ = ["SCHEMA", "apply_source_correction"]
