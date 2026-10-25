# -*- coding: utf-8 -*-
"""拆分候选的**局部采纳契约**（P3 首包；纯函数，无模型/IO）.

不是“再算一遍残余码集合”，而是把拆分作用域说清：

- 目标子事实、受影响事实/声音：R2 必须 `supported`；
- 非目标内容、绑定、节拍/声音/卡片/hook/plan_basis：逐字冻结；
- 保护事实/声音：按显式 `R0 → R2` 表判；表外或 contradicted ⇒ degraded；
  允许组合但 R2 非 supported 时，`missing`/义务向量不同 ⇒ unconfirmed（不是已证退化）；
  **保护事实的展示轴与来源轴分别套同一张表**（来源轴按 `source_support.verdict`），
  残余的 `(code, uid)` 命中不能替代轴比较；
- 候选残余：不得落在目标/受影响实体；非目标残余必须按 `(code, uid)`（无 uid 时
  `(code, path)`）逐条命中基线；命中不了即拒收。

调用方负责把 `plan_acceptance_problems` 的完整残余传进来；本模块不自己算验收。
"""
from __future__ import annotations

import json

from shared import content_verification as cv
from shared import fact_split as fs
from shared import presentation_contract as pc


def _rec(code, ref, reason, path="") -> dict:
    return {"code": str(code), "kind": "split_adoption", "uid": str(ref or ""),
            "ref": str(ref or ""), "path": str(path or ""),
            "reason": str(reason), "fix": ""}


