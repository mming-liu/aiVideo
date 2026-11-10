# -*- coding: utf-8 -*-
"""受限事实拆分合同（R2-2；纯函数，无模型/IO）.

目标：给「复合事实只表达一半」一条**可审计**的合法修法，不把 partial 降 advisory。

本模块只做确定性判据：
  · 父事实 2–4 个孩子，孩子 uid 以父 uid 开头且唯一；
  · 每个孩子有非空主张、连续原文证据、单值承载；
  · 孩子主张按顺序**恰好拼回父主张**（归一化标点/空白后逐字覆盖）——
    这保证主体、受事、时间、否定、条件、因果限定不丢也不新增；改写式拆分
    不在这里放行（交模型判定/人工裁决）；
  · 已满足的非目标事实（正文、证据、承载）冻结；
  · 父事实被孩子取代，**不是**简单删除；谱系单独登记。

真正的语义等价仍由内容核验/模型判定兜底；本模块不冒充语义判据。
"""
from __future__ import annotations

import re

from shared.fact_metadata import split_claim_evidence
from step01_script_gen import plan_feasibility as pf

VERSION = "restricted_split.v1"
LINEAGE_FIELD = "fact_split_lineage"
MIN_CHILDREN = 2
MAX_CHILDREN = 4

_DROP = re.compile(r"[\s，。；：、！？…,.!?;:《》“”‘’\"'（）()\[\]【】—\-]+")


def _norm_claim(text) -> str:
    return _DROP.sub("", str(text or ""))


def _facts(plan) -> list:
    return [str(f) for f in ((plan or {}).get("required_facts") or [])]


def _uids(plan, n: int) -> list:
    raw = (plan or {}).get("fact_uids")
    if isinstance(raw, list) and len(raw) == n and all(str(x).strip() for x in raw):
        return [str(x) for x in raw]
    return ["f%02d" % (i + 1) for i in range(n)]


def _rec(code, index, path, reason, fix="") -> dict:
    return {"code": code, "kind": "fact", "index": index, "path": path,
            "reason": reason, "detail": reason, "fix": fix}


def _child_rows(children, parent_uid, child_uids=None) -> list:
    out = []
    _uids = [str(x) for x in (child_uids or [])]
    for i, c in enumerate(children or [], 1):
        _fallback_uid = _uids[i - 1] if i - 1 < len(_uids) else ""
        if isinstance(c, str):
            claim, ev = split_claim_evidence(c)
            carrier = pf.fact_carrier(c)
            out.append({"uid": _fallback_uid, "claim": claim, "evidence": ev,
                        "carrier": carrier, "raw": c, "index": i})
        elif isinstance(c, dict):
            claim = str(c.get("claim") or "")
            ev = str(c.get("evidence") or "")
            carrier = c.get("carrier")
            if isinstance(carrier, dict):
                # 视图的 carrier 是 {kind, uid}，要换成 `画面/台词k/旁白k` 需要
                # 视图的编号表；本模块不猜。只接受显式 text/raw，否则按缺失报。
                carrier = str(carrier.get("text") or carrier.get("raw") or "")
            out.append({"uid": str(c.get("uid") or _fallback_uid), "claim": claim,
                        "evidence": ev, "carrier": str(carrier or ""),
                        "raw": c, "index": i})
        else:
            out.append({"uid": _fallback_uid, "claim": "", "evidence": "",
                        "carrier": "", "raw": c, "index": i})
    return out


