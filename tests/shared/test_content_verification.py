# -*- coding: utf-8 -*-
"""R4：独立内容核验（`shared/content_verification.py` + `step01_script_gen/content_verify.py`）.

规格：`docs/aiVideo-step01-planning-closure-2026-10-04.md` **R4** /
`docs/aiVideo-screen-hybrid-plan-2026-10-04.md` **§3.3**。

R §2.3 的两组反例钉住本模块存在的理由：

- **误收**：事实「周明去年辞职」+ 证据「仓库管理员周明辞职。」，实际旁白只说
  「仓库管理员周明」——旧判据只要求 6 字连续重合，统一验收返回 0 问题；
- **误拒**：证据「…辞职，仓库仍旧正常营业。」，旁白已表达辞职，却因没复述营业被拒。

所以"完整支持"必须由**独立核验**给出；确定性一侧只核**记录的形状与身份**，特别是：
**支持片段必须逐字出自实际载体正文**——引用不能冒充已展示内容。
"""
from __future__ import annotations

import copy
import hashlib
import pytest
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import content_verification as cv                               # noqa: E402
from shared import presentation_contract as pc                               # noqa: E402

_CLAIM = "周明去年递了辞呈，账目随后由林青接管"
#: 事实的「当前有效证据」——注意它与**冻结原文**不是同一句（原文里是"周明去年递了辞呈。"）：
#: 这正是要核的那件事（引文可能是写错的那一份）。
_EVIDENCE = "仓库管理员周明去年递了辞呈。账目随后由林青接管。"
#: **冻结原文正文**（核验请求带的就是它）：来源片段必须在**这里**定位。
_NOVEL = "雨下了三天。仓库管理员周明去年递了辞呈。账目随后由林青接管。北岸仓库还有货位。"
_NOVEL_DIGEST = hashlib.sha256(_NOVEL.encode("utf-8")).hexdigest()


def _view(carriers=None, *, claims=None, cards=None):
    """一份最小新模式视图（事实 → 载体关系可定制）."""
    _car = carriers or [{"kind": pc.KIND_NARRATION, "uid": "n01"}]
    _f = [{"uid": "f01", "claim": _CLAIM, "evidence": _EVIDENCE}]
    for _i, _c in enumerate((claims or [_CLAIM])[1:], 2):
        _f.append({"uid": "f%02d" % _i, "claim": _c, "evidence": _EVIDENCE})
    return {
        "schema": pc.PRESENTATION_SCHEMA, "mode": pc.MODE_HYBRID,
        "event_binding": "declared",
        "frozen_facts": _f, "frozen_events": ["甲"],
        "beats": [{"uid": "b01", "event_uids": ["甲"], "text": "周明放下辞呈",
                   "purpose": "交代", "evidence": _EVIDENCE, "source": {},
                   "voice_ref": {"kind": pc.KIND_NARRATION, "uid": "n01"}}],
        "dialogue_units": [],
        "narration_units": [{"uid": "n01", "text": _EVIDENCE, "source": {"text": _EVIDENCE}}],
        "screen_cards": list(cards or []),
        "fact_bindings": [{"fact_uid": x["uid"], "carriers": _car} for x in _f],
        "hook_bindings": {"spoken_uids": [], "carriers": []},
        "evidence_updates": [],
        "plan_basis": {},
    }


def _obligations(view, fact, verdict):
    """给一条事实造**义务行**（v2：`supported` 必须覆盖全部义务）.

    义务点名该事实的**第一个载体**，片段逐字取该载体的实际正文——所以默认造出来的
    记录在义务轴上**自洽**；要测义务轴本身的用例另造（见「义务轴」一节）。
    """
    _bind = next((b for b in view["fact_bindings"] if b["fact_uid"] == fact["uid"]), {})
    _ref = next(iter(_bind.get("carriers") or []), None)
    _uid = pc.ref_uid(_ref) if _ref else ""
    _texts = [t for t in (cv.carrier_texts(view, _ref) if _ref else []) if t]
    _ok = verdict == cv.VERDICT_SUPPORTED and bool(_texts)
    return [{"obligation_id": cv.CLAIM_OBLIGATION_ID,
             "text": str(fact.get("claim") or ""),
             "verdict": cv.VERDICT_SUPPORTED if _ok else cv.VERDICT_PARTIAL,
             "evidence": ([{"carrier_uid": _uid, "exhibitor_span": _texts[0]}]
                          if _ok else []),
             "missing": "" if _ok else "（夹具：非 supported，未写明缺什么）",
             "reason": ""}]


def _record(view, *, verdict=cv.VERDICT_SUPPORTED, spans=None, status="ok",
            voices=None, digest=None, source_verdict=None, source_spans=None):
    """一份**自洽**的核验记录（三条轴 + 义务轴：来源 / 载体 / 声音 / 逐项义务）.

    ⚠️ 来源片段与事实**按位置配对**：用 `zip` 而不是"在推导式里引用另一个推导式的循环
    变量"（复核 2026-10-05 指出的 `s` 未定义——那是本轮大量核验用例在**构造夹具**时
    失败的直接原因，测的判据根本没跑到）。
    """
    _d = digest if digest is not None else cv.plan_digest_of(
        cv.view_candidate(view, view))
    # **义务支座同样由代码盖章**（生产里是 `VerificationController.record_for`）：
    # 夹具直接造记录时也要带上，否则统一验收会（正确地）报支座不一致。
    _basis = cv.obligation_basis_digest(view)
    if status != "ok":
        return {"schema": cv.SCHEMA_VERIFICATION, "status": status,
                "rule_version": cv.DEFAULT_RULE_VERSION, "plan_digest": _d,
                "novel_digest": _NOVEL_DIGEST,
                "source_support": [], "facts": [], "voice_units": [],
                "error": "核验调用失败：timeout"}
    _sv = (source_verdict if source_verdict is not None else cv.VERDICT_SUPPORTED)
    _facts = list(view["frozen_facts"])
    _ss = list(source_spans if source_spans is not None
               else [str(f.get("evidence") or "") for f in _facts])
    if len(_ss) < len(_facts):                       # 不足则按空片段补齐（测试可故意）
        _ss += [""] * (len(_facts) - len(_ss))
    _source = [{"fact_uid": f["uid"], "verdict": _sv,
                "evidence_spans": ([_s] if _s else []), "reason": ""}
               for f, _s in zip(_facts, _ss)]
    _carrier = [{"fact_uid": f["uid"], "verdict": verdict,
                 "support_spans": list(spans if spans is not None else [_EVIDENCE]),
                 "missing": "", "suggested_carriers": [],
                 "obligations": _obligations(view, f, verdict)}
                for f in _facts]
    _voice = list(voices if voices is not None else [
        {"unit_uid": pc.ref_uid(u), "verdict": cv.VERDICT_SUPPORTED,
         "source_kind": "novel_speech", "speaker": "周明",
         "echo_text": str(u.get("text") or ""), "reason": ""}
        for u in view["narration_units"] + view["dialogue_units"]])
    return {
        "schema": cv.SCHEMA_VERIFICATION, "status": "ok",
        "obligation_basis": _basis,
        "rule_version": cv.DEFAULT_RULE_VERSION, "plan_digest": _d,
        "novel_digest": _NOVEL_DIGEST,
        "source_support": _source,          # 问 1：原文是否支持主张
        "facts": _carrier,                   # 问 2：实际载体是否完整表达
        "voice_units": _voice,               # 问 3：声音出处与说话人
    }


def _codes(probs):
    return [p["code"] for p in probs]


# ---------------------------------------------------------------------------
# ① 记录形状与身份
# ---------------------------------------------------------------------------
def test_missing_record_is_rejected_not_treated_as_content_ok():
    """缺核验记录 ⇒ 明确拒收（**核验失败不当作内容可接受**）."""
    _v = _view()
    _p = cv.verification_problems(None, _v, plan=cv.view_candidate(_v, _v))
    assert "verify_record_missing" in _codes(_p), _codes(_p)


def test_unknown_schema_and_digest_mismatch_are_rejected():
    _v = _view()
    _plan = cv.view_candidate(_v, _v)
    _r = _record(_v)
    _r["schema"] = "别的.v1"
    assert "verify_schema_unknown" in _codes(
        cv.verification_problems(_r, _v, plan=_plan))
    _r2 = _record(_v, digest="0" * 64)
    assert "verify_digest_mismatch" in _codes(
        cv.verification_problems(_r2, _v, plan=_plan))


def test_rule_version_change_invalidates_the_record():
    """规则版本变了就得重新核验——旧记录不能直接复用."""
    _v = _view()
    _plan = cv.view_candidate(_v, _v)
    _r = _record(_v)
    assert cv.verification_problems(_r, _v, plan=_plan) == [], \
        _codes(cv.verification_problems(_r, _v, plan=_plan))
    assert "verify_rule_version_mismatch" in _codes(
        cv.verification_problems(_r, _v, plan=_plan,
                                 rule_version="content_verification.v2"))


def test_every_frozen_fact_needs_exactly_one_result():
    _v = _view(claims=[_CLAIM, "第二件事", "第三件事"])
    _plan = cv.view_candidate(_v, _v)
    _r = _record(_v)
    _r["facts"] = _r["facts"][:1]                       # 缺两项
    assert "verify_fact_missing" in _codes(
        cv.verification_problems(_r, _v, plan=_plan))
    _v2 = _view()
    _r2 = _record(_v2)
    _r2["facts"].append(dict(_r2["facts"][0]))          # 重复一项
    assert "verify_fact_duplicate" in _codes(
        cv.verification_problems(_r2, _v2, plan=cv.view_candidate(_v2, _v2)))


# ---------------------------------------------------------------------------
# ② 支持片段必须**逐字出自实际载体正文**
# ---------------------------------------------------------------------------
def test_support_span_must_come_from_the_actual_carrier_text():
    """**引用不能冒充已展示内容**：片段只出现在「原文证据」里、载体正文里没有 ⇒ 拒收."""
    _v = _view(carriers=[{"kind": pc.KIND_NARRATION, "uid": "n01"}])
    _v["narration_units"][0]["text"] = "周明放下辞呈"      # 载体只说了这一句
    _plan = cv.view_candidate(_v, _v)
    _ok = _record(_v, spans=["周明放下辞呈"])
    assert cv.verification_problems(_ok, _v, plan=_plan) == [], \
        _codes(cv.verification_problems(_ok, _v, plan=_plan))
    _bad = _record(_v, spans=[_EVIDENCE])                 # 证据里的原文，载体没说过
    got = _codes(cv.verification_problems(_bad, _v, plan=_plan))
    assert "verify_support_span_not_exhibited" in got, got


