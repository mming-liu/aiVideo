# -*- coding: utf-8 -*-
"""P3 生产拆分事务包装（离线可注入 M/R；本模块不建 service）.

调用顺序固定为：授权 → D → M → apply（候选副本）→ R → split_state →
plan_acceptance_problems → 可选的提交前探针。任一步失败返回失败结果，绝不提交。
"""
from __future__ import annotations

import copy

from shared import content_verification as cv
from shared import fact_split as fs
from shared import presentation_contract as pc
from step01_script_gen import outline as o
from step01_script_gen import split_judge as sj


def _fail(stage, problems, *, candidate=None, plan=None, record=None,
          m_calls=0, extra=None) -> dict:
    _out = {"ok": False, "stage": stage, "candidate": candidate,
            "plan": plan, "record": record, "problems": list(problems or []),
            "m_calls": int(m_calls)}
    if extra:
        _out.update(extra)
    return _out


def _entry_for(ledger, parent):
    for _e in (ledger or {}).get("splits") or []:
        if isinstance(_e, dict) and str(_e.get("parent_uid") or "") == str(parent):
            return _e
    return None


def run_split_transaction(base_view, base_plan, base_record, split_ops, *,
                          scope, ledger, novel_text, m_call, m_call_id,
                          verify_candidate, accept=None,
                          authority_digest=None, authorization_view=None,
                          authorization_plan=None,
                          acceptance=None) -> dict:
    """执行一笔拆分事务；`verify_candidate(candidate)->record` 是 R 注入点."""
    _base_digest = cv.plan_digest_of(base_plan)
    _auth_digest = str(authority_digest or _base_digest)
    _parent = str((split_ops or {}).get("parent_uid") or "")
    _children = [c for c in ((split_ops or {}).get("children") or [])
                 if isinstance(c, dict)]
    _entry = _entry_for(ledger, _parent)
    if not isinstance(_entry, dict):
        return _fail("authorization", [{"code": "split_ledger_missing",
                                        "reason": "账本缺父条目"}])
    _auth = fs.authority_problems(
        authorization_view if isinstance(authorization_view, dict) else base_view,
        authorization_plan if isinstance(authorization_plan, dict) else base_plan,
        ledger, [_entry], base_record,
        authority_digest=_auth_digest, novel_text=novel_text)
    if _auth:
        return _fail("authorization", _auth)
    _parent_fact = next((f for f in (base_view.get("frozen_facts") or [])
                         if str(f.get("uid")) == _parent), None)
    if not isinstance(_parent_fact, dict):
        return _fail("authorization", [{"code": "split_parent_missing",
                                        "reason": "父事实不在基线视图"}])
    _d = fs.d_problems(_parent_fact, _children,
                       _entry.get("parent_evidence_spans") or [])
    if _d:
        return _fail("d", _d)
    if m_call is None:
        return _fail("m", [{"code": "split_m_call_missing",
                            "reason": "未提供 M 调用"}])
    try:
        _m = sj.judge_split(_parent_fact.get("claim"), _children,
                            call=m_call, call_id=m_call_id)
    except Exception as exc:                                         # noqa: BLE001
        # M 调用异常按 §7.5 归 pending_adjudication（不是“判负”，也不重试）。
        return _fail("m", [{"code": "split_m_call_failed",
                            "reason": "%s: %s" % (type(exc).__name__, exc)}],
                     m_calls=1, extra={"status": "pending_adjudication",
                                       "error": str(exc),
                                       "judgment": None})
    if not _m.get("ok") or not _m.get("judgment"):
        return _fail("m", _m.get("problems") or [
            {"code": "split_m_failed", "reason": "M 未产出 judgment"}],
            m_calls=1, extra={"status": "pending_adjudication",
                              "judgment": None,
                              "m_problems": list(_m.get("problems") or [])})
    if str(_m["judgment"].get("verdict") or "") != "supported":
        return _fail("m", [{"code": "split_m_not_supported",
                            "reason": "M 判定不是 supported"}], m_calls=1,
                     extra={"status": "pending_adjudication",
                            "judgment": _m["judgment"]})
    _ledger = copy.deepcopy(ledger)
    _new_entry = _entry_for(_ledger, _parent)
    if not isinstance(_new_entry, dict):
        return _fail("m", [{"code": "split_ledger_missing",
                            "reason": "账本缺父条目"}], m_calls=1)
    _new_entry["judgment"] = _m["judgment"]
    try:
        _cand, _cp, _res = fs.apply_split_view(
            base_view, split_ops, scope=scope, ledger=_ledger, record=base_record,
            accept=lambda _v: [], authority_digest=_auth_digest,
            novel_text=novel_text, authorization_view=authorization_view,
            authorization_plan=authorization_plan)
    except Exception as exc:                                         # noqa: BLE001
        return _fail("apply", [{"code": "split_apply_failed",
                                "reason": "%s: %s" % (type(exc).__name__, exc)}],
                     m_calls=1)
    if _cand is None or _res.get("kind") != "applied":
        return _fail("apply", _cp or [{"code": "split_apply_failed",
                                       "reason": str(_res.get("detail") or "")}],
                     m_calls=1)
    try:
        _cand_plan = pc.plan_from_view(_cand)
    except pc.PresentationContractError as _exc:
        return _fail("projection", list(_exc.problems), candidate=_cand,
                     m_calls=1)
    try:
        _rec = verify_candidate(_cand)
    except Exception as exc:                                         # noqa: BLE001
        return _fail("r", [{"code": "split_r_call_failed",
                            "reason": "%s: %s" % (type(exc).__name__, exc)}],
                     candidate=_cand, plan=_cand_plan, m_calls=1,
                     extra={"error": str(exc)})
    if not isinstance(_rec, dict):
        return _fail("r", [{"code": "split_r_record_missing",
                            "reason": "R 未返回记录"}], candidate=_cand,
                     plan=_cand_plan, m_calls=1)
    _cand_plan["plan_verification"] = _rec
    _state = fs.split_state(_cand, _new_entry, record=_rec)
    if _state != "effective":
        return _fail("r_state", [{"code": "split_" + _state,
                                  "reason": "拆分状态不是 effective：%s" % _state}],
                     candidate=_cand, plan=_cand_plan, record=_rec, m_calls=1)
    try:
        _acceptance = (acceptance(_cand_plan, novel_text)
                       if acceptance is not None
                       else o.plan_acceptance_problems(_cand_plan, novel_text))
    except Exception as exc:                                         # noqa: BLE001
        return _fail("acceptance", [{"code": "split_acceptance_failed",
                                     "reason": "%s: %s"
                                     % (type(exc).__name__, exc)}],
                     candidate=_cand, plan=_cand_plan, record=_rec, m_calls=1)
    if _acceptance:
        return _fail("acceptance", _acceptance, candidate=_cand,
                     plan=_cand_plan, record=_rec, m_calls=1)
    try:
        _probe = list(accept(_cand) or []) if accept is not None else []
    except Exception as exc:                                         # noqa: BLE001
        return _fail("probe", [{"code": "split_probe_failed",
                                "reason": "%s: %s" % (type(exc).__name__, exc)}],
                     candidate=_cand, plan=_cand_plan, record=_rec, m_calls=1)
    if _probe:
        return _fail("probe", _probe, candidate=_cand, plan=_cand_plan,
                     record=_rec, m_calls=1)
    return {"ok": True, "stage": "committed", "candidate": _cand,
            "plan": _cand_plan, "record": _rec, "problems": [],
            "m_calls": 1, "state": _state}