def split_contract_problems(parent_fact, children, novel_text="",
                            parent_uid="", child_uids=None) -> list:
    """父子拆分合同（不判语义等价；覆盖式逐字判据 + 单载体 + 证据连续）."""
    _parent_claim, _parent_ev = split_claim_evidence(parent_fact)
    _parent_uid = str(parent_uid or "")
    _rows = _child_rows(children, _parent_uid, child_uids=child_uids)
    _p: list = []
    if not (MIN_CHILDREN <= len(_rows) <= MAX_CHILDREN):
        _p.append(_rec("split_child_count", None, "children",
                       "拆分必须是 %d–%d 个子事实（现在是 %d 条）"
                       % (MIN_CHILDREN, MAX_CHILDREN, len(_rows)),
                       "按独立命题拆 2–4 条；过多/过少都不算原子拆分"))
        return _p
    _uids = [r["uid"] for r in _rows]
    if any(not u for u in _uids):
        _p.append(_rec("split_child_uid_missing", None, "children",
                       "每个子事实都要有稳定 uid", "用父 uid 前缀生成，如 f02a/f02b"))
    if len(set(_uids)) != len(_uids):
        _p.append(_rec("split_child_uid_duplicate", None, "children",
                       "子事实 uid 重复：%s" % _uids, "保证每个 uid 唯一"))
    if _parent_uid:
        for r in _rows:
            if r["uid"] and not r["uid"].startswith(_parent_uid):
                _p.append(_rec("split_child_uid_prefix", r["index"],
                               "children[%d].uid" % (r["index"] - 1),
                               "子事实 uid %r 不以父 uid %r 开头"
                               % (r["uid"], _parent_uid),
                               "用 %s 前缀编号，保留父子谱系" % _parent_uid))
    _parent_norm = _norm_claim(_parent_claim)
    _norms: list = []
    _novel_norm = pf._source_norm(str(novel_text or ""))       # noqa: SLF001
    for r in _rows:
        i = r["index"]
        if not r["claim"].strip():
            _p.append(_rec("split_child_claim_empty", i,
                           "children[%d].claim" % (i - 1),
                           "第 %d 个子事实没有主张正文" % i, "补一条独立主张"))
        if not r["evidence"].strip():
            _p.append(_rec("split_child_evidence_empty", i,
                           "children[%d].evidence" % (i - 1),
                           "第 %d 个子事实没有原文证据" % i,
                           "补一段连续原文证据"))
        else:
            _ev_norm = pf._source_norm(r["evidence"])          # noqa: SLF001
            if not _novel_norm or _ev_norm not in _novel_norm:
                _p.append(_rec("split_child_evidence_not_in_novel", i,
                               "children[%d].evidence" % (i - 1),
                               "第 %d 个子事实的证据不在原文里" % i,
                               "逐字引用原文；不要改写/概括"))
            _fact = "%s｜原文证据：“%s”｜承载：%s" % (
                r["claim"], r["evidence"], r["carrier"] or "画面")
            _noncontig = pf.fact_evidence_noncontiguous(_fact, novel_text)
            if _noncontig:
                _p.append(_rec("split_child_evidence_noncontiguous", i,
                               "children[%d].evidence" % (i - 1),
                               "第 %d 个子事实的证据不是连续原文：%s"
                               % (i, _noncontig),
                               "每条子事实只引用一段连续原文"))
        if not r["carrier"].strip():
            _p.append(_rec("split_child_carrier_missing", i,
                           "children[%d].carrier" % (i - 1),
                           "第 %d 个子事实没有载体" % i,
                           "给单值载体：画面 / 台词k / 旁白k"))
        elif pf.parse_carrier_decl(r["carrier"]) is None:
            _p.append(_rec("split_child_carrier_invalid", i,
                           "children[%d].carrier" % (i - 1),
                           "第 %d 个子事实的载体 %r 非法"
                           % (i, r["carrier"]),
                           "只写画面 / 台词k / 旁白k（单值）"))
        _n = _norm_claim(r["claim"])
        if not _n:
            _p.append(_rec("split_child_claim_empty_norm", i,
                           "children[%d].claim" % (i - 1),
                           "第 %d 个子事实的主张归一后为空" % i,
                           "补可核验的主张正文"))
        elif _parent_norm and _n not in _parent_norm:
            _p.append(_rec("split_child_claim_not_covered", i,
                           "children[%d].claim" % (i - 1),
                           "第 %d 个子事实的主张不是父主张的连续片段：%r"
                           % (i, r["claim"]),
                           "只拆父主张里已有的连续片段；改写/新增事实不算拆分"))
        _norms.append(_n)
    if _parent_norm and "".join(_norms) != _parent_norm:
        _p.append(_rec("split_claim_union_mismatch", None, "children",
                       "子主张按顺序拼接 %r 与父主张 %r 不等价——主体/受事/时间/"
                       "因果/否定/条件不得丢失或新增"
                       % ("".join(_norms), _parent_norm),
                       "按父主张的连续片段重拆；改写式拆分交人工/模型判定"))
    if len(set(_norms)) != len(_norms):
        _p.append(_rec("split_child_claim_duplicate", None, "children",
                       "子主张重复：%s" % _norms, "每个孩子必须是独立命题"))
    return _p


def lineage_record(parent_uid, child_uids, parent_claim="", basis="") -> dict:
    return {"version": VERSION, "parent_uid": str(parent_uid),
            "child_uids": [str(x) for x in (child_uids or [])],
            "parent_claim": str(parent_claim or ""),
            "basis": str(basis or "restricted_split")}