def test_purpose_alone_cannot_support_a_fact():
    """**「本镜信息目的」是声明，观众看不到**（工作包 2 第 3 条）——它不能当证据."""
    _v = _view(carriers=[{"kind": pc.KIND_VISUAL, "uid": "b01"}])
    _v["narration_units"] = []
    _v["beats"][0]["voice_ref"] = None
    _v["beats"][0]["text"] = "周明把辞呈摁在桌面上"        # 画面正文里没有"林青接管"
    _v["beats"][0]["purpose"] = "交代林青接管账目"         # 只有声明里有
    _plan = cv.view_candidate(_v, _v)
    _r = _record(_v, spans=["交代林青接管账目"])
    got = _codes(cv.verification_problems(_r, _v, plan=_plan))
    assert "verify_support_span_not_exhibited" in got, got
    # 合法对照：正文里真的有这句话 ⇒ 通过
    _v2 = _view(carriers=[{"kind": pc.KIND_VISUAL, "uid": "b01"}])
    _v2["narration_units"] = []
    _v2["beats"][0]["voice_ref"] = None
    _v2["beats"][0]["text"] = "林青接管账目"
    _r2 = _record(_v2, spans=["林青接管账目"])
    assert cv.verification_problems(_r2, _v2,
                                    plan=cv.view_candidate(_v2, _v2)) == []


def test_supported_without_a_span_is_rejected():
    _v = _view()
    _r = _record(_v, spans=[])
    assert "verify_support_span_missing" in _codes(
        cv.verification_problems(_r, _v, plan=cv.view_candidate(_v, _v)))


def test_joint_carriers_are_unioned_before_matching():
    """**联合承载**：片段出现在任一载体正文里就算展示过."""
    _v = _view(carriers=[{"kind": pc.KIND_VISUAL, "uid": "b01"},
                         {"kind": pc.KIND_CARD, "uid": "c01"}],
               cards=[{"card_id": "c01", "beat_uid": "b01", "template": "background",
                       "text": "林青接管账目"}])
    _v["narration_units"] = []
    _v["beats"][0]["voice_ref"] = None
    _r = _record(_v, spans=["林青接管账目"])
    assert cv.verification_problems(_r, _v,
                                    plan=cv.view_candidate(_v, _v)) == [], \
        _codes(cv.verification_problems(_r, _v, plan=cv.view_candidate(_v, _v)))


# ---------------------------------------------------------------------------
# ②' 来源支座与载体支座**分账**（工作包 2 第 2 条）
# ---------------------------------------------------------------------------
def test_source_support_and_carrier_support_are_two_axes():
    """一个 `supported` 不能代替两问：来源轴与载体轴**各自**必须有结论."""
    _v = _view()
    _plan = cv.view_candidate(_v, _v)
    _r = _record(_v)
    assert cv.verification_problems(_r, _v, plan=_plan) == [], \
        _codes(cv.verification_problems(_r, _v, plan=_plan))
    # 删掉来源轴 ⇒ 明确拒收
    _r2 = _record(_v)
    del _r2["source_support"]
    assert "verify_source_support_missing" in _codes(
        cv.verification_problems(_r2, _v, plan=_plan))


def test_source_not_supporting_the_claim_is_a_fact_basis_problem():
    """原文不支持冻结主张 ⇒ `fact_basis_invalid`，**不许**悄悄改主张换通过."""
    _v = _view()
    _plan = cv.view_candidate(_v, _v)
    _r = _record(_v, source_verdict=cv.VERDICT_PARTIAL, source_spans=[])
    _r["source_support"][0]["reason"] = "原文只有辞呈，没有林青接管"
    _got = _codes(cv.verification_problems(_r, _v, plan=_plan))
    assert "fact_basis_invalid" in _got, got
    _p = [x for x in cv.verification_problems(_r, _v, plan=_plan)
          if x["code"] == "fact_basis_invalid"][0]
    assert "不许" in _p["reason"] and "改写主张" in _p["reason"]


def test_source_supported_but_carrier_incomplete_is_distinguished():
    """**来源支持但正文不完整** ≠ **来源不支持**：两个 code 不同，修法也不同."""
    _v = _view()
    _plan = cv.view_candidate(_v, _v)
    _r = _record(_v, source_verdict=cv.VERDICT_SUPPORTED,
                 source_spans=[_EVIDENCE], verdict=cv.VERDICT_PARTIAL, spans=[])
    _r["facts"][0]["missing"] = "缺时间"
    got = _codes(cv.verification_problems(_r, _v, plan=_plan))
    assert "verify_fact_unsupported" in got and "fact_basis_invalid" not in got, got


# ---------------------------------------------------------------------------
# ③ 三个结论的可用性
# ---------------------------------------------------------------------------
def test_partial_contradicted_unknown_cannot_be_executable():
    _v = _view()
    _plan = cv.view_candidate(_v, _v)
    for _bad in (cv.VERDICT_PARTIAL, cv.VERDICT_CONTRADICTED):
        _r = _record(_v, verdict=_bad, spans=[])
        _r["facts"][0]["missing"] = "缺时间"
        _got = _codes(cv.verification_problems(_r, _v, plan=_plan))
        assert "verify_fact_unsupported" in _got, (_bad, _got)
    _r = _record(_v, verdict=cv.VERDICT_UNKNOWN, spans=[])
    _got = _codes(cv.verification_problems(_r, _v, plan=_plan))
    assert "verify_fact_unknown" in _got, _got
    assert cv.unsupported_facts(_record(_v, verdict=cv.VERDICT_PARTIAL, spans=[]))


def test_failed_verification_is_not_accepted_as_content_ok():
    _v = _view()
    for _st in (cv.FAIL_CALL, cv.FAIL_BAD_RESPONSE, cv.FAIL_DEADLINE):
        _r = _record(_v, status=_st)
        _got = _codes(cv.verification_problems(_r, _v,
                                               plan=cv.view_candidate(_v, _v)))
        assert "verify_not_available" in _got, (_st, _got)


# ---------------------------------------------------------------------------
# ④ 声音来源：原文逐字命中 ≠ 原文人物发言
# ---------------------------------------------------------------------------
def test_voice_unit_must_be_a_real_novel_speech_with_a_speaker():
    _v = _view()
    _r = _record(_v, voices=[{"unit_uid": "n01", "verdict": cv.VERDICT_SUPPORTED,
                              "source_kind": "novel_narration", "speaker": "",
                              "echo_text": str(_v["narration_units"][0].get("text") or ""),
                              "reason": "这是叙述段"}])
    _got = _codes(cv.verification_problems(_r, _v,
                                           plan=cv.view_candidate(_v, _v)))
    assert "verify_voice_not_speech" in _got, _got


def test_voice_units_must_cover_every_unit_exactly():
    _v = _view()
    _r = _record(_v, voices=[])
    assert "verify_voice_units_mismatch" in _codes(
        cv.verification_problems(_r, _v, plan=cv.view_candidate(_v, _v)))


def test_saved_voice_uids_are_used_not_regenerated_positions():
    """uid 取**计划里保存的**稳定身份，不按数组位置重编（工作包 2 第 4 条）."""
    _v = _view()
    _v["dialogue_units"] = [{"uid": "dz7", "text": "这里以后就交给林青了。",
                             "source": {}}]
    _v["narration_units"] = []
    _echo = "这里以后就交给林青了。"
    # 载体正文换成这句，`support_spans` 才能真的从**实际载体正文**里定位。
    # ⚠️ 必须在算 `_plan` **之前**改完：`plan` 与 `view` 必须��**同一份候选**的两个
    # 投影（工作包 1 第 1 条），先算 plan 再改 view 就会让两者分叉、摘要自然对不上。
    _v["beats"][0]["text"] = _echo
    # 事实的载体改成这条**对白**（旁白表已清空，载体还挂在 n01 上就成了悬空）
    _v["beats"][0]["voice_ref"] = {"kind": pc.KIND_DIALOGUE, "uid": "dz7"}
    _v["fact_bindings"] = [{"fact_uid": "f01",
                            "carriers": [{"kind": pc.KIND_DIALOGUE,
                                          "uid": "dz7"}]}]
    _plan = cv.view_candidate(_v, _v)
    # 记录用**保存的** uid、且回带本条正文 ⇒ 通过
    _ok = _record(_v, spans=[_echo],
                  voices=[{"unit_uid": "dz7", "verdict": cv.VERDICT_SUPPORTED,
                           "source_kind": "novel_speech", "speaker": "周明",
                           "echo_text": _echo, "reason": ""}])
    assert cv.verification_problems(_ok, _v, plan=_plan) == [], \
        _codes(cv.verification_problems(_ok, _v, plan=_plan))
    # 记录按位置编号成 d01 ⇒ 拒收（"d01" 是凭空造的身份）
    _bad = _record(_v, spans=[_echo],
                   voices=[{"unit_uid": "d01", "verdict": cv.VERDICT_SUPPORTED,
                            "source_kind": "novel_speech", "speaker": "周明",
                            "echo_text": _echo, "reason": ""}])
    assert "verify_voice_units_mismatch" in _codes(
        cv.verification_problems(_bad, _v, plan=_plan))
    # 回带的正文与这条单位不符 ⇒ 拒收（不能只查 speaker 非空）
    _wrong = _record(_v, spans=[_echo],
                     voices=[{"unit_uid": "dz7", "verdict": cv.VERDICT_SUPPORTED,
                              "source_kind": "novel_speech", "speaker": "周明",
                              "echo_text": "另一句台词", "reason": ""}])
    assert "verify_voice_text_mismatch" in _codes(
        cv.verification_problems(_wrong, _v, plan=_plan))


def test_duplicate_or_dangling_voice_records_are_rejected():
    _v = _view()
    _v["dialogue_units"] = [{"uid": "dz7", "text": "这句话", "source": {}},
                            {"uid": "dz9", "text": "那句话", "source": {}}]
    _v["narration_units"] = []
    _plan = cv.view_candidate(_v, _v)
    _one = [{"unit_uid": "dz7", "verdict": cv.VERDICT_SUPPORTED,
             "source_kind": "novel_speech", "speaker": "甲", "echo_text": "这句话",
             "reason": ""}]
    assert "verify_voice_units_mismatch" in _codes(
        cv.verification_problems(_record(_v, voices=_one), _v, plan=_plan))
    _dup = _one + _one
    assert "verify_voice_units_mismatch" in _codes(
        cv.verification_problems(_record(_v, voices=_dup), _v, plan=_plan))


