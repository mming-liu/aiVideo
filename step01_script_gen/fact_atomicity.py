# -*- coding: utf-8 -*-
"""R3-2 / R4-1：首版事实原子性预检（无模型）.

只拦**主张含多个实义分句、但整条主张未被连续原文直接支持**的事实，给出可执行拆法：
- 被连续原文片段**直接包含**的分句 → 子事实（沿用父载体）；
- 其余分句 → `needs_evidence=True`，要求补连续原文证据或收窄/删除，
  **不由代码改写成新主张**；
- 所有分句都有连续原文直接支持 → 用 R2-2 合同验证父子谱系/并集/证据，
  合法才给 `legal_split=True`。

R4-1 修正：2-gram 相似度只用于**候选检索/诊断**（`candidate_evidence` /
`candidate_similarity`），**绝不能**据此产生 `covered` 或 `needs_evidence=false`。
覆盖的唯一依据是「分句规范化后逐字出现在某个连续原文片段中」。
"""
from __future__ import annotations

import re

from shared.fact_metadata import split_claim_evidence
from step01_script_gen import plan_feasibility as pf
from step01_script_gen import restricted_split as rs

VERSION = "fact_atomicity.v2"

_SPLIT_RE = re.compile(r"[，,；;。]")
#: 仅用于候选检索的诊断阈值；不参与 covered 判定。
_CANDIDATE_SIMILARITY = 0.6


def _norm(s):
    return "".join(str(s or "").split())


def _segments(claim):
    parts = [p.strip() for p in _SPLIT_RE.split(str(claim or ""))]
    return [p for p in parts if len(_norm(p)) >= 2]


def _chunks(evidence):
    """连续证据片段：按省略号/空行/句号切开；**不**再把整段原文当连续片段."""
    raw = str(evidence or "")
    out = []
    for part in re.split(r"……|\n\s*\n|。", raw):
        part = part.strip()
        if len(_norm(part)) >= 4:
            out.append(part)
    return out


def _bigrams(text):
    s = _norm(text)
    return {s[i:i + 2] for i in range(max(0, len(s) - 1))}


def _similarity(segment, chunk):
    seg = _bigrams(segment)
    if not seg:
        return 0.0
    return len(seg & _bigrams(chunk)) / len(seg)


def _coverage(segment, chunks):
    """返回 `{direct, candidate, similarity}`.

    `direct` 非空才表示**连续原文直接支持**；`candidate` 只是诊断，不得当覆盖。
    """
    seg = _norm(segment)
    if not seg:
        return {"direct": "", "candidate": "", "similarity": 0.0}
    best_ch, best_sim = "", 0.0
    for ch in chunks:
        text = _norm(ch)
        if seg in text:
            return {"direct": ch, "candidate": ch, "similarity": 1.0}
        sim = _similarity(seg, ch)
        if sim > best_sim:
            best_ch, best_sim = ch, sim
    return {
        "direct": "",
        "candidate": best_ch if best_sim >= _CANDIDATE_SIMILARITY else "",
        "similarity": best_sim,
    }


def _covered(segment, chunks):
    """兼容旧调用：只返回**直接支持**的连续片段；候选相似度不会出现在这里."""
    return _coverage(segment, chunks)["direct"]


def _child_fact(uid, claim, evidence, carrier):
    # 子事实只用于合同验证/提案；不写回计划。
    return "%s｜原文证据：“%s”｜承载：%s" % (claim, evidence, carrier)


def atomicity_problems(plan, novel_text="") -> list:
    """逐条必保留事实返回原子性问题；合法拆法放进 `proposed_children`."""
    facts = pf.plan_facts(plan) if hasattr(pf, "plan_facts") else (
        plan.get("required_facts") or [])
    uids = plan.get("fact_uids") or []
    probs = []
    for idx, fact in enumerate(facts, 1):
        uid = str(uids[idx - 1]) if idx - 1 < len(uids) and str(uids[idx - 1]) else \
            "f%02d" % idx
        claim, evidence = split_claim_evidence(str(fact))
        segs = _segments(claim)
        # 只把**两个以上实义分句**当复合主张；像「杨旭字文轩」这种 5 字
        # 称谓/同位语不单独当命题，避免把完整的原子身份事实误拦。
        segs = [s for s in segs if len(_norm(s)) >= 6]
        if len(segs) < 2:
            continue
        chunks = _chunks(evidence)
        # 整条主张被某个连续片段直接包含：不按分句硬拆（合法原子/完整表达）。
        if any(_norm(claim) in _norm(ch) for ch in chunks):
            continue
        covered = []
        uncovered = []
        for seg in segs:
            cov = _coverage(seg, chunks)
            if cov["direct"]:
                covered.append({"claim": seg, "evidence": cov["direct"]})
            else:
                uncovered.append({
                    "claim": seg,
                    "evidence": "",
                    "needs_evidence": True,
                    "candidate_evidence": cov["candidate"],
                    "candidate_similarity": round(cov["similarity"], 4),
                })
        carrier = pf.fact_carrier(str(fact))
        children = []
        for i, seg in enumerate(covered + uncovered, 1):
            children.append({
                "uid": "%s%s" % (uid, chr(96 + i)),  # f02a/f02b…
                "claim": seg["claim"],
                "evidence": seg["evidence"],
                "carrier": carrier,
                "needs_evidence": not bool(seg["evidence"]),
                **({"candidate_evidence": seg.get("candidate_evidence", ""),
                    "candidate_similarity": seg.get("candidate_similarity", 0.0)}
                   if seg.get("candidate_evidence") else {}),
            })
        legal = not uncovered and len(children) >= 2
        split_problems = []
        if legal:
            child_facts = [_child_fact(c["uid"], c["claim"], c["evidence"],
                                       c["carrier"]) for c in children]
            split_problems = rs.split_contract_problems(
                str(fact), child_facts, novel_text=novel_text, parent_uid=uid,
                child_uids=[c["uid"] for c in children])
            legal = not split_problems
        probs.append({
            "code": ("fact_atomicity_composite" if legal
                     else "fact_atomicity_composite_uncovered"),
            "kind": "fact",
            "uid": uid,
            "path": "required_facts[%d]" % (idx - 1),
            "claim": claim,
            "covered_segments": [c["claim"] for c in covered],
            "uncovered_segments": [c["claim"] for c in uncovered],
            "proposed_children": children,
            "legal_split": bool(legal),
            "split_problems": [p.get("code") for p in split_problems],
            "reason": ("一条事实含多个分句，整条主张没有连续原文直接支持"
                       if not covered else
                       "一条事实含多个分句，现有连续证据只覆盖其中一部分（%s）"
                       % "、".join(c["claim"] for c in covered)),
            "fix": ("按 proposed_children 拆分；未直接覆盖的分句必须补连续原文证据，"
                    "或收窄/删除该分句——不得由代码改写，也不得用词面相似度冒充覆盖"
                    if uncovered else
                    "按 proposed_children 拆分并登记父子谱系；不得改写式拆分"),
        })
    return probs


def first_atomicity_problem(plan, novel_text="") -> dict | None:
    p = atomicity_problems(plan, novel_text)
    return p[0] if p else None


__all__ = ["VERSION", "atomicity_problems", "first_atomicity_problem"]
