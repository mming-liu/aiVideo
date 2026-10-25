# -*- coding: utf-8 -*-
"""P3 拆分进入修订入口的**测试夹具**（匿名样本 + 记录/服务桩）.

只依赖版本库内冻结样本；不读 output/.tmp，不发网络。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from shared import content_verification as cv
from shared import presentation_contract as pc
from shared.content_verification import VerificationController
from step01_script_gen import content_verify as cverify
from step01_script_gen import outline as o

ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLES = ROOT / "tools" / "samples"


def load_sample(name: str) -> dict:
    _p = SAMPLES / ("p3_split_entry_%s.json" % name)
    return json.loads(_p.read_text(encoding="utf-8"))


def carrier_texts(view) -> dict:
    _out = {}
    for _b in view.get("beats") or []:
        _out[str(_b.get("uid"))] = str(_b.get("text") or "")
    for _u in (view.get("dialogue_units") or []) + (view.get("narration_units") or []):
        _out[str(_u.get("uid"))] = str(_u.get("text") or "")
    return _out


def record_for(view, plan_digest, *, partial=(), missing=None, spans=None,
               source_verdict=None, novel=""):
    """夹具记录：形状/义务支座/证据与真实入口同源；裁决由参数给定.

    `source_verdict` 给**来源轴**裁决（默认 supported）；只用来构造 v2.2 的来源轴
    反例（`unknown → contradicted`），不改变记录协议。
    """
    _texts = carrier_texts(view)
    _binds = {str(b.get("fact_uid")): (b.get("carriers") or [])
              for b in (view.get("fact_bindings") or []) if isinstance(b, dict)}
    _facts, _sources, _voices = [], [], []
    for _f in view.get("frozen_facts") or []:
        _uid = str(_f.get("uid"))
        _verdict = "partial" if _uid in partial else "supported"
        _miss = str((missing or {}).get(_uid) or
                    ("未展示完整主张" if _verdict != "supported" else ""))
        _car = (_binds.get(_uid) or [{}])[0]
        _cuid = str(_car.get("uid") or "")
        _ctext = _texts.get(_cuid, "")
        _facts.append({
            "fact_uid": _uid, "verdict": _verdict,
            "support_spans": ([_ctext] if _verdict == "supported" and _ctext else []),
            "missing": _miss, "suggested_carriers": [],
            "obligations": [{
                "obligation_id": "claim", "text": str(_f.get("claim") or ""),
                "verdict": _verdict,
                "evidence": ([{"carrier_uid": _cuid, "exhibitor_span": _ctext}]
                             if _verdict == "supported" and _cuid and _ctext else []),
                "missing": _miss, "reason": ""}]})
        _sp = (spans or {}).get(_uid) or [str(_f.get("evidence") or "")]
        _sv = str((source_verdict or {}).get(_uid) or "supported")
        _sources.append({"fact_uid": _uid, "verdict": _sv,
                         "evidence_spans": [x for x in _sp if str(x)],
                         "reason": ""})
    for _u in view.get("dialogue_units") or []:
        _so = _u.get("source") if isinstance(_u.get("source"), dict) else {}
        _voices.append({"unit_uid": str(_u.get("uid")), "verdict": "supported",
                        "source_kind": "novel_speech",
                        "speaker": str(_so.get("speaker") or "（夹具：未指定说话人）"),
                        "echo_text": str(_u.get("text") or ""), "reason": ""})
    for _u in view.get("narration_units") or []:
        _voices.append({"unit_uid": str(_u.get("uid")), "verdict": "supported",
                        "source_kind": "novel_narration", "speaker": "",
                        "echo_text": str(_u.get("text") or ""), "reason": ""})
    _beats = [str(b.get("uid")) for b in (view.get("beats") or [])]
    return {
        "schema": cv.SCHEMA_VERIFICATION, "status": "ok",
        "plan_digest": str(plan_digest),
        "novel_digest": cverify.novel_identity(novel)["digest"],
        "rule_version": cv.DEFAULT_RULE_VERSION,
        "facts": _facts, "source_support": _sources, "voice_units": _voices,
        "obligation_basis": cv.obligation_basis_digest(view),
        "coherence": {
            "beat_uids": _beats, "verdict": "supported",
            "conflict_beats": [], "evidence_spans": [],
            "reason": "按计划给定的先后顺序承接",
            "dependencies": [
                {"beat_uid": _uid, "requires_before": _beats[:_i],
                 "reason": "按计划给定的先后顺序承接"}
                for _i, _uid in enumerate(_beats)]}}


def baseline(sample: dict, *, partial_extra=(), source_verdict=None) -> dict:
    novel = str(sample["novel"])
    plan = o.normalize_plan_ranges(copy.deepcopy(sample["plan"]))[0]
    view, _vp = pc.view_from_plan(plan)
    assert not _vp, _vp
    digest = cv.plan_digest_of(plan)
    _parent = str(sample["split"]["parent_uid"])
    _partial = (_parent,) + tuple(partial_extra)
    record = record_for(
        view, digest, partial=_partial,
        spans={_parent: list(sample["split"]["parent_evidence_spans"])},
        source_verdict=source_verdict, novel=novel)
    plan["plan_verification"] = record
    return {"sample": sample, "novel": novel, "plan": plan, "view": view,
            "digest": digest, "record": record, "parent": _parent}


def split_answer(sample: dict, *, extra=None, kids=None) -> dict:
    _kids = copy.deepcopy(kids if kids is not None else sample["split"]["children"])
    _children = [{"claim": c["claim"], "evidence": c["evidence"],
                  "carrier_uid": c["carrier"]["uid"]} for c in _kids]
    if extra:
        _children[0].update(extra)
    return {"schema": "plan_content_repair.answer.v1",
            "answers": [{"slot_id": "split:fact:%s" % sample["split"]["parent_uid"],
                         "action": "split_fact", "children": _children}]}


class StubService:
    def __init__(self, answer, *, m_verdict="supported", m_missing=""):
        self.answer = answer
        self.m = {"verdict": m_verdict, "missing": m_missing, "reason": ""}
        self.calls = []
        self.requests = []

    def generate(self, request):
        from _text_gen.models import TextGenerationResult
        _name = str((request.json_schema or {}).get("name") or "")
        self.calls.append(_name)
        self.requests.append(request)
        if _name == "plan_content_repair_answer":
            return TextGenerationResult(
                success=True, text=json.dumps(self.answer, ensure_ascii=False),
                call_id="stub-answer")
        if _name == "fact_split_m.v1":
            return TextGenerationResult(
                success=True, text=json.dumps(self.m, ensure_ascii=False),
                call_id="stub-m")
        raise AssertionError("unexpected schema %r" % _name)


def controller(base: dict, *, verify=None, max_verifications=4,
               repair=None, validate_record=None) -> VerificationController:
    return VerificationController(
        verify=verify or (lambda view, digest: record_for(
            view, digest, novel=base["novel"])),
        novel_digest=base["record"]["novel_digest"],
        max_verifications=max_verifications,
        rule_version=cv.DEFAULT_RULE_VERSION, repair=repair,
        validate_record=validate_record)


def run_entry(base: dict, *, answer, m_verdict="supported",
              verify=None, max_calls=1, with_r0=True, svc=None,
              repair=None, deadline_sec=60, ctl=None,
              validate_record=None):
    plan = copy.deepcopy(base["plan"])
    if not with_r0:
        plan.pop("plan_verification", None)
    ctl = ctl or controller(base, verify=verify, repair=repair,
                            validate_record=validate_record)
    _svc = svc or StubService(answer, m_verdict=m_verdict)
    _orig = o.create_service
    o.create_service = lambda *a, **k: _svc
    try:
        out = o.run_plan_revision(
            plan, base["novel"], verification=ctl, max_calls=max_calls,
            deadline_sec=deadline_sec, target_fact_uids=[base["parent"]],
            allowed_repair_actions={"split_fact"})
    finally:
        o.create_service = _orig
    return out, _svc, ctl


def evidence_of(out) -> dict:
    return ((out.get("history") or [{}])[-1] or {}).get("evidence") or {}