# ---------------------------------------------------------------------------
# ⑤ 控制器：一个摘要一次、上限、共截止
# ---------------------------------------------------------------------------
def test_one_verification_per_digest_and_reused_on_repeat():
    calls = []

    def _v(view, digest):
        calls.append(digest)
        return _record(view)

    _c = cv.VerificationController(verify=_v)
    _view0 = _view()
    _d = cv.plan_digest_of(_view0)
    _r1 = _c.record_for(_view0, _d)
    _r2 = _c.record_for(_view0, _d)
    assert len(calls) == 1, "同一摘要被核验了两次"
    assert _r1 is _r2
    assert _c.used == 1


def test_digest_is_stamped_by_code_not_by_the_verifier():
    """核验方**自报**的摘要不作数——由控制器盖章."""
    def _v(view, digest):
        return _record(view, digest="deadbeef")

    _c = cv.VerificationController(verify=_v)
    _v0 = _view()
    _plan = cv.view_candidate(_v0, _v0)
    _r = _c.record_for(_v0, cv.plan_digest_of(_plan))
    assert _r["plan_digest"] == cv.plan_digest_of(_plan)
    assert cv.verification_problems(_r, _v0, plan=_plan) == [], \
        _codes(cv.verification_problems(_r, _v0, plan=_plan))


def test_the_obligation_basis_is_stamped_by_code_not_by_the_verifier():
    """**义务支座由代码盖章**（与 `plan_digest` / `novel_digest` 同一条规矩）.

    核验方自报的支座**不作数**：控制器盖的是**本次视图算出来**的那一个——否则一份
    "自己声明待核集合"的记录就能绕过集合核对（复核 2026-10-09 P1 的同一条根因）。
    """
    _v = _view()
    _plan = cv.view_candidate(_v, _v)

    def _fn(view, digest):
        _r = _record(view, digest=digest)
        _r["obligation_basis"] = "f" * 64               # 核验方自报（错的）
        return _r

    _c = cv.VerificationController(verify=_fn)
    _r = _c.record_for(_v, cv.plan_digest_of(_plan))
    assert _r["obligation_basis"] == cv.obligation_basis_digest(_v), \
        "义务支座必须由代码盖章，不接受核验方自报"
    assert cv.verification_problems(_r, _v, plan=_plan) == [], \
        _codes(cv.verification_problems(_r, _v, plan=_plan))


def test_verification_budget_is_bounded_and_failures_are_not_content_ok():
    """坏响应**不许**被 `setdefault("status","ok")` 盖成"核验成功"（旧写法只判是不是对象）."""
    def _v(view, digest):
        return {"nope": True}

    _c = cv.VerificationController(verify=_v, max_verifications=2)
    for _i in range(4):
        _vv = _view()
        _vv["frozen_facts"][0]["claim"] = _CLAIM + str(_i)
        _r = _c.record_for(_vv)
        assert _r["status"] != "ok", "第 %d 次核验不该成功" % _i
    assert _c.used == 2, _c.used


def test_bad_response_is_recorded_as_bad_response_not_as_ok():
    _c = cv.VerificationController(verify=lambda v, d: {"facts": "不是数组"})
    _r = _c.record_for(_view())
    assert _r["status"] == cv.FAIL_BAD_RESPONSE
    assert "facts" in _r["error"]
    # 缺 `source_support` 同样是坏响应（三条轴缺一不可）
    _c2 = cv.VerificationController(
        verify=lambda v, d: {"facts": [], "voice_units": []})
    assert _c2.record_for(_view())["status"] == cv.FAIL_BAD_RESPONSE


def test_deadline_exhausted_is_recorded_as_failed():
    _c = cv.VerificationController(verify=lambda v, d: _record(v),
                                   deadline_at=0.0, clock=lambda: 1.0)
    _r = _c.record_for(_view())
    assert _r["status"] == cv.FAIL_DEADLINE
    assert _c.used == 0, "过点后不该再发调用"


def test_protocol_correction_echoes_only_the_verifier_answer():
    """协议纠正**不许**把本地证据包（上一轮整份请求正文）回显给核验员.

    2026-11-06 隔离重跑实测：`previous_response=json.dumps(_rec)` 把
    `_call_evidence.prompt` 一起带回，纠正请求 21,448 → 67k → … → 54,234,784
    字符，最后一轮 300s 超时（既没拿到记录，也没有任何响应字节）。
    """
    _v = _view()
    _dig = cv.plan_digest_of(cv.view_candidate(_v, _v))
    echoes = []

    def _verify(view, digest):
        rec = _record(view, digest=digest)
        rec["_call_evidence"] = {"prompt": "P" * 5000, "raw": "R" * 100}
        return rec

    def _validate(rec, view, digest):
        return [{"code": "verify_fake_protocol", "kind": "verification",
                 "ref": "", "uid": "", "path": "", "reason": "x", "fix": ""}]

    def _repair(view, digest, failure, previous):
        echoes.append(previous)
        rec = _record(view, digest=digest)
        rec["_call_evidence"] = {"prompt": "P" * 5000, "raw": "R" * 100}
        return rec

    _c = cv.VerificationController(verify=_verify, validate_record=_validate,
                                   repair=_repair, max_verifications=2)
    _c.record_for(_v, _dig)

    assert echoes, "没有发起协议纠正"
    assert all("P" * 100 not in _e for _e in echoes), \
        "上一轮请求正文被回显进纠正请求"


# ---------------------------------------------------------------------------
# ⑥ 请求装配：原文上下文、三问分开、引文不混入展示正文
# ---------------------------------------------------------------------------
def test_verification_prompt_carries_the_frozen_novel_identity_and_context():
    """核验请求**必须带原文身份与原文**——否则「原文是否支持主张」无从判断."""
    from step01_script_gen.content_verify import build_verification_prompt, novel_identity
    _v = _view()
    _txt = build_verification_prompt(_v, novel_text=_EVIDENCE,
                                     novel_digest="abc123", novel_version="v9")
    assert "abc123" in _txt and "v9" in _txt
    assert _EVIDENCE in _txt, "原文正文没进请求"
    assert "问 1" in _txt and "source_support" in _txt, "来源轴没被问"
    _id = novel_identity(_EVIDENCE)
    assert _id["digest"] and _id["version"].startswith("novel_sha256:")


def test_verification_prompt_separates_claims_evidence_and_actual_text():
    from step01_script_gen.content_verify import build_verification_prompt
    _v = _view(carriers=[{"kind": pc.KIND_NARRATION, "uid": "n01"}])
    _v["narration_units"][0]["text"] = "周明放下辞呈"          # 实际展示的只有这句
    # 原文正文用**另一段**（不含引文）：否则「引文只出现在证据栏」这条断言数的是
    # 原文栏 + 证据栏两处，测不出"引文有没有被混进展示正文栏"
    _other = "北岸仓库的货位已经清点完毕。"
    _txt = build_verification_prompt(_v, novel_text=_other, novel_digest="d")
    assert "主张（**冻结**，你不能改写它）：%s" % _CLAIM in _txt
    assert "当前有效证据：%s" % _EVIDENCE in _txt
    assert "周明放下辞呈" in _txt
    # 关键：**引文不是展示正文**——引文只该出现在「当前有效证据」那一行。
    # 「实际展示正文」那一行必须是**别的**句子（此处是载体正文"周明放下辞呈"）。
    _lines = _txt.splitlines()
    _carrier_lines = [ln for ln in _lines if ln.strip().startswith("- [")]
    assert _carrier_lines, "没有实际展示正文栏"
    assert all("周明放下辞呈" in ln for ln in _carrier_lines), _carrier_lines
    assert all(_EVIDENCE not in ln for ln in _carrier_lines), \
        "引文被当成了展示正文"


def _display_view() -> dict:
    """带 `purpose`（声明）与原文的视图——用来证明**展示任务不送它们**."""
    _v = _view(carriers=[{"kind": pc.KIND_VISUAL, "uid": "b01"},
                         {"kind": pc.KIND_NARRATION, "uid": "n01"}])
    _v["beats"][0]["purpose"] = "交代周明辞职并交出账目"
    return _v


def test_the_display_task_sends_only_the_frozen_claim_and_the_shown_text():
    """**隔离展示任务**：请求只含冻结主张 + 给定义务 + 实际展示正文.

    机械断言禁入项**不在**请求里：小说原文、当前出处、`purpose`（声明）、来源/声音结论、
    预期标签。这一条不靠「我记得没放」——输入快照与渲染出来的正文都逐字查。
    """
    from step01_script_gen import content_verify as V
    _v = _display_view()
    _in = V.display_task_input(_v)
    _txt = V.build_display_prompt(_v)

    def _keys(obj, depth=0):
        if depth > 6:
            return set()
        if isinstance(obj, dict):
            _k = set(obj)
            for _x in obj.values():
                _k |= _keys(_x, depth + 1)
            return _k
        if isinstance(obj, list):
            _k = set()
            for _x in obj:
                _k |= _keys(_x, depth + 1)
            return _k
        return set()

    assert not (_keys(_in) & set(V.DISPLAY_FORBIDDEN_KEYS)), _keys(_in)
    assert _NOVEL not in _txt, "请求里出现了小说原文"
    assert "交代周明辞职并交出账目" not in _txt, "请求里出现了声明（purpose）"
    _w = _want_row(_v, "f01")
    assert _w["text"] in _txt, "冻结主张/给定义务没进请求"
    assert _EVIDENCE in _txt, "实际展示正文没进请求"


def _display_payload(view):
    """一份**合法**的展示响应（按给定义务回带证据）."""
    from shared.content_verification import frozen_obligations
    _w = frozen_obligations(view)["f01"][0]
    return {"facts": [{"fact_uid": "f01", "verdict": cv.VERDICT_SUPPORTED,
                       "support_spans": [_EVIDENCE], "missing": "",
                       "suggested_carriers": [],
                       "obligations": [{"obligation_id": _w["obligation_id"],
                                        "text": _w["text"],
                                        "verdict": cv.VERDICT_SUPPORTED,
                                        "evidence": [{"carrier_uid": "n01",
                                                      "exhibitor_span": _EVIDENCE}],
                                        "missing": "", "reason": ""}]}]}


def _stub_display_service(monkeypatch, payload):
    """只替换**模型服务**：请求照常构造，返回给定正文（复核 r3 用例的同一手法）."""
    import json as _json
    from step01_script_gen import outline as _o
    _calls: list = []

    class _Svc:
        def generate(self, req):
            _calls.append(req)

            class _R:
                success = True
                error = ""
                call_id = "display-stub"
                text = _json.dumps(payload, ensure_ascii=False)
            return _R()

    monkeypatch.setattr(_o, "create_service", lambda *a, **k: _Svc())
    return _calls


