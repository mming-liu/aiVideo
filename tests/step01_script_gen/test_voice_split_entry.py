# -*- coding: utf-8 -*-
"""「新建声音 + 事实拆分」经真实入口 `outline.revise_plan_once` 的自足回放（零模型调用）.

匿名最小情节：复合事实的唯一载体（对白）只说了前一层，后一层在另一处已准入原话里。
桩只提供协议合法的回答 / M / R 记录；判定**不是**模型结论，只证明修法可达、可消费、
失败不落中间稿。不依赖 `.tmp`、`output` 或任何作品内容。
"""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from _text_gen.models import TextGenerationResult
from shared import content_verification as cv
from shared import presentation_contract as pc
from step01_script_gen import outline as o
from step01_script_gen import plan_content_repair as pcr
from step01_script_gen.plan_revision import RevisionBudget

LINE_A = "是我，主人已经睡下了，不必惊动。"
LINE_B = "等等，库房的账要连夜送去码头，你去备船。"
NOVEL = ("夜里，阿青推门进院。\n"
         "守门人问道：“谁在那里？”\n"
         "阿青低声道：“%s”\n"
         "守门人笑道：“那我回屋了。”\n"
         "阿青又道：“%s”\n"
         "天亮前，小船离开了码头。\n") % (LINE_A, LINE_B)
NOVEL_DIGEST = hashlib.sha256(NOVEL.encode("utf-8")).hexdigest()
#: 走生产请求路径（不进旧协议回放开关）
pytestmark = pytest.mark.v2_request

PARENT = "f02"
PARENT_CLAIM = "阿青对守门人称主人已睡下，随后又要他备船连夜送账去码头"
PARENT_SPANS = ["阿青低声道：“%s”" % LINE_A, "阿青又道：“%s”" % LINE_B]


def _view() -> dict:
    return {
        "schema": pc.PRESENTATION_SCHEMA, "mode": pc.MODE_LEGACY,
        "event_binding": "unknown",
        "frozen_facts": [
            {"uid": "f01", "claim": "阿青夜里推门进院", "evidence": "夜里，阿青推门进院。"},
            {"uid": PARENT, "claim": PARENT_CLAIM, "evidence": PARENT_SPANS[0]},
            {"uid": "f03", "claim": "天亮前小船离开码头", "evidence": "天亮前，小船离开了码头。"},
        ],
        "frozen_events": ["阿青夜归支走守门人", "小船离港"],
        "beats": [
            {"uid": "b01", "text": "阿青推开院门走进夜色里的院子", "event_uids": ["阿青夜归支走守门人"],
             "purpose": "", "evidence": "夜里，阿青推门进院。", "source": {},
             "voice_ref": {"kind": "visual", "uid": "b01"}},
            {"uid": "b02", "text": "守门人举灯发问，阿青压低声音回答", "event_uids": ["阿青夜归支走守门人"],
             "purpose": "", "evidence": PARENT_SPANS[0], "source": {},
             "voice_ref": {"kind": "dialogue", "uid": "d01"}},
            {"uid": "b03", "text": "天色将明，小船驶离码头", "event_uids": ["小船离港"],
             "purpose": "", "evidence": "天亮前，小船离开了码头。", "source": {},
             "voice_ref": {"kind": "visual", "uid": "b03"}},
        ],
        "dialogue_units": [{"uid": "d01", "text": LINE_A, "source": {"text": LINE_A}}],
        "narration_units": [],
        "screen_cards": [],
        "fact_bindings": [
            {"fact_uid": "f01", "carriers": [{"kind": "visual", "uid": "b01"}]},
            {"fact_uid": PARENT, "carriers": [{"kind": "dialogue", "uid": "d01"}]},
            {"fact_uid": "f03", "carriers": [{"kind": "visual", "uid": "b03"}]},
        ],
        "hook_bindings": {"spoken_uids": [], "carriers": []},
        "evidence_updates": [],
        "plan_basis": {"fact_uids": ["f01", PARENT, "f03"], "climax": "小船离港",
                       "causal_chain": ["阿青支走守门人→小船离港"], "content_note": "匿名样本"},
    }


def _texts(view, carrier) -> str:
    return "\n".join(str(t) for t in cv.carrier_texts(view, carrier) or [] if str(t))