def plan_split_problems(base_plan, candidate_plan, parent_uid, child_uids,
                        novel_text="") -> list:
    """应用后的局部保护：目标替换、非目标事实/承载冻结、谱系登记."""
    _base_facts = _facts(base_plan)
    _base_uids = _uids(base_plan, len(_base_facts))
    _cand_facts = _facts(candidate_plan)
    _cand_uids = _uids(candidate_plan, len(_cand_facts))
    _parent = str(parent_uid or "")
    _children = [str(x) for x in (child_uids or [])]
    _p: list = []
    if not _children:
        # 先于任何 uid/长度比较：只删父（或拿占位事实顶长度）不是拆分，
        # 是**核心语义丢失**——不能靠"fact_uids 长度相同"混过去。
        return [_rec("split_children_empty", None, "children",
                     "父事实 %s 被拆掉但没有任何子事实——这是核心语义丢失，"
                     "不是拆分；用占位事实凑长度同样拒收" % _parent,
                     "至少给 2 个覆盖父主张的连续片段；不能只删父")]
    if _parent not in _base_uids:
        return [_rec("split_parent_missing", None, "fact_uids",
                     "父事实 %r 不在基线 fact_uids 里" % _parent,
                     "确认父 uid；不要按位置猜")]
    _base_map = dict(zip(_base_uids, _base_facts))
    _cand_map = dict(zip(_cand_uids, _cand_facts))
    if _parent in _cand_map:
        _p.append(_rec("split_parent_not_removed", None, "required_facts",
                       "父事实 %s 仍在候选里；拆分应替换父条目，不能父子并存"
                       % _parent, "删除父条目、保留孩子，并登记谱系"))
    _missing = [c for c in _children if c not in _cand_map]
    if _missing:
        _p.append(_rec("split_child_missing", None, "required_facts",
                       "候选缺少子事实：%s" % _missing,
                       "每个孩子都要落成一条 required_fact"))
    _expected_len = len(_base_uids) - 1 + len(_children)
    if len(_cand_uids) != _expected_len:
        _p.append(_rec("split_fact_count_mismatch", None, "fact_uids",
                       "候选事实数 %d，期望 %d（父 1 条 → 孩子 %d 条）"
                       % (len(_cand_uids), _expected_len, len(_children)),
                       "不要删父凑数/重复孩子；只做一次目标替换"))
    _target = set(_children) | {_parent}
    _base_non = [u for u in _base_uids if u not in _target]
    _cand_non = [u for u in _cand_uids if u not in set(_children)]
    if _base_non != _cand_non:
        _p.append(_rec("split_non_target_uid_changed", None, "fact_uids",
                       "非目标事实 uid/顺序被改动：base=%s cand=%s"
                       % (_base_non, _cand_non),
                       "非目标事实与编号必须冻结"))
    for uid in _base_non:
        _b = _base_map.get(uid) or ""
        _c = _cand_map.get(uid) or ""
        if not _c:
            continue
        if (pf.strip_fact_carrier(_b) != pf.strip_fact_carrier(_c)
                or pf.fact_carrier(_b) != pf.fact_carrier(_c)):
            _p.append(_rec("split_non_target_fact_changed", None,
                           "required_facts[%s]" % uid,
                           "非目标事实 %s 的正文/证据/承载被改动" % uid,
                           "非目标事实及承载冻结；只改目标事实"))
    # 非目标事实的承载节拍正文必须仍在候选里逐字存在（节拍可增删，但已有承载不许改）。
    _cand_beats = [str(b) for b in ((candidate_plan or {}).get("shot_beats") or [])]
    for uid in _base_non:
        _decl = pf.fact_carrier(_base_map.get(uid) or "")
        _parsed = pf.parse_carrier_decl(_decl)
        if not _parsed:
            continue
        _b, _kind, _k = _parsed
        _base_beats = [str(x) for x in ((base_plan or {}).get("shot_beats") or [])]
        if 1 <= _b <= len(_base_beats):
            _bt = _base_beats[_b - 1]
            if _bt not in _cand_beats:
                _p.append(_rec("split_non_target_beat_changed", None,
                               "shot_beats[B%d]" % _b,
                               "非目标事实 %s 的承载节拍 B%d 正文被改动/删除"
                               % (uid, _b),
                               "承载关系冻结；只改目标事实的节拍"))
    _lineage = (candidate_plan or {}).get(LINEAGE_FIELD)
    _rows = _lineage if isinstance(_lineage, list) else []
    _hit = [x for x in _rows if isinstance(x, dict)
            and str(x.get("parent_uid") or "") == _parent
            and [str(c) for c in (x.get("child_uids") or [])] == _children]
    if not _hit:
        _p.append(_rec("split_lineage_missing", None, LINEAGE_FIELD,
                       "候选没有登记父子谱系 %s → %s" % (_parent, _children),
                       "写 fact_split_lineage（父/孩子 uid + 依据）；禁止只删父"))
    # 目标孩子的合同（用基线父事实 + 候选孩子事实）。
    _parent_fact = _base_map.get(_parent) or ""
    _child_facts = []
    for c in _children:
        _raw = _cand_map.get(c)
        if _raw:
            _child_facts.append(_raw)
    if _parent_fact and _child_facts:
        _p.extend(split_contract_problems(
            _parent_fact, _child_facts, novel_text=novel_text,
            parent_uid=_parent, child_uids=_children))
    return _p


__all__ = ["VERSION", "LINEAGE_FIELD", "MIN_CHILDREN", "MAX_CHILDREN",
           "split_contract_problems", "lineage_record", "plan_split_problems"]