def _record_for_view(view, table):
    """按**合成集合**（根义务 + 分量）造一份自洽记录（夹具；不是核验结论）."""
    _d = cv.plan_digest_of(cv.view_candidate(view, view))
    _rec = _record(view, digest=_d)
    _want = cv.expected_obligations(view, table)
    for _row in _rec["facts"]:
        _fu = str(_row["fact_uid"])
        _rows = _want.get(_fu) or []
        _row["obligations"] = [
            {"obligation_id": _o["obligation_id"], "text": _o["text"],
             "verdict": cv.VERDICT_SUPPORTED,
             "evidence": [{"carrier_uid": "n01", "exhibitor_span": _EVIDENCE}],
             "missing": "", "reason": ""} for _o in _rows]
    _rec["obligation_basis"] = cv.obligation_basis_digest(view, table)
    return _rec




# ---------------------------------------------------------------------------
# 分量协议（修正版 P1，复核 2026-10-09-r3 §5②）：根义务不可删，分量只增检查
# ---------------------------------------------------------------------------
def _table_for(view, fact_uid="f01"):
    """一份**合法**分量表：按主张逐段拆（覆盖整条主张）."""
    _claim = next(str(f["claim"]) for f in view["frozen_facts"] if f["uid"] == fact_uid)
    return {fact_uid: [
        {"kind": "subject", "relation": "主体：周明", "span": "周明"},
        {"kind": "time", "relation": "时间：去年", "span": "去年"},
        {"kind": "action", "relation": "动作：递了辞呈", "span": "递了辞呈"},
        {"kind": "action", "relation": "后续：账目由林青接管", "span": "，账目随后由林青接管"},
    ]}




def test_the_checkpoint_text_is_the_frozen_span_not_the_model_label():
    """**检查项由冻结 `span` 编译**，`relation` 只作说明（复核 2026-10-09-r4 P1）.

    实测：`span`「三年内使杨家跻身青州十大豪门」而 `relation`「使家族跻身豪门」——
    按 `relation` 编译等于**在编译时把限定关系丢掉**，实验问的不是计划要问的内容。
    """
    from step01_script_gen import content_verify as V
    _v = _view()
    _c = {"kind": "causality", "span": _CLAIM, "relation": "完成交接"}
    assert cv.component_problems("f01", _CLAIM, [_c]) == []
    _in = V.display_task_input(_v, {"f01": [_c]})
    _row = _in["facts"][0]["obligations"][1]
    assert _row["text"] == _CLAIM, _row          # 检查项 = 冻结 span
    assert _row.get("source_span") == _CLAIM
    _txt = V.build_display_prompt(_v, {"f01": [_c]})
    assert _CLAIM in _txt, "请求里没有冻结 span"
    # 概括标签**不**替换待核内容（它只作说明）；id 与正文分列渲染
    _obj = json.dumps({"obligation_id": "c1", "text": _CLAIM}, ensure_ascii=False)
    assert _obj in _txt
    assert "[c1] %s" % _CLAIM not in _txt


@pytest.mark.parametrize("bad, want", [
    (1, "verify_decomposition_container"),
    # 空容器（[]、{}、""）自 2026-10 裁决 B 起视为"未启用分量"（合法）；
    # 非空非 dict 才报容器错误。
    ([1], "verify_decomposition_container"),
    ({"f01": 1}, "verify_decomposition_container"),
    ({"f01": []}, "verify_decomposition_gap"),
    ({"f01": [{"kind": "time", "relation": "时间", "span": "主张里没有这段"}]},
     "verify_decomposition_span_not_in_claim"),
    ({"f99": [{"kind": "time", "relation": "时间", "span": _CLAIM}]},
     "verify_decomposition_fact_unknown"),
])
def test_an_explicitly_supplied_table_must_be_complete_and_valid(bad, want):
    """**显式传表必须完整有效**；`None` 才是"没有启用分量"的合法表示（r4 P2）."""
    _v = _view()
    assert cv.decomposition_problems(_v, None) == []
    _got = [x["code"] for x in cv.decomposition_problems(_v, bad)]
    assert want in _got, (want, _got)


def test_a_component_table_must_cover_every_character_of_the_claim():
    """**文字覆盖**：漏掉的那段被点名报出——漏拆不等于"没被检查"（r3 §5② 的核心约束）."""
    _v = _view()
    _tbl = _table_for(_v)
    assert cv.component_problems("f01", _CLAIM, _tbl["f01"]) == []
    # 单变量：把「时间：去年」那条去掉 ⇒ 主张里的「去年」没有任何分量指向
    _cut = {"f01": [c for c in _tbl["f01"] if c["kind"] != "time"]}
    _got = [x["code"] for x in cv.component_problems("f01", _CLAIM, _cut["f01"])]
    assert "verify_decomposition_gap" in _got, _got
    _msg = json.dumps(cv.component_problems("f01", _CLAIM, _cut["f01"]),
                      ensure_ascii=False)
    assert "去年" in _msg, _msg


@pytest.mark.parametrize("comps, want", [
    (None, "verify_decomposition_container"),
    ([{"kind": "time", "relation": "时间：去年"}], "verify_decomposition_field_missing"),
    ([{"kind": "time", "relation": "时间：去年", "span": "去年", "x": 1}],
     "verify_decomposition_unknown_field"),
    ([{"kind": "去年", "relation": "时间", "span": "去年"}],
     "verify_decomposition_kind_invalid"),
    ([{"kind": "time", "relation": "  ", "span": "去年"}],
     "verify_decomposition_relation_blank"),
    ([{"kind": "time", "relation": "时间", "span": "  "}],
     "verify_decomposition_span_blank"),
    ([{"kind": "time", "relation": "时间", "span": "主张里没有这段"}],
     "verify_decomposition_span_not_in_claim"),
    ([{"kind": "time", "relation": "时间", "span": "去年"},
      {"kind": "time", "relation": "又说一遍", "span": "去年"}],
     "verify_decomposition_duplicate"),
    ([{"kind": "time", "relation": "时间"}],
     "verify_decomposition_field_missing"),
])
def test_component_faults_have_explicit_exits(comps, want):
    """分量表逐项违约各有出口（不筛坏成员、不"少一项当没问题"）."""
    _got = [x["code"] for x in cv.component_problems("f01", _CLAIM, comps)]
    assert want in _got, (want, _got)


def test_the_root_obligation_always_survives_the_decomposition():
    """**根义务不可删**：合成集合 = 根义务（id `claim`）+ 分量（`c1…cn`）.

    支座摘要随表变化 ⇒ 没有按表回带的记录**过不了**验收（不可能"少核一部分"）。
    """
    _v = _view()
    _root_only = cv.expected_obligations(_v)
    _tbl = _table_for(_v)
    _full = cv.expected_obligations(_v, _tbl)
    assert [o["obligation_id"] for o in _root_only["f01"]] == [cv.CLAIM_OBLIGATION_ID]
    assert [o["obligation_id"] for o in _full["f01"]] == \
        [cv.CLAIM_OBLIGATION_ID, "c1", "c2", "c3", "c4"]
    assert cv.obligation_basis_digest(_v) != cv.obligation_basis_digest(_v, _tbl)
    # 一张"缩过水"的表（少了时间分量）也算不出原表的摘要 —— 不可能悄悄换表
    _cut = {"f01": [c for c in _tbl["f01"] if c["kind"] != "time"]}
    assert cv.obligation_basis_digest(_v, _cut) != cv.obligation_basis_digest(_v, _tbl)


def test_every_component_must_be_covered_or_the_fact_is_not_executable():
    """事实 supported ⇒ **根义务与全部分量**都要 supported（少一条就不可执行）."""
    _v = _view()
    _tbl = _table_for(_v)
    _plan = cv.view_candidate(_v, _v)
    _r = _record_for_view(_v, _tbl)
    assert cv.verification_problems(_r, _v, plan=_plan, decomposition=_tbl) == [], \
        _codes(cv.verification_problems(_r, _v, plan=_plan, decomposition=_tbl))
    # 单变量：把时间分量改成 partial ⇒ 整条事实不再是 supported（且被点名）
    _w = cv.expected_obligations(_v, _tbl)["f01"][2]
    _row = next(x for x in _r["facts"] if x["fact_uid"] == "f01")
    _row["obligations"][2] = {"obligation_id": _w["obligation_id"], "text": _w["text"],
                              "verdict": cv.VERDICT_PARTIAL, "evidence": [],
                              "missing": "实际展示正文没有时间跨度", "reason": ""}
    _row["verdict"] = cv.VERDICT_PARTIAL
    _got = _codes(cv.verification_problems(_r, _v, plan=_plan, decomposition=_tbl))
    assert "verify_fact_unsupported" in _got, _got
    # 根义务没被回带（只剩分量）⇒ 集合层直接拒收
    _r2 = _record_for_view(_v, _tbl)
    _row2 = next(x for x in _r2["facts"] if x["fact_uid"] == "f01")
    _row2["obligations"] = _row2["obligations"][1:]
    _got2 = _codes(cv.verification_problems(_r2, _v, plan=_plan, decomposition=_tbl))
    assert "verify_obligation_missing" in _got2, _got2


def test_the_display_prompt_marks_the_uid_unambiguously():
    """**uid 必须无歧义**：实测（分量实验 4/6 次）模型会把 `[visual b09]` 整段当成 uid.

    所以请求里把 uid 单独标出来（`uid=` 后那串），并明说不要带类别。
    """
    from step01_script_gen import content_verify as V
    _v = _display_view()
    _txt = V.build_display_prompt(_v)
    assert "载体 uid=n01" in _txt, _txt[-600:]
    assert "[visual b01]" not in _txt and "[screen_card" not in _txt, \
        "载体行不该再出现 [类别 uid] 这种容易被整段抄走的写法"
    assert "不要带类别" in _txt