def _record(view, digest, *, base=None, parent_partial=False) -> dict:
    """协议合法的 R 桩：未改动的事实沿用基线行（载体身份换成当前视图里的），其余按单一载体 supported."""
    _base_f = {r["fact_uid"]: r for r in (base or {}).get("facts") or []}
    _base_s = {r["fact_uid"]: r for r in (base or {}).get("source_support") or []}
    _obs = cv.frozen_obligations(view) or {}
    _binds = {b["fact_uid"]: (b.get("carriers") or [{}])[0] for b in view.get("fact_bindings") or []}
    facts, sources = [], []
    for f in view.get("frozen_facts") or []:
        uid, car = f["uid"], _binds.get(f["uid"]) or {}
        txt = _texts(view, car)
        if uid in _base_f:
            row = copy.deepcopy(_base_f[uid])
            for ob in row.get("obligations") or []:
                for e in ob.get("evidence") or []:
                    e["carrier_uid"] = car.get("uid")
            facts.append(row)
            sources.append(copy.deepcopy(_base_s[uid]))
            continue
        partial = parent_partial and uid == PARENT
        verdict = "partial" if partial else "supported"
        facts.append({"fact_uid": uid, "verdict": verdict,
                      "support_spans": [txt], "missing": "缺后一层" if partial else "",
                      "suggested_carriers": [],
                      "obligations": [{"obligation_id": ob.get("obligation_id"),
                                       "text": ob.get("text"), "verdict": verdict,
                                       "evidence": [{"carrier_uid": car.get("uid"),
                                                     "exhibitor_span": txt}],
                                       "missing": "缺后一层" if partial else "",
                                       "reason": "桩"}
                                      for ob in (_obs.get(uid) or [])]})
        sources.append({"fact_uid": uid, "verdict": "supported",
                        "evidence_spans": (list(PARENT_SPANS) if uid == PARENT
                                           else [f["evidence"]]), "reason": "桩"})
    voices = [{"unit_uid": u["uid"], "verdict": "supported", "source_kind": "novel_speech",
               "speaker": "阿青", "echo_text": u["text"], "reason": "桩"}
              for u in view.get("dialogue_units") or []]
    beats = [b["uid"] for b in view.get("beats") or []]
    return {"schema": cv.SCHEMA_VERIFICATION, "status": "ok", "plan_digest": digest,
            "novel_digest": NOVEL_DIGEST, "rule_version": cv.DEFAULT_RULE_VERSION,
            "facts": facts, "source_support": sources, "voice_units": voices,
            "obligation_basis": cv.obligation_basis_digest(view),
            "coherence": {"beat_uids": beats, "verdict": "supported", "conflict_beats": [],
                          "evidence_spans": [], "reason": "桩",
                          "dependencies": [{"beat_uid": u, "requires_before": beats[:i],
                                            "reason": "桩"} for i, u in enumerate(beats)]}}


def _setup():
    plan = pc.plan_from_view(_view())
    v0, vp = pc.view_from_plan(plan)
    assert not vp, vp
    r0 = _record(v0, cv.plan_digest_of(plan), parent_partial=True)
    plan["plan_verification"] = r0
    ctl = cv.VerificationController(
        verify=lambda view, digest: _record(view, digest, base=r0),
        novel_digest=NOVEL_DIGEST, max_verifications=4,
        rule_version=cv.DEFAULT_RULE_VERSION)
    assert ctl.seed(cv.plan_digest_of(plan), r0, view=v0, plan=plan)
    # 与核验问题生产者同形（`capability=view` 决定走视图/槽位接口）
    problems = [{"code": "verify_fact_unsupported", "kind": "verification",
                 "uid": PARENT, "ref": PARENT, "path": "", "fix": "", "index": None,
                 "reason": "复合事实只展示了前一层", "detail": "复合事实只展示了前一层",
                 "basis": "", "limits": {}, "capability": "view"}]
    return plan, v0, ctl, problems


class _Svc:
    def __init__(self, answer, m_verdict="supported"):
        self.answer, self.m_verdict, self.calls = answer, m_verdict, []

    def generate(self, req):
        name = str((req.json_schema or {}).get("name") or "")
        self.calls.append(name)
        if name == "fact_split_m.v1":
            return TextGenerationResult(success=True, call_id="stub-m", text=json.dumps(
                {"verdict": self.m_verdict, "missing": "" if self.m_verdict == "supported"
                 else "丢了后一层", "reason": "桩"}, ensure_ascii=False))
        return TextGenerationResult(success=True, call_id="stub-answer",
                                    text=json.dumps(self.answer, ensure_ascii=False))


def _answer(ctx, v0, *, voice_text=LINE_B, kids=None):
    vslot, sslot = ctx["voice_split"][0]["slots"]
    tmp = pcr._create_voice_temp_uid(vslot)
    return {"schema": pcr.SLOT_ANSWER_SCHEMA, "answers": [
        {"slot_id": vslot["slot_id"], "action": "create_voice", "text": voice_text,
         "voice_kind": "dialogue", "placement": "new", "insert_after_uid": "b02",
         "event_index": 1, "beat_text": "守门人转身欲走，阿青叫住他吩咐备船"},
        {"slot_id": sslot["slot_id"], "action": "split_fact", "children": kids or [
            {"claim": "阿青对守门人称主人已睡下", "evidence": LINE_A, "carrier_uid": "d01"},
            {"claim": "阿青随后要守门人备船连夜送账去码头",
             "evidence": "库房的账要连夜送去码头，你去备船", "carrier_uid": tmp}]}]}