def _digest(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def _problem_uid(p) -> str:
    return str((p or {}).get("uid") or (p or {}).get("ref")
               or (p or {}).get("unit_uid") or (p or {}).get("fact_uid") or "")


def _record_maps(record) -> tuple:
    _facts = {str(x.get("fact_uid") or ""): x for x in (record or {}).get("facts") or []
              if isinstance(x, dict)}
    _src = {str(x.get("fact_uid") or ""): x
            for x in (record or {}).get("source_support") or [] if isinstance(x, dict)}
    _voices = {str(x.get("unit_uid") or ""): x
               for x in (record or {}).get("voice_units") or [] if isinstance(x, dict)}
    return _facts, _src, _voices


#: 显式 R0 → R2 表；不让执行者自行排序 partial/unknown/contradicted。
_ALLOWED_VERDICTS = {
    "supported": {"supported"},
    "partial": {"supported", "partial"},
    "unknown": {"supported", "partial", "unknown"},
    "contradicted": set(),
}


def _allowed(base_verdict, candidate_verdict) -> bool:
    return str(candidate_verdict or "") in _ALLOWED_VERDICTS.get(
        str(base_verdict or ""), set())


def _obligation_vector(row) -> list:
    return [(str(o.get("obligation_id") or ""), str(o.get("text") or ""),
             str(o.get("verdict") or ""))
            for o in ((row or {}).get("obligations") or []) if isinstance(o, dict)]


def _facts_map(view) -> dict:
    return {str(f.get("uid") or ""): f for f in (view or {}).get("frozen_facts") or []
            if isinstance(f, dict)}


def _bindings_map(view) -> dict:
    return {str(b.get("fact_uid") or ""): [dict(c) for c in (b.get("carriers") or [])
                                           if isinstance(c, dict)]
            for b in (view or {}).get("fact_bindings") or [] if isinstance(b, dict)}


def _child_carriers(candidate_view, child_uids) -> list:
    _binds = _bindings_map(candidate_view)
    _out: list = []
    for _uid in child_uids:
        for _c in _binds.get(str(_uid)) or []:
            if _c not in _out:
                _out.append(_c)
    return _out


def _facts_on_carriers(view, carriers) -> set:
    _want = {(str(c.get("kind") or ""), str(c.get("uid") or "")) for c in carriers}
    _out: set = set()
    for _b in (view or {}).get("fact_bindings") or []:
        if not isinstance(_b, dict):
            continue
        for _c in (_b.get("carriers") or []):
            if isinstance(_c, dict) and (str(_c.get("kind") or ""),
                                         str(pc.ref_uid(_c))) in _want:
                _out.add(str(_b.get("fact_uid") or ""))
                break
    return _out


def _voices_on_carriers(view, carriers) -> set:
    from shared.presentation_contract import ref_uid
    _out: set = set()
    _beats = {str(b.get("uid") or ""): b for b in (view or {}).get("beats") or []
              if isinstance(b, dict)}
    for _c in carriers:
        _kind = str(_c.get("kind") or "")
        _uid = str(_c.get("uid") or "")
        if _kind in ("dialogue", "narration"):
            _out.add(_uid)
        elif _kind == "visual":
            _vr = (_beats.get(_uid) or {}).get("voice_ref") or {}
            if str(_vr.get("kind") or "") in ("dialogue", "narration"):
                _u = ref_uid(_vr)
                if _u:
                    _out.add(str(_u))
    return _out


def adoption_problems(*, base_view, candidate_view, base_plan, candidate_plan,
                      base_record, candidate_record, parent_uid, child_uids,
                      baseline_problems, candidate_problems) -> list:
    _p: list = []
    _parent = str(parent_uid or "")
    _children = [str(x) for x in (child_uids or []) if str(x)]
    _target = {_parent} | set(_children)
    # ---- 记录身份 ----
    for _label, _plan, _record in (("base", base_plan, base_record),
                                   ("candidate", candidate_plan, candidate_record)):
        if not isinstance(_record, dict) or str(_record.get("status") or "") != "ok":
            _p.append(_rec("split_adoption_record_invalid", "",
                           "%s 记录缺失或 status 不是 ok" % _label))
            continue
        if str(_record.get("plan_digest") or "") != cv.plan_digest_of(_plan):
            _p.append(_rec("split_adoption_record_invalid", "",
                           "%s 记录的 plan_digest 与计划摘要不一致" % _label))
        if str(_record.get("rule_version") or "") != cv.DEFAULT_RULE_VERSION:
            _p.append(_rec("split_adoption_record_invalid", "",
                           "%s 记录的 rule_version 不是当前版本" % _label))
    _base_facts, _base_src, _base_voices = _record_maps(base_record)
    _cand_facts, _cand_src, _cand_voices = _record_maps(candidate_record)
    # ---- 目标子事实 ----
    for _uid in _children:
        if str((_cand_facts.get(_uid) or {}).get("verdict") or "") != "supported" \
                or str((_cand_src.get(_uid) or {}).get("verdict") or "") != "supported":
            _p.append(_rec("split_adoption_child_unsupported", _uid,
                           "子事实 %s 未获 R2 双轴 supported" % _uid))
    # ---- 受影响事实/声音 ----
    _carriers = _child_carriers(candidate_view, _children)
    _affected = _facts_on_carriers(base_view, _carriers) - _target
    for _uid in sorted(_affected):
        if str((_cand_facts.get(_uid) or {}).get("verdict") or "") != "supported" \
                or str((_cand_src.get(_uid) or {}).get("verdict") or "") != "supported":
            _p.append(_rec("split_adoption_affected_unsupported", _uid,
                           "受影响事实 %s 未获 R2 双轴 supported" % _uid))
    _affected_voices = _voices_on_carriers(base_view, _carriers)
    for _uid in sorted(_affected_voices):
        _b = _base_voices.get(_uid) or {}
        _c = _cand_voices.get(_uid) or {}
        if str(_c.get("verdict") or "") != "supported":
            _p.append(_rec("split_adoption_voice_unsupported", _uid,
                           "受影响声音 %s 未获 R2 supported" % _uid))
        for _k in ("echo_text", "source_kind", "speaker"):
            if str(_b.get(_k) or "") != str(_c.get(_k) or ""):
                _p.append(_rec("split_adoption_voice_unsupported", _uid,
                               "受影响声音 %s 的 %s 与 R0 不同" % (_uid, _k)))
                break
    # ---- 保护事实/声音：显式 R0 → R2 表（展示轴与来源轴**各自**比较）----
    for _uid, _brow in _base_facts.items():
        if _uid in _target or _uid in _affected:
            continue
        _crow = _cand_facts.get(_uid)
        if not isinstance(_crow, dict):
            _p.append(_rec("split_adoption_record_invalid", _uid,
                           "保护事实 %s 在 R2 记录里缺失" % _uid))
            continue
        _b = str(_brow.get("verdict") or "")
        _c = str(_crow.get("verdict") or "")
        _display_ok = _allowed(_b, _c)
        if not _display_ok:
            _p.append(_rec("split_adoption_degraded", _uid,
                           "保护事实 %s 的展示轴 R2 verdict=%r 不在 R0=%r 的允许组合内"
                           % (_uid, _c, _b)))
        # **来源轴**：与展示轴同一张显式表，按记录的 `verdict` 核对；`evidence_spans`
        # / `reason` 只留档（span 宽度会变，不新增协议）。来源 `unknown → contradicted`
        # 在这里阻断——残余里两侧都可能是 `fact_basis_invalid/(uid)`，命中不能代替轴比较。
        _bsrc = _base_src.get(_uid)
        _csrc = _cand_src.get(_uid)
        if not isinstance(_bsrc, dict) or not isinstance(_csrc, dict):
            _p.append(_rec("split_adoption_record_invalid", _uid,
                           "保护事实 %s 的来源轴记录缺失（R0/R2），不能当作无退化"
                           % _uid))
        else:
            _bv = str(_bsrc.get("verdict") or "")
            _cv = str(_csrc.get("verdict") or "")
            if not _allowed(_bv, _cv):
                _p.append(_rec("split_adoption_degraded", _uid,
                               "保护事实 %s 的来源轴 R2 verdict=%r 不在 R0=%r 的"
                               "允许组合内" % (_uid, _cv, _bv)))
        if _display_ok and _c != "supported":
            _bmiss = str(_brow.get("missing") or "")
            _cmiss = str(_crow.get("missing") or "")
            if _bmiss != _cmiss:
                _p.append(_rec("split_adoption_unconfirmed", _uid,
                               "保护事实 %s 仍非 supported，且 missing 与 R0 不同；"
                               "只能记无法确认、待裁决" % _uid))
            elif _obligation_vector(_brow) != _obligation_vector(_crow):
                _p.append(_rec("split_adoption_unconfirmed", _uid,
                               "保护事实 %s 的 R2 义务向量与 R0 不同；"
                               "只能记无法确认、待裁决" % _uid))
    for _uid, _brow in _base_voices.items():
        if _uid in _affected_voices:
            continue
        if _uid not in _cand_voices:
            _p.append(_rec("split_adoption_voice_unsupported", _uid,
                           "保护声音 %s 在 R2 记录里缺失" % _uid))
            continue
        _c = _cand_voices.get(_uid) or {}
        _b = _brow
        if not _allowed(_b.get("verdict"), _c.get("verdict")):
            _p.append(_rec("split_adoption_degraded", _uid,
                           "保护声音 %s 的 R2 verdict=%r 不在 R0=%r 的允许组合内"
                           % (_uid, _c.get("verdict"), _b.get("verdict"))))
            continue
        for _k in ("echo_text", "source_kind", "speaker"):
            if str(_b.get(_k) or "") != str(_c.get(_k) or ""):
                _p.append(_rec("split_adoption_unconfirmed", _uid,
                               "保护声音 %s 的 %s 与 R0 不同；只能记无法确认"
                               % (_uid, _k)))
                break
    # ---- 非目标结构冻结 ----
    _base_facts_view = _facts_map(base_view)
    _cand_facts_view = _facts_map(candidate_view)
    for _uid, _f in _base_facts_view.items():
        if _uid in _target:
            continue
        _cf = _cand_facts_view.get(_uid)
        if not isinstance(_cf, dict) or (
                str(_cf.get("claim") or ""), str(_cf.get("evidence") or "")) != (
                str(_f.get("claim") or ""), str(_f.get("evidence") or "")):
            _p.append(_rec("split_adoption_frozen_modified", _uid,
                           "非目标事实 %s 的主张/证据被改动" % _uid, "frozen_facts"))
    _base_bind = _bindings_map(base_view)
    _cand_bind = _bindings_map(candidate_view)
    for _uid, _cars in _base_bind.items():
        if _uid in _target:
            continue
        if _cand_bind.get(_uid) != _cars:
            _p.append(_rec("split_adoption_frozen_modified", _uid,
                           "非目标事实 %s 的绑定被改动" % _uid, "fact_bindings"))
    for _key in ("beats", "dialogue_units", "narration_units", "screen_cards",
                 "evidence_updates", "hook_bindings", "frozen_events"):
        if _digest((base_view or {}).get(_key)) != _digest(
                (candidate_view or {}).get(_key)):
            _p.append(_rec("split_adoption_frozen_modified", "",
                           "`%s` 被拆分事务改动" % _key, _key))
    _bb = {k: v for k, v in ((base_view or {}).get("plan_basis") or {}).items()
           if k not in ("fact_uids", fs.CONTEXT_FIELD)}
    _cb = {k: v for k, v in ((candidate_view or {}).get("plan_basis") or {}).items()
           if k not in ("fact_uids", fs.CONTEXT_FIELD)}
    if _digest(_bb) != _digest(_cb):
        _p.append(_rec("split_adoption_frozen_modified", "",
                       "`plan_basis`（除 fact_uids/拆分账本）被改动", "plan_basis"))
    # ---- deferred 残余逐条映射 ----
    _base_keys = {(_problem_uid(p), str((p or {}).get("code") or ""),
                   str((p or {}).get("path") or "")) for p in (baseline_problems or [])
                  if isinstance(p, dict)}
    for _p0 in (candidate_problems or []):
        if not isinstance(_p0, dict):
            continue
        _uid = _problem_uid(_p0)
        _code = str(_p0.get("code") or "")
        _path = str(_p0.get("path") or "")
        if _code.startswith("split_") or _code == "verify_fact_split_context":
            _p.append(_rec("split_adoption_scope_residual", _uid,
                           "候选仍有拆分状态残余：%s" % _code, _path))
            continue
        if _uid and (_uid in _target or _uid in _affected
                     or _uid in _affected_voices):
            _p.append(_rec("split_adoption_scope_residual", _uid,
                           "候选残余落在拆分目标/受影响事实上：%s" % _code, _path))
            continue
        if _uid:
            if not any(_k[0] == _uid and _k[1] == _code for _k in _base_keys):
                _p.append(_rec("split_adoption_unmapped_residual", _uid,
                               "候选残余 %s/%s 在基线里没有同一身份的记录"
                               % (_uid, _code), _path))
        else:
            if _code == "split_pending" or _path.startswith("frozen_facts"):
                _p.append(_rec("split_adoption_unmapped_residual", "",
                               "无 uid 的候选残余无法映射：%s" % _code, _path))
            elif not any(_k[0] == "" and _k[1] == _code and _k[2] == _path
                         for _k in _base_keys):
                _p.append(_rec("split_adoption_unmapped_residual", "",
                               "无 uid 的候选残余 %s@%s 在基线里没有逐字同一路径"
                               % (_code, _path), _path))
    return _p


__all__ = ["adoption_problems"]