def test_a_well_formed_display_response_passes_the_producer_exit(monkeypatch):
    """固定响应 → **生产出口**（真建请求 → 真解析 → **由代码盖章**）→ 展示轴零问题.

    ⚠️ 不手工补 `obligation_basis` / `display_digest`：那正是复核 2026-10-09-r3 P1
    指出的「测试盖章把生产出口的缺口盖住了」。身份只能来自生产出口。
    """
    from step01_script_gen import content_verify as V
    _v = _display_view()
    _calls = _stub_display_service(monkeypatch, _display_payload(_v))
    _out = V.verify_display_with_service(_v)
    assert len(_calls) == 1
    assert _out["problems"] == [], _out["problems"]
    assert _out["record"]["obligation_basis"] == cv.obligation_basis_digest(_v)
    assert _out["record"]["display_digest"] == cv.display_input_digest(_v)
    assert _out["record"]["rule_version"] == cv.DEFAULT_RULE_VERSION
    assert cv.display_problems(_out["record"], _v) == [], \
        _codes(cv.display_problems(_out["record"], _v))
    # **反例（单变量）**：证据片段不在它点名的载体正文里 ⇒ 具体内容问题被拒，
    # 且**不出现**身份类错误（身份是代码盖的，与内容对错分账）
    _bad = _display_payload(_v)
    _bad["facts"][0]["obligations"][0]["evidence"][0]["exhibitor_span"] = "这段正文根本不在载体里"
    _calls2 = _stub_display_service(monkeypatch, _bad)
    _out2 = V.verify_display_with_service(_v)
    assert len(_calls2) == 1
    _got = _codes(cv.display_problems(_out2["record"], _v))
    assert "verify_obligation_span_not_exhibited" in _got, _got
    assert not [c for c in _got if "basis" in c or "digest" in c], _got


def test_a_record_without_code_stamped_identity_is_rejected():
    """**解析结果本身过不了展示轴**（fail-closed）：身份必须由生产出口给，不是测试补的."""
    from step01_script_gen import content_verify as V
    _v = _display_view()
    _rec, _bad = V.parse_display_response(
        json.dumps(_display_payload(_v), ensure_ascii=False))
    assert _bad == [] and _rec is not None, _bad
    _got = _codes(cv.display_problems(_rec, _v))
    assert "verify_obligation_basis_mismatch" in _got, _got
    assert "verify_display_digest_mismatch" in _got, _got


def test_the_prompt_carries_the_frozen_obligations_and_a_matching_example():
    """提示必须**逐条给出**待核义务，且 id 与正文**分列**（单源渲染）.

    2026-10-25 P4：旧写法 `1. [claim] <text>` 让模型把 id 回成 `"[claim] <text>"`
    或 `"1"`，三份记录全部因义务身份被拒。现在渲染成两键对象，模型可原样照抄；
    样例的 id 仍与给定的那一个一致（不手抄第二份）。
    """
    from step01_script_gen.content_verify import build_verification_prompt
    _v = _view()
    _txt = build_verification_prompt(_v, novel_text=_NOVEL, novel_digest=_NOVEL_DIGEST)
    _w = _want_row(_v, 'f01')
    _obj = json.dumps({"obligation_id": _w["obligation_id"],
                       "text": _w["text"]}, ensure_ascii=False)
    assert _obj in _txt, '待核义务必须以明确字段进提示'
    assert ('[%s] %s' % (_w['obligation_id'], _w['text'])) not in _txt, \
        '不得再渲染成 `[id] 文本`（id 与正文边界歧义）'
    assert ('"obligation_id": "%s"' % _w['obligation_id']) in _txt, \
        '样例的 id 必须与给定的那一个一致（否则模型照抄样例）'


def test_display_prompt_renders_obligations_as_explicit_fields():
    """展示核验入口同样把义务渲染成两键对象（两个入口共用一处渲染）."""
    from step01_script_gen import content_verify as V
    _v = _display_view()
    _txt = V.build_display_prompt(_v)
    _o = V.display_task_input(_v)["facts"][0]["obligations"][0]
    _obj = json.dumps({"obligation_id": _o["obligation_id"],
                       "text": _o["text"]}, ensure_ascii=False)
    assert _obj in _txt, '展示核验提示必须给出明确字段'
    assert ("[%s] %s" % (_o["obligation_id"], _o["text"])) not in _txt

def test_verification_prompt_marks_purpose_as_not_evidence():
    """"「声明」栏明确标注**不能当证据**——核验模型据此不会拿 purpose 冒充."""
    from step01_script_gen.content_verify import build_verification_prompt
    _v = _view(carriers=[{"kind": pc.KIND_VISUAL, "uid": "b01"}])
    _v["narration_units"] = []
    _v["beats"][0]["voice_ref"] = None
    _v["beats"][0]["purpose"] = "交代林青接管"
    _txt = build_verification_prompt(_v)
    assert "不能**当证据" in _txt or "不能当证据" in _txt.replace("**", "")


def test_verification_response_parser_rejects_non_conforming_payloads():
    """解析返回 `(记录, 问题)`；**坏响应带问题记录整份拒收**（不筛成员、不字符串化）."""
    from step01_script_gen.content_verify import parse_verification_response

    def _parse(payload):
        return parse_verification_response(
            payload if isinstance(payload, str) else json.dumps(payload))

    _rec, _probs = _parse("不是 JSON")
    assert _rec is None and _probs, (_rec, _probs)
    _rec, _probs = _parse({"facts": "x"})
    assert _rec is None and _codes(_probs), (_rec, _probs)
    # **三条轴缺一不可**（缺 `source_support` 即坏响应）——按**轴**给身份
    _rec, _probs = _parse({"facts": [], "voice_units": []})
    assert _rec is None and "verify_source_support_missing" in _codes(_probs), _probs
    # 字段类型违约：`evidence_spans=1` 是违约（不是"一个片段"），带路径与原值
    _rec, _probs = _parse({"source_support": [{"fact_uid": "f01",
                                               "verdict": "supported",
                                               "evidence_spans": 1, "reason": ""}],
                           "facts": [], "voice_units": []})
    assert _rec is None, _rec
    _hit = next(x for x in _probs if x["code"] == "verify_record_field_type")
    assert _hit["path"] == "source_support[0].evidence_spans" and _hit["value"] == 1, _hit
    # 结果组里夹坏成员 ⇒ **不筛掉**，整份拒收
    _rec, _probs = _parse({"source_support": [1], "facts": [], "voice_units": []})
    assert _rec is None and "verify_record_item_not_object" in _codes(_probs), _probs
    _rec, _probs = _parse({"source_support": [], "facts": [], "voice_units": []})
    assert _rec is not None and _probs == [] and _rec["status"] == "ok"
    assert _rec["schema"] == cv.SCHEMA_VERIFICATION
    assert set(_rec) >= {"source_support", "facts", "voice_units"}

# ---------------------------------------------------------------------------
# ⑥ 记录契约（复核 2026-10-07 三轮 ①）：解析 / 控制器 / 缓存读回 / 统一验收**共用**
#
# 旧形态：`evidence_spans=1` / `support_spans=1` / `suggested_carriers=1` 先记
# `status=ok`，随后在**统一验收**里抛 `TypeError`（验收器被坏输入打崩，不是拒收）；
# 结果组里夹的坏成员被 `isinstance(x, dict)` 过滤掉（"少了一项"冒充"没问题"）；
# `speaker=123` 一路通过。修法不是再补几个字段检查，而是一份**记录契约**——
# 四个入口调**同一个函数**。
# ---------------------------------------------------------------------------
def _shape_codes(_r):
    return [x["code"] for x in cv.record_shape_problems(_r)]


def test_a_legal_record_passes_every_entry():
    """**合法对照**：一份合法记录在四个入口都通过（判据同源，不各说各话）."""
    from step01_script_gen.content_verify import parse_verification_response
    _v = _view()
    _plan = cv.view_candidate(_v, _v)
    _r = _record(_v)
    assert cv.record_shape_problems(_r) == []
    assert cv.verification_problems(_r, _v, plan=_plan, novel_text=_NOVEL,
                                    novel_digest=_NOVEL_DIGEST) == []
    # ① 解析入口（生产者）：三组齐全 ⇒ `(记录, [])`
    _parsed, _p1 = parse_verification_response(json.dumps(
        {"source_support": [], "facts": [], "voice_units": []}))
    assert _parsed is not None and _p1 == [], (_parsed, _p1)
    # ② 控制器装载（缓存复用）：摘要一致 + 形状合格 + 状态 ok + 规则版本 ⇒ True
    _ctl = cv.VerificationController(verify=None, novel_digest=_NOVEL_DIGEST)
    assert _ctl.seed(cv.plan_digest_of(_plan), _r, plan=_plan) is True
    assert _ctl.seed("0" * 64, _r) is False          # 别份候选的记录不许装
    _bad = _record(_v)
    _bad["facts"][0]["support_spans"] = 1
    assert _ctl.seed(cv.plan_digest_of(_plan), _bad, plan=_plan) is False     # 形状不合格不许装


def test_a_bad_group_container_is_refused_per_axis():
    """整组缺失/不是数组 ⇒ **按轴**给身份（"哪条轴没有结论"），不是一句笼统的形状错.

    并且**统一验收入口不抛异常**：旧形态是 `TypeError`。
    """
    _v = _view()
    _plan = cv.view_candidate(_v, _v)
    for _g, _code in (("source_support", "verify_source_support_missing"),
                      ("facts", "verify_fact_missing"),
                      ("voice_units", "verify_voice_units_mismatch")):
        _r = _record(_v)
        _r[_g] = 1                                   # 不是数组
        assert _code in _shape_codes(_r), (_g, _shape_codes(_r))
        _got = _codes(cv.verification_problems(_r, _v, plan=_plan))
        assert _code in _got, (_g, _got)
        _r2 = _record(_v)
        del _r2[_g]                                  # 整组缺席（缺席不是通过）
        assert _code in _shape_codes(_r2), (_g, _shape_codes(_r2))


@pytest.mark.parametrize("group, field, value", [
    ("source_support", "evidence_spans", 1),
    ("source_support", "reason", 5),
    ("facts", "support_spans", 1),
    ("facts", "suggested_carriers", 1),
    ("facts", "missing", ["缺"]),
    ("voice_units", "speaker", 123),
    ("voice_units", "echo_text", ["x"]),
    ("voice_units", "source_kind", 7),
])
def test_a_bad_field_is_refused_with_path_and_value(group, field, value):
    """字段类型违约：带**字段路径 + 原值**；不字符串化、不按单项兜底、不崩在验收里."""
    _v = _view()
    _r = _record(_v)
    _r[group][0][field] = value
    _hit = next(x for x in cv.record_shape_problems(_r)
                if x["code"] == "verify_record_field_type"
                and x["path"] == "%s[0].%s" % (group, field))
    assert _hit["value"] == value, _hit
    _got = _codes(cv.verification_problems(_r, _v, plan=cv.view_candidate(_v, _v)))
    assert "verify_record_field_type" in _got, _got