def run_create_voice_then_split(*, v0, r0, create_voice, split_args,
                                verify_candidate, m_call, m_call_id,
                                accept=None) -> dict:
    """P3-D：create_voice V0→V1（不提交），再以 V1 为 base 拆分 V1→V2.

    拆分授权仍用 `(V0, R0)`；`V1` 的 `frozen_facts` 必须与 `V0` 逐字一致。
    失败同时丢弃 V1/V2，仅返回 V0 与失败候选供留档。
    """
    _v0_plan = pc.plan_from_view(v0)
    _v0_digest = cv.plan_digest_of(_v0_plan)
    _v1 = create_voice(v0)
    if not isinstance(_v1, dict):
        return {"ok": False, "stage": "create_voice", "baseline": v0,
                "v1": None, "v2": None,
                "problems": [{"code": "create_voice_failed",
                              "reason": "create_voice 未返回候选"}]}
    _base_facts = [(f.get("uid"), f.get("claim"), f.get("evidence"))
                   for f in (v0.get("frozen_facts") or []) if isinstance(f, dict)]
    _v1_facts = [(f.get("uid"), f.get("claim"), f.get("evidence"))
                 for f in (_v1.get("frozen_facts") or []) if isinstance(f, dict)]
    if _base_facts != _v1_facts:
        return {"ok": False, "stage": "create_voice_facts", "baseline": v0,
                "v1": _v1, "v2": None,
                "problems": [{"code": "create_voice_facts_modified",
                              "reason": "create_voice 改动了 frozen_facts"}]}
    _args = dict(split_args)
    _ledger = copy.deepcopy(_args.get("ledger") or {})
    _ledger["base_digest"] = cv.plan_digest_of(pc.plan_from_view(_v1))
    _args["ledger"] = _ledger
    _v1_plan = pc.plan_from_view(_v1)
    _res = run_split_transaction(
        _v1, _v1_plan, r0, _args.get("split_ops") or {},
        scope=_args.get("scope"), ledger=_ledger,
        novel_text=_args.get("novel_text") or "",
        m_call=m_call, m_call_id=m_call_id,
        verify_candidate=verify_candidate, accept=accept,
        authority_digest=_v0_digest, authorization_view=v0,
        authorization_plan=_v0_plan,
        acceptance=split_args.get("acceptance"))
    _out = dict(_res)
    _out.update({"baseline": v0, "v1": _v1, "v2": _res.get("candidate")})
    if not _res.get("ok"):
        _out["rollback"] = True
        _out["candidate"] = None
    return _out