def _run(monkeypatch, plan, problems, ctl, svc):
    monkeypatch.setattr(o, "create_service", lambda *a, **k: svc)
    return o.revise_plan_once(copy.deepcopy(plan), problems,
                              budget=RevisionBudget(max_calls=3, deadline_sec=600),
                              novel_text=NOVEL, verification=ctl)


def _voice_order(view):
    units = {u["uid"]: u["text"] for u in view.get("dialogue_units") or []}
    return [units[b["voice_ref"]["uid"]] for b in view.get("beats") or []
            if (b.get("voice_ref") or {}).get("kind") == "dialogue"]


def test_routes_to_voice_split_and_offers_only_unexpressed_line():
    plan, _v0, ctl, problems = _setup()
    ok, _why, ctx = o._content_slots_applicable(plan, problems, verification=ctl,
                                                 novel_text=NOVEL)
    assert ok and ctx["state"] == pcr.STATE_VOICE_SPLIT, ctx.get("state")
    vslot = ctx["voice_split"][0]["slots"][0]
    assert LINE_B in vslot["voice_source_spans"]
    # 已由 d01 表达的同一处原话不再作为新建候选（C）
    assert LINE_A not in vslot["voice_source_spans"]


def test_commit_projects_voice_order_and_keeps_non_targets(monkeypatch):
    plan, v0, ctl, problems = _setup()
    _ok, _w, ctx = o._content_slots_applicable(plan, problems, verification=ctl,
                                                 novel_text=NOVEL)
    svc = _Svc(_answer(ctx, v0))
    res = _run(monkeypatch, plan, problems, ctl, svc)
    assert res["kind"] == "applied", (res.get("detail"), res.get("evidence", {}).get("transaction"))
    assert svc.calls == ["plan_content_repair_answer", "fact_split_m.v1"]
    nv, vp = pc.view_from_plan(res["plan"])
    assert not vp
    back, bp = pc.view_from_plan(pc.plan_from_view(nv))
    assert not bp and back["fact_bindings"] == nv["fact_bindings"]
    # 实际声音顺序：先解释，再叫住吩咐；没有重复新增
    assert _voice_order(nv) == [LINE_A, LINE_B]
    assert len(nv["beats"]) == len(v0["beats"]) + 1
    uids = [f["uid"] for f in nv["frozen_facts"]]
    assert uids == ["f01", "f02.1", "f02.2", "f03"]
    binds = {b["fact_uid"]: b["carriers"] for b in nv["fact_bindings"]}
    kinds = {binds["f02.1"][0]["kind"], binds["f02.2"][0]["kind"]}
    assert kinds == {"dialogue"} and binds["f02.1"][0]["uid"] != binds["f02.2"][0]["uid"]
    base_non = {f["uid"]: (f["claim"], f["evidence"]) for f in v0["frozen_facts"]
                if f["uid"] != PARENT}
    new_non = {f["uid"]: (f["claim"], f["evidence"]) for f in nv["frozen_facts"]
               if "." not in f["uid"]}
    assert base_non == new_non
    # 新节拍的原文依据是新声音自己的原话，不是另一层
    new_beat = [b for b in nv["beats"]
                if (b.get("voice_ref") or {}).get("uid") == binds["f02.2"][0]["uid"]][0]
    assert LINE_B in new_beat["evidence"]


def test_split_judged_negative_leaves_no_intermediate_plan(monkeypatch):
    plan, v0, ctl, problems = _setup()
    _ok, _w, ctx = o._content_slots_applicable(plan, problems, verification=ctl,
                                                 novel_text=NOVEL)
    svc = _Svc(_answer(ctx, v0), m_verdict="contradicted")
    res = _run(monkeypatch, plan, problems, ctl, svc)
    assert res["kind"] != "applied" and res.get("plan") is None
    assert ctl.used == 0  # M 判负在 R 之前停下，不耗核验


def test_reusing_expressed_line_is_rejected_before_any_split(monkeypatch):
    plan, v0, ctl, problems = _setup()
    _ok, _w, ctx = o._content_slots_applicable(plan, problems, verification=ctl,
                                                 novel_text=NOVEL)
    svc = _Svc(_answer(ctx, v0, voice_text=LINE_A))
    res = _run(monkeypatch, plan, problems, ctl, svc)
    assert res["kind"] == "illegal" and res.get("plan") is None
    assert svc.calls == ["plan_content_repair_answer"]
    # 已表达的那处原话不在本槽来源里，回答契约就拒收（早于编译与拆分）
    assert (res.get("evidence") or {}).get("failure_stage") == "answer_contract"
    assert ctl.used == 0


@pytest.mark.parametrize("capacity_zero", [True])
def test_no_verification_capacity_does_not_route(capacity_zero):
    plan, _v0, ctl, problems = _setup()
    ctl.max_verifications = 0
    ok, _why, ctx = o._content_slots_applicable(plan, problems, verification=ctl,
                                                 novel_text=NOVEL)
    assert ctx.get("state") != pcr.STATE_VOICE_SPLIT
    assert [e["fact_uid"] for e in ctx.get("voice_split_unavailable") or []] == [PARENT]