def test_a_bad_member_is_not_filtered_out():
    """"结果组里夹了一个坏成员" ≠ "少了一项"（过滤会让缺项检查看错表）."""
    _v = _view()
    _r = _record(_v)
    _r["facts"] = [1] + list(_r["facts"])
    _probs = cv.record_shape_problems(_r)
    assert any(x["code"] == "verify_record_item_not_object" and x["path"] == "facts[0]"
               for x in _probs), _probs
    _got = _codes(cv.verification_problems(_r, _v, plan=cv.view_candidate(_v, _v)))
    assert "verify_record_item_not_object" in _got, _got
    assert "verify_fact_missing" not in _got, _got


def test_the_record_must_bind_this_run_novel_identity():
    """记录必须绑**本次**原文身份：空摘要 / 别份摘要都不算通过（缺席不是通过）."""
    _v = _view()
    _plan = cv.view_candidate(_v, _v)
    _r = _record(_v)
    assert cv.verification_problems(_r, _v, plan=_plan, novel_text=_NOVEL,
                                    novel_digest=_NOVEL_DIGEST) == []
    for _bad in ("", "0" * 64):
        _r2 = _record(_v)
        _r2["novel_digest"] = _bad
        _got = _codes(cv.verification_problems(_r2, _v, plan=_plan, novel_text=_NOVEL,
                                               novel_digest=_NOVEL_DIGEST))
        assert "verify_novel_digest_mismatch" in _got, (_bad, _got)


def test_the_controller_stamps_the_novel_identity_of_this_run():
    """原文身份由**代码盖章**：核验方自报的值一律覆盖（自报不作数）."""
    _v = _view()
    _plan = cv.view_candidate(_v, _v)
    _rec = _record(_v)
    _rec["novel_digest"] = "自报的摘要"
    _ctl = cv.VerificationController(verify=lambda view, dig: _rec, deadline_at=None,
                                     novel_digest=_NOVEL_DIGEST)
    _got = _ctl.record_for(_v, cv.plan_digest_of(_plan))
    assert _got["novel_digest"] == _NOVEL_DIGEST, _got["novel_digest"]
    # 失败记录不带内容结论（也不该被原文身份判据牵连）
    _ctl2 = cv.VerificationController(verify=lambda view, dig: None, deadline_at=None,
                                      novel_digest=_NOVEL_DIGEST)
    _failed = _ctl2.record_for(_v, cv.plan_digest_of(_plan))
    assert _failed["status"] != "ok"
    assert "verify_novel_digest_mismatch" not in _codes(
        cv.verification_problems(_failed, _v, plan=_plan, novel_text=_NOVEL,
                                 novel_digest=_NOVEL_DIGEST))

def test_the_unified_acceptance_checks_this_run_novel_identity():
    """**统一验收入口**必须拿本次原文身份核——不能只靠缓存或控制器那一侧.

    判别性：把 `plan_acceptance_problems → verification_problems` 的 `novel_digest`
    实参去掉，这条就转红（记录绑着别份原文也能过关）。用的是**结构完整**的混合计划
    （`_view()` 那份只够读关系，过不了结构门）。
    """
    from tests.shared.test_cache_and_card_handoff import _NOVEL as _N2
    from tests.shared.test_cache_and_card_handoff import _hybrid_plan, _record_for
    from step01_script_gen import outline as _o
    _plan = _hybrid_plan()
    _plan["plan_verification"] = _record_for(_plan, novel_digest="0" * 64)
    _got = [x["code"] for x in _o.plan_acceptance_problems(_plan, _N2)]
    assert "verify_novel_digest_mismatch" in _got, _got
    # **合法对照**：绑对了就没有这一条
    _plan["plan_verification"] = _record_for(
        _plan, novel_digest=hashlib.sha256(_N2.encode("utf-8")).hexdigest())
    _got2 = [x["code"] for x in _o.plan_acceptance_problems(_plan, _N2)]
    assert "verify_novel_digest_mismatch" not in _got2, _got2


def test_seed_refuses_a_record_without_this_run_novel_identity():
    """装载必须核**本次原文身份**（复核 2026-10-07 四轮 P2）.

    实测形态：正确 / 错误 / 空原文摘要**都返回 `True`**，`record_for()` 随后**零调用**
    复用那份错误记录。这里同时给**合法对照**：绑对了 ⇒ 装载成功且零调用复用；被拒 ⇒
    下一次读取**真的重新核验**（不是把错误记录接着用）。
    """
    _v = _view()
    _plan = cv.view_candidate(_v, _v)
    _dig = cv.plan_digest_of(_plan)
    _calls = []

    def _svc(view, dig):
        _calls.append(dig)
        return _record(_v)

    _ctl = cv.VerificationController(verify=_svc, novel_digest=_NOVEL_DIGEST)
    assert _ctl.seed(_dig, _record(_v), plan=_plan) is True
    _got = _ctl.record_for(_v, _dig)
    assert _calls == [], "合法记录应当零调用复用：%s" % _calls
    assert _got["novel_digest"] == _NOVEL_DIGEST
    for _bad in ("0" * 64, "别的原文的摘要", ""):
        _r = _record(_v)
        _r["novel_digest"] = _bad
        _c2 = cv.VerificationController(verify=_svc, novel_digest=_NOVEL_DIGEST)
        assert _c2.seed(_dig, _r) is False, _bad
        _calls.clear()
        _got2 = _c2.record_for(_v, _dig)
        assert _calls == [_dig], "被拒之后没有重新核验：%s" % _calls
        assert _got2["novel_digest"] == _NOVEL_DIGEST, _got2
    _r3 = _record(_v)
    del _r3["novel_digest"]                     # 字段整个缺失也算"绑不上"
    assert cv.VerificationController(
        verify=_svc, novel_digest=_NOVEL_DIGEST).seed(_dig, _r3) is False
    # 控制器**自己**没拿到本次原文身份 ⇒ 谁都不装（证明不了就不许当已核验用）
    assert cv.VerificationController(verify=_svc).seed(_dig, _record(_v)) is False


@pytest.mark.parametrize("blank", ["", "   ", chr(0x3000), " " + chr(0x3000) + " "])
def test_a_blank_speaker_is_not_a_speaker(blank):
    """说话人要有**非空白**内容（复核 2026-10-07 四轮 P2）.

    实测形态：`speaker=""` 被拒，但 `"   "` 与全角空格得到 `status=ok` 且统一验收零问题。
    这一层只要求"有内容"，**不替它猜人物**——判断"是不是真名"不是确定性判据的职责。
    """
    _v = _view()
    _plan = cv.view_candidate(_v, _v)

    def _mk(_s):
        return _record(_v, voices=[
            {"unit_uid": "n01", "verdict": cv.VERDICT_SUPPORTED,
             "source_kind": "novel_speech", "speaker": _s,
             "echo_text": _EVIDENCE, "reason": ""}])

    assert "verify_voice_speaker_missing" not in _codes(                 # 合法对照
        cv.verification_problems(_mk("周明"), _v, plan=_plan))
    _got = _codes(cv.verification_problems(_mk(blank), _v, plan=_plan))
    assert "verify_voice_speaker_missing" in _got, (repr(blank), _got)


@pytest.mark.parametrize("blank", ["", "   ", chr(0x3000)])
def test_a_blank_speaker_makes_the_run_not_executable(blank):
    """空白说话人必须让**统一验收**报出问题 ⇒ 本次不可执行.

    `executable = 验收为空 ∧ 未过截止`（`generate_outline_result`），所以"验收报出这条"
    就是"不可执行"的判据。**单变量对照**：同一份**结构完整**的计划，只换 `speaker` 的
    写法——合法姓名下没有这一条，空白下必须有（夹具自身的其他既存项两边都有，不参与判别）。
    """
    from tests.shared.test_cache_and_card_handoff import _NOVEL as _N2
    from tests.shared.test_cache_and_card_handoff import _hybrid_plan, _record_for
    from step01_script_gen import outline as _o
    _nv = hashlib.sha256(_N2.encode("utf-8")).hexdigest()

    def _codes_with(_speaker):
        _p = _hybrid_plan()
        _p["dialogue_lines"] = ["周明去年递了辞呈。"]      # 声音轴要有单位，才谈得上说话人
        _p["presentation"]["dialogue_uids"] = ["d01"]
        _rec = _record_for(_p, novel_digest=_nv)
        for _u in _rec["voice_units"]:
            _u["speaker"] = _speaker
        _p["plan_verification"] = _rec
        return [x["code"] for x in _o.plan_acceptance_problems(_p, _N2)]

    _ok = _codes_with("周明")
    assert "verify_voice_speaker_missing" not in _ok, _ok
    _bad = _codes_with(blank)
    assert "verify_voice_speaker_missing" in _bad, (repr(blank), _bad)


# ---------------------------------------------------------------------------
# 义务轴（v3）：待核集合**由冻结主张派生**，核验方不能增删改写；事实裁决由逐条派生
# ---------------------------------------------------------------------------
def _with_obligations(rec, fact_uid, obligations):
    for _r in rec["facts"]:
        if _r["fact_uid"] == fact_uid:
            _r["obligations"] = obligations
    return rec


def _obl(oid, text, verdict, carrier="", span="", reason="", missing=None,
         evidence=None):
    """v4 义务行：证据是**数组**（给了载体或片段就装一项）.

    非 `supported` 时 `missing` 必须非空——默认给一个占位（要测"空白被拒"的用例
    显式传 `missing="   "`）。
    """
    _ev = (list(evidence) if evidence is not None else
           ([{"carrier_uid": str(carrier), "exhibitor_span": str(span)}]
            if (str(carrier) or str(span)) else []))
    _m = (missing if missing is not None
          else ("" if str(verdict) == cv.VERDICT_SUPPORTED else "（夹具：未写明缺什么）"))
    return {"obligation_id": oid, "text": text, "verdict": verdict,
            "evidence": _ev, "missing": _m, "reason": reason}


def _want_row(view, fact_uid):
    """**代码给定的那一条**义务（合法形状的基准；id 与 text 都不许改）."""
    return cv.frozen_obligations(view)[str(fact_uid)][0]


def test_a_self_consistent_obligation_axis_has_no_problems():
    """**合法对照**：每条义务点名真实载体 + 逐字片段 ⇒ 义务轴上没有问题."""
    _v = _view()
    _r = _record(_v)
    assert cv.obligation_axis_problems(_r, _v) == [],         [x["code"] for x in cv.obligation_axis_problems(_r, _v)]
    assert cv.verification_problems(_r, _v, plan=cv.view_candidate(_v, _v)) == []


def test_the_obligation_basis_does_not_depend_on_what_the_candidate_shows():
    """**P1（2026-10-09）**：待核集合只看冻结主张，与候选**展示了什么**无关.

    把载体全清掉（没有任何展示正文）⇒ 集合与支座摘要**一个字都不变**：
    "候选少演了什么"不许反过来决定"该核哪几部分"（那正是本缺陷的形态）。
    """
    _v = _view()
    _v2 = _view(carriers=[])
    assert cv.frozen_obligations(_v) == cv.frozen_obligations(_v2)
    assert cv.obligation_basis_digest(_v) == cv.obligation_basis_digest(_v2)
    assert cv.frozen_obligations(_v)["f01"][0]["obligation_id"] == cv.CLAIM_OBLIGATION_ID
    assert cv.frozen_obligations(_v)["f01"][0]["text"] == _CLAIM


def test_dropping_the_unmet_obligation_cannot_certify_the_fact():
    """**P1 的真实形态**：删掉没被满足的义务、换成一条"只写结果"的 ⇒ 拒收.

    §84.2 的误收：主张要求「四年前接手家业 + 三年内跻身十大豪门」，实际只演了结果。
    旧口径下"删掉没满足的那条、只剩结果那条"照样通过——完整主张被结果子集替代。
    """
    _v = _view()
    _plan = cv.view_candidate(_v, _v)
    _r = _record(_v)
    _with_obligations(_r, "f01", [
        _obl("result", "杨家跻身青州十大豪门", cv.VERDICT_SUPPORTED, "n01", _EVIDENCE)])
    _got = _codes(cv.verification_problems(_r, _v, plan=_plan))
    assert "verify_obligation_unknown_id" in _got, _got      # 自造的那条义务
    assert "verify_obligation_missing" in _got, _got         # 少掉的那条义务
    # **合法对照（单变量）**：同一条事实、同一条义务，诚实判 `partial` ⇒ 集合层不再有问题
    _r2 = _record(_v)
    _w = _want_row(_v, "f01")
    _with_obligations(_r2, "f01", [
        _obl(_w["obligation_id"], _w["text"], cv.VERDICT_PARTIAL, "", "",
             "只演了结果，时间跨度没被展示")])
    _r2["facts"][0]["verdict"] = cv.VERDICT_PARTIAL
    _got2 = _codes(cv.verification_problems(_r2, _v, plan=_plan))
    assert not [c for c in _got2 if c.startswith("verify_obligation_")], _got2




def test_a_claim_can_be_evidenced_jointly_by_two_carriers():
    """**联合承载**（v4）：一条义务由两个载体各展示一部分 ⇒ 协议能无损记录、可过验收.

    这是**表达性**用例（复核 2026-10-09-r2「联合承载记录的表达缺口」）：对白表达「谎称」、
    画面表达「去向」这种关系，旧契约只能点名一个载体，记不全；现在 `evidence[]` 每项各绑
    一个载体 + 该载体的逐字片段。语义结论仍由核验方对**并集**给出，本用例不替它表态。
    """
    _v = _view(carriers=[{"kind": pc.KIND_VISUAL, "uid": "b01"},
                         {"kind": pc.KIND_NARRATION, "uid": "n01"}])
    _plan = cv.view_candidate(_v, _v)
    _w = _want_row(_v, "f01")
    _r = _record(_v)
    _with_obligations(_r, "f01", [
        _obl(_w["obligation_id"], _w["text"], cv.VERDICT_SUPPORTED, missing="",
             evidence=[{"carrier_uid": "b01", "exhibitor_span": "周明放下辞呈"},
                       {"carrier_uid": "n01", "exhibitor_span": _EVIDENCE}])])
    _got = _codes(cv.verification_problems(_r, _v, plan=_plan))
    assert _got == [], _got


def test_joint_evidence_faults_have_explicit_exits():
    """联合证据的**每一项**都逐项判：坏身份 / 虚构片段 / 空载体 / 空证据 / 空白 missing.

    逐项单独触发、断言各自的 code（不看"问题列表非空"）。
    """
    _v = _view(carriers=[{"kind": pc.KIND_VISUAL, "uid": "b01"},
                         {"kind": pc.KIND_NARRATION, "uid": "n01"}])
    _plan = cv.view_candidate(_v, _v)
    _w = _want_row(_v, "f01")
    _i, _t = _w["obligation_id"], _w["text"]
    _good = {"carrier_uid": "n01", "exhibitor_span": _EVIDENCE}
    _cases = [
        # 自报 supported 却一条证据都不给
        ("verify_obligation_evidence_missing",
         _obl(_i, _t, cv.VERDICT_SUPPORTED, missing="", evidence=[])),
        # 第二项跨事实 / 悬空身份
        ("verify_obligation_carrier_unknown",
         _obl(_i, _t, cv.VERDICT_SUPPORTED, missing="",
              evidence=[dict(_good), {"carrier_uid": "cZZ", "exhibitor_span": _EVIDENCE}])),
        # 第二项片段是编的（不在该载体实际正文里）
        ("verify_obligation_span_not_exhibited",
         _obl(_i, _t, cv.VERDICT_SUPPORTED, missing="",
              evidence=[dict(_good), {"carrier_uid": "b01",
                                      "exhibitor_span": "这段正文根本不在载体里"}])),
        # 证据项没点名载体 / 没回带片段
        ("verify_obligation_carrier_missing",
         _obl(_i, _t, cv.VERDICT_SUPPORTED, missing="",
              evidence=[{"carrier_uid": "", "exhibitor_span": _EVIDENCE}])),
        ("verify_obligation_span_missing",
         _obl(_i, _t, cv.VERDICT_SUPPORTED, missing="",
              evidence=[{"carrier_uid": "n01", "exhibitor_span": "   "}])),
        # 非 supported 却不写缺什么
        ("verify_obligation_missing_blank",
         _obl(_i, _t, cv.VERDICT_PARTIAL, missing="   ")),
    ]
    for _want, _row in _cases:
        _r = _record(_v)
        _with_obligations(_r, "f01", [_row])
        _got = _codes(cv.verification_problems(_r, _v, plan=_plan))
        assert _want in _got, (_want, _got)




def test_the_verification_context_is_local_checked_and_gates_reuse():
    """核验上下文：**本地生成、模型不可改**；它决定支座，也决定旧记录能不能复用.

    - `mode=root`（没有表）合法；`mode=components` 必须带**已准入**的表；
    - 表变了 ⇒ 支座摘要变 ⇒ 旧记录不复用；
    - 上下文摘要不符 / 事实基座不符 / 规则不符 ⇒ 拒（自报不作证明）。
    """
    _v = _view()
    _plan = dict(cv.view_candidate(_v, _v))
    _tbl = {_v["frozen_facts"][0]["uid"]: [
        {"kind": "other", "relation": "整条", "span": _CLAIM}]}
    # 两轴策略身份（`models`）是**上下文的必填项**：复核 r7 P1-B 要求逐轴登记非空模型名，
    # 构建函数与读取侧共用同一份准入（不再允许缺省/空白/非对象）。
    _models = {"source_voice": "stub-source-voice", "display": "stub-display"}
    _root_ctx = cv.build_verification_context(_v, models=_models)
    _comp_ctx = cv.build_verification_context(_v, decomposition=_tbl, models=_models)
    assert cv.context_problems(_v, _root_ctx) == []
    assert cv.context_problems(_v, _comp_ctx) == []
    assert _root_ctx["mode"] == cv.MODE_ROOT and _comp_ctx["mode"] == cv.MODE_COMPONENTS
    # 记录按**根策略**盖章：在带分量上下文的计划上**不复用**
    _r = _record(_v)
    _plan[cv.VERIFICATION_CONTEXT_KEY] = _root_ctx
    assert cv.record_basis_matches(_r, _plan) is True
    _plan[cv.VERIFICATION_CONTEXT_KEY] = _comp_ctx
    assert cv.record_basis_matches(_r, _plan) is False, "分量策略下不许按根义务复用"
    # 单变量对照：记录按**同一张表**盖章 ⇒ 复用
    _fu = _v["frozen_facts"][0]["uid"]
    _row = next(x for x in _r["facts"] if x["fact_uid"] == _fu)
    _row["obligations"] = [dict(_row["obligations"][0],
                                obligation_id="c1", text=_CLAIM)] + _row["obligations"]
    _r["obligation_basis"] = cv.obligation_basis_digest(_v, _tbl)
    assert cv.record_basis_matches(_r, _plan) is True
    # 坏上下文：摘要被改、事实基座不符、mode 与表不符 —— 一律拒
    for _mut in ("digest", "fact_basis", "mode"):
        _bad = dict(_comp_ctx)
        if _mut == "digest":
            _bad["context_digest"] = "0" * 64
        elif _mut == "fact_basis":
            _bad["fact_basis_digest"] = "0" * 64
        else:
            _bad["mode"] = cv.MODE_ROOT
        assert cv.context_problems(_v, _bad), _mut
        _plan[cv.VERIFICATION_CONTEXT_KEY] = _bad
        assert cv.record_basis_matches(_r, _plan) is False, _mut


def test_seed_with_a_plan_uses_the_shared_basis_check():
    """`seed(..., plan=)`：装载入口按**共用判据**核对义务支座（给了计划就走强判据）.
    """
    _v = _view()
    _plan = cv.view_candidate(_v, _v)
    _dig = cv.plan_digest_of(_plan)
    _good = _record(_v)
    _calls: list = []
    _ctl = cv.VerificationController(verify=lambda *a: _calls.append(a),
                                     novel_digest=_NOVEL_DIGEST)
    assert _ctl.seed(_dig, _good, plan=_plan) is True
    assert _ctl.record_for(_v, _dig) == _good and _calls == []
    _bad = _record(_v)
    _bad["obligation_basis"] = "0" * 64
    _ctl2 = cv.VerificationController(verify=lambda *a: _calls.append(a),
                                      novel_digest=_NOVEL_DIGEST)
    assert _ctl2.seed(_dig, _bad, plan=_plan) is False
    assert _dig not in _ctl2.records, "被拒的记录不许进 memo"

def test_a_supported_fact_must_cover_every_obligation():
    """**核心判据**：整项 supported、却有一条义务没被覆盖 ⇒ 拒收（§84.2 的真实误收形态）.

    "有一段被展示的正文"不等于"主张的每一部分都被展示"。
    """
    _v = _view()
    _plan = cv.view_candidate(_v, _v)
    _w = _want_row(_v, "f01")
    _r = _record(_v)
    _with_obligations(_r, "f01", [
        _obl(_w["obligation_id"], _w["text"], cv.VERDICT_PARTIAL, "", "",
             "实际展示正文没有时间跨度")])
    _got = _codes(cv.verification_problems(_r, _v, plan=_plan))
    assert "verify_obligation_uncovered" in _got, _got
    # **单变量对照**：那条义务也判 supported（并给出逐字片段）⇒ 这一条消失
    _with_obligations(_r, "f01", [
        _obl(_w["obligation_id"], _w["text"], cv.VERDICT_SUPPORTED, "n01", _EVIDENCE)])
    assert "verify_obligation_uncovered" not in _codes(
        cv.verification_problems(_r, _v, plan=_plan))


def test_the_fact_verdict_is_derived_from_the_row_verdicts():
    """事实裁决**由逐条结果派生**：全 supported 才能填 supported（`unknown` 也不行）."""
    _v = _view()
    _plan = cv.view_candidate(_v, _v)
    _w = _want_row(_v, "f01")
    _r = _record(_v)
    _with_obligations(_r, "f01", [
        _obl(_w["obligation_id"], _w["text"], cv.VERDICT_UNKNOWN, "", "",
             "给定信息不足以判断")])
    _r["facts"][0]["verdict"] = cv.VERDICT_UNKNOWN
    # 派生值 = unknown，记录也是 unknown ⇒ 自洽；但 unknown 本身不能成为可执行结论
    _got = _codes(cv.verification_problems(_r, _v, plan=_plan))
    assert "verify_fact_verdict_inconsistent" not in _got, _got
    assert "verify_fact_unknown" in _got, _got
    # **单变量**：逐条全是 supported，事实却填 partial ⇒ 派生不一致
    _r2 = _record(_v)
    _r2["facts"][0]["verdict"] = cv.VERDICT_PARTIAL
    assert "verify_fact_verdict_inconsistent" in _codes(
        cv.verification_problems(_r2, _v, plan=_plan))


def test_every_obligation_fault_has_an_explicit_rejection_path():
    """集合层与逐项层**各有出口**：少 / 多 / 重复 / 改写 / 坏身份 / 空白 / 坏片段.

    逐项**单独**触发、断言各自的 code（不看"问题列表非空"）。
    """
    _v = _view()
    _plan = cv.view_candidate(_v, _v)
    _w = _want_row(_v, "f01")
    _id, _tx = _w["obligation_id"], _w["text"]
    _good = _obl(_id, _tx, cv.VERDICT_SUPPORTED, "n01", _EVIDENCE)
    _cases = [
        ("verify_obligation_missing", []),
        ("verify_obligation_unknown_id",
         [_obl("o9", _tx, cv.VERDICT_SUPPORTED, "n01", _EVIDENCE)]),
        ("verify_obligation_duplicate", [_good, dict(_good)]),
        ("verify_obligation_text_substituted",
         [_obl(_id, "杨家跻身青州十大豪门", cv.VERDICT_SUPPORTED, "n01", _EVIDENCE)]),
        ("verify_obligation_unknown_field", [dict(_good, extra="x")]),
        ("verify_obligation_carrier_unknown",
         [_obl(_id, _tx, cv.VERDICT_SUPPORTED, "b99", _EVIDENCE)]),
        ("verify_obligation_text_blank", [_obl(_id, "   ", cv.VERDICT_SUPPORTED,
                                               "n01", _EVIDENCE)]),
        ("verify_obligation_verdict_invalid", [_obl(_id, _tx, "大概吧", "", "")]),
        ("verify_obligation_span_missing", [_obl(_id, _tx, cv.VERDICT_SUPPORTED,
                                                 "n01", "   ")]),
        ("verify_obligation_carrier_missing", [_obl(_id, _tx, cv.VERDICT_SUPPORTED,
                                                    "", _EVIDENCE)]),
        ("verify_obligation_span_not_exhibited",
         [_obl(_id, _tx, cv.VERDICT_SUPPORTED, "n01", "这段正文根本不在载体里")]),
    ]
    for _want, _obls in _cases:
        _r = _record(_v)
        _with_obligations(_r, "f01", _obls)
        _got = _codes(cv.verification_problems(_r, _v, plan=_plan))
        assert _want in _got, (_want, _got)


def test_a_bad_carrier_uid_is_not_taken_from_another_entity():
    """坏身份用**别的实体**的 uid 冒充展示出处 ⇒ 拒收（不跨事实找同名实体）."""
    _v = _view(carriers=[{"kind": pc.KIND_VISUAL, "uid": "b01"},
                         {"kind": pc.KIND_NARRATION, "uid": "n01"}])
    _plan = cv.view_candidate(_v, _v)
    _w = _want_row(_v, "f01")
    _r = _record(_v)
    _with_obligations(_r, "f01", [_obl(_w["obligation_id"], _w["text"],
                                       cv.VERDICT_SUPPORTED, "b01", _EVIDENCE)])
    _got = _codes(cv.verification_problems(_r, _v, plan=_plan))
    # b01 是**这条事实的载体之一**（画面），但它的正文不含这段片段 ⇒ 坏片段，不是坏身份
    assert "verify_obligation_span_not_exhibited" in _got, _got
    _with_obligations(_r, "f01", [_obl(_w["obligation_id"], _w["text"],
                                       cv.VERDICT_SUPPORTED, "cZZ", _EVIDENCE)])
    assert "verify_obligation_carrier_unknown" in _codes(
        cv.verification_problems(_r, _v, plan=_plan))


def test_a_record_whose_obligation_basis_changed_is_not_reused():
    """支座变了（主张改过 / 换了派生口径）⇒ 旧记录不复用；空字段同样不复用（fail-closed）."""
    _v = _view()
    _plan = cv.view_candidate(_v, _v)
    _r = _record(_v)
    assert cv.record_basis_matches(_r, _plan) is True
    _r["obligation_basis"] = "0" * 64
    assert "verify_obligation_basis_mismatch" in _codes(
        cv.verification_problems(_r, _v, plan=_plan))
    assert cv.record_basis_matches(_r, _plan) is False
    _r.pop("obligation_basis")
    assert cv.record_basis_matches(_r, _plan) is False
    assert "verify_obligation_basis_mismatch" in _codes(
        cv.verification_problems(_r, _v, plan=_plan))


def test_a_fact_without_a_claim_has_no_verifiable_basis():
    """冻结主张为空 ⇒ 派生不出义务：请求侧**零调用**挡住，验收侧也据实拒收."""
    _v = _view()
    _v["frozen_facts"][0]["claim"] = "  "
    from step01_script_gen.content_verify import _request_admission
    _adm = _request_admission(_v)
    assert "verify_obligation_basis_missing" in [x.get("code") for x in _adm], _adm
    _r = _record(_v)
    assert "verify_obligation_basis_missing" in _codes(
        cv.obligation_axis_problems(_r, _v))


@pytest.mark.parametrize("mutate, want", [
    (lambda o: o.__setitem__("obligations", [1]), "verify_record_obligation_not_object"),
    (lambda o: o["obligations"][0].pop("reason"), "verify_record_field_missing"),
    (lambda o: o["obligations"][0].pop("evidence"), "verify_record_field_missing"),
    (lambda o: o["obligations"][0].__setitem__("evidence", 1),
     "verify_record_field_type"),
    # v4：证据项自身也逐项判（旧写法只点名一个载体，联合承载表达不了）
    (lambda o: o["obligations"][0]["evidence"].__setitem__(0, "x"),
     "verify_obligation_evidence_not_object"),
    (lambda o: o["obligations"][0]["evidence"][0].pop("carrier_uid"),
     "verify_record_field_missing"),
    (lambda o: o["obligations"][0]["evidence"][0].__setitem__("carrier_uid", 123),
     "verify_record_field_type"),
    (lambda o: o["obligations"][0]["evidence"][0].__setitem__("extra", "x"),
     "verify_obligation_evidence_unknown_field"),
    (lambda o: o["obligations"][0].__setitem__("carrier_uid", "c01"),
     "verify_obligation_unknown_field"),
])
def test_obligation_rows_are_checked_by_the_same_record_contract(mutate, want):
    """义务行的**形状**由同一份记录契约判（坏成员不筛掉、字段路径与原值都在）."""
    _v = _view()
    _r = _record(_v)
    mutate(_r["facts"][0])
    _got = cv.record_shape_problems(_r)
    assert want in [x["code"] for x in _got], (want, [x["code"] for x in _got])


def test_an_old_rule_record_is_not_reused_under_the_new_obligations():
    """**旧规则记录不许按新义务复用**：v1 记录在形状层就被拒，装载也拒."""
    import copy as _copy
    _v = _view()
    _plan = cv.view_candidate(_v, _v)
    _dig = cv.plan_digest_of(_plan)
    _old = _copy.deepcopy(_record(_v))
    _old["schema"] = "content_verification.v1"
    _old["rule_version"] = "content_verification.v1.semantic_carriers.1"
    for _row in _old["facts"]:
        _row.pop("obligations", None)
    _old.pop("obligation_basis", None)
    _shape = [x["code"] for x in cv.record_shape_problems(_old)]
    assert "verify_schema_unknown" in _shape, _shape
    assert _shape.count("verify_record_field_missing") == len(_old["facts"]), _shape
    _calls = []
    _ctl = cv.VerificationController(
        verify=lambda view, dig: (_calls.append(dig), _record(_v))[1],
        novel_digest=_NOVEL_DIGEST)
    assert _ctl.seed(_dig, _old) is False, "旧规则记录不许装载"

def test_quote_style_variants_do_not_break_the_verbatim_source_gate():
    """**引号风格差异不改"逐字"结论**（2026-10-10 真实闭环实测的形态）.

    真实来源轴返回把原文的双引号 `"噗"` 写成中文单引号 `'噗'`，其余 51 字逐字相同；
    旧归一化集漏了 `‘’` ⇒ 逐字门判"非逐字"，真实修订闭环卡在来源轴。
    判据在 `cv._norm`（来源轴 span 门与展示侧落点定位**共用**的那一份）。
    """
    _novel = "他道清寒幽冷的光芒“噗”地一声便刺。"
    _model = "他道清寒幽冷的光芒‘噗’地一声便刺。"
    assert _model != _novel, "夹具：两种引号确实不同"
    assert cv._norm(_model) == cv._norm(_novel), "引号风格差异被当成非逐字"
    # 判别：正文真的不同时仍不相等（不是"什么都放行"）
    assert cv._norm("他道清寒幽冷的光芒‘噗’地一声便刺入。") != cv._norm(_novel)
    assert cv._norm(_novel + "多出的字") != cv._norm(_novel)
