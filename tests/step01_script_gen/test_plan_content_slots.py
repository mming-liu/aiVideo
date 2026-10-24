# -*- coding: utf-8 -*-
"""**内容槽位适配器**（规划收口计划 §5.6，2026-10-08）.

覆盖范围（§5.6 的对照表）：槽位按稳定 UID 生成、不可表达时整轮落回既有 delta、
模型只选内容与关系（代码装配增量）、坏回答结构化拒收且零应用、同实体多槽合并/冲突、
编译出的增量经**既有应用器与同一条验收链**、一轮一次额度。

夹具是**真实残余**：ch1 自动规划留下的两条 `verify_fact_unsupported`（f02 / f08）+
结构完整的 hybrid 计划 + 冻结原文（`fixtures/plan_content_slots_ch1_v1.json`）。
"""
from __future__ import annotations

import copy
import functools
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import content_verification as cv                                   # noqa: E402
from shared import presentation_contract as pc                                  # noqa: E402
from step01_script_gen import plan_content_repair as pcr                        # noqa: E402
from step01_script_gen import outline as o                                      # noqa: E402

#: 本文件测的是**生产路由**（v2 增量 / 内容槽位），不是旧协议回放——
#: 目录级 conftest 默认把用例标成 `ROUTE_AUTO` 回放（那会让"按能力表挑接口"生效）。
pytestmark = pytest.mark.v2_request

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "plan_content_slots_ch1_v1.json"


def _fixture() -> dict:
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    # This suite supplies explicit verdict fixtures for current routing, not a
    # historical cache acceptance test. The hybrid source semantics are unchanged.
    fixture["plan"]["plan_verification"]["rule_version"] = cv.DEFAULT_RULE_VERSION
    return fixture


def _novel() -> str:
    return _fixture()["novel_text"]


def _Budget(**kw):
    from step01_script_gen.plan_revision import RevisionBudget
    return RevisionBudget(**dict({"max_calls": 3, "deadline_sec": 120.0}, **kw))


def _codes(problems) -> list:
    return [str((_p or {}).get("code") or "") for _p in (problems or [])]


def _real_record() -> dict:
    """夹具**自带**的真实核验记录（源轴的真实逐字片段、载体轴的真实 partial 都在里面）."""
    return copy.deepcopy(_fixture()["plan"]["plan_verification"])


#: **冻结的完整演出样本**（复核 2026-10-08 r2 P1 要求）——见「② 合法回答」一节的
#: `_frozen_samples()`：样本的**身份是整条事实的展示集合**（逐字、排序），裁决随样本
#: 冻结；表外 + 正文变过一律 `partial`。
def _texts_of(view, fact_uid) -> tuple:
    """一条事实**当前实际展示的全部正文**（排序后的整份集合）——样本的身份就是它."""
    return tuple(sorted(str(_t) for _t in cv.all_carrier_texts(view, fact_uid)
                        if str(_t)))


@functools.lru_cache(maxsize=None)
def _base_texts() -> dict:
    """夹具基线里每条事实的展示集合——那份**真实记录**绑的就是这一次内容."""
    _view, _err = pc.view_from_plan(_fixture()["plan"])
    assert _view is not None and not _err, _err
    return {str(_x["uid"]): _texts_of(_view, _x["uid"]) for _x in _view["frozen_facts"]}


def _answer_view(answer) -> dict:
    """一份槽位回答 → **真的**跑一遍（编译 → 既有应用器）得到的候选视图."""
    _view, _err = pc.view_from_plan(_fixture()["plan"])
    assert _view is not None and not _err, _err
    _found = pcr.content_slots(_view, _fixture()["problems"])
    assert not _found["unexpressible"] and not _found.get("blocked"), _found
    _d, _perr = pcr.compile_delta(_view, _found["slots"], answer, novel_text=_novel())
    assert not _perr, _perr
    _got, _aerr, _state = pc.apply_delta_view(_view, _d, accept=lambda _: [])
    assert _state["kind"] == "applied", (_state, _aerr)
    return _got


def _stub_obligations(view, fact_uid, verdict) -> list:
    """桩记录的**义务行**——按**冻结主张派生的那一条**来给（v3：集合由代码定）.

    本桩不是生产核验器：它只回答**给定**的那条义务（id 与 text 原样回带，不增删改写）。
    判 `supported` 时点名当前实际展示的一个真实载体并给出逐字片段；否则该义务判
    `partial`、不带载体与片段（**不替模型编造"哪一部分差在哪"**——那属于真实核验的
    语义判断，由实跑校准）。
    """
    _want = cv.frozen_obligations(view).get(str(fact_uid)) or []
    if not _want:
        return []
    _o = _want[0]
    _row = {"obligation_id": _o["obligation_id"], "text": _o["text"],
            "verdict": cv.VERDICT_PARTIAL, "evidence": [],
            "missing": "本桩只对冻结样本判定：这一条在当前展示下不成立", "reason": ""}
    if str(verdict) == cv.VERDICT_SUPPORTED:
        for _b in view.get("fact_bindings") or []:
            if str(_b.get("fact_uid")) != str(fact_uid):
                continue
            for _c in _b.get("carriers") or []:
                for _t in cv.carrier_texts(view, _c):
                    if _t:
                        return [dict(_row, verdict=cv.VERDICT_SUPPORTED, missing="",
                                     evidence=[{"carrier_uid": pc.ref_uid(_c),
                                                "exhibitor_span": _t}])]
    return [_row]

def _controller(samples=None) -> cv.VerificationController:
    """桩核验：**有限整份样本**的明确裁决（`samples=None` ⇒ 用 `FROZEN_SAMPLES`）.

    判据只有两条，都取**已有证据**：

    ① **冻结样本表**：一条事实的**整份展示集合**逐字等于某个样本 ⇒ 判该样本写明的裁决。
       样本的**身份是整份集合**，不是"某段正文里含某个短语"——短语既不是完整主张
       （「终挂上十大豪门的匾额」不含主体与时间跨度），也会让"只删掉一个限定关系"照样
       通过（复核实测：F2 新画面只写这半句，旧桩仍给 `supported`，真实修订控制器一次
       调用就 `stop=ok / problems=[]`）。
    ② 夹具**自带的那份真实记录**：只在该事实的展示集合**逐字未变**时才作数——正文一变，
       旧结论一律不作数（回落 `partial`）。旧桩在这里把"没有已展示片段"的支持结论
       **换成最长可用片段**后继续给 `supported`，等于"内容怎么改都算通过"。
       片段也不再代打：它必须**逐字出自当前载体正文**，判据在统一验收那一处
       （`verify_support_span_not_exhibited`），本桩不替它修。

    表外 + 正文变过的组合一律 `partial`：本桩**不给它没验过的内容发通过证**。
    这不是生产核验器——真实模型能否区分"完整"与"缺限定关系"，必须由实跑校准
    （工作包 §84.2 第 1 条），不能拿本桩当证据。
    """
    from step01_script_gen.content_verify import novel_identity
    _rec = _real_record()
    _base = _base_texts()
    _tbl = dict(FROZEN_SAMPLES if samples is None else samples)

    def _verify(view, digest):
        _out = copy.deepcopy(_rec)
        _out["plan_digest"] = digest
        for _f in _out.get("facts") or []:
            _fu = str(_f.get("fact_uid"))
            _now = _texts_of(view, _fu)
            if _now != _base.get(_fu):
                _f.update(verdict=cv.VERDICT_PARTIAL, support_spans=[],
                          missing="实际展示正文与夹具记录绑定的那一次**逐字不同**——"
                                  "本桩只对冻结样本判定，不给没验过的内容发通过证",
                          suggested_carriers=[])
            _hit = next((_s for _s in (_tbl.get(_fu) or []) if _s["texts"] == _now), None)
            if _hit is not None:
                _f.update(verdict=_hit["verdict"],
                          support_spans=list(_hit["support_spans"]),
                          missing=str(_hit.get("missing") or ""),
                          suggested_carriers=[])
            # **义务行总是跟着最终结论走**（v3）：待核集合由冻结主张定，事实裁决
            # 由逐条义务派生——只改 verdict 而不改义务行，记录就不自洽了。
            _f.update(obligations=_stub_obligations(view, _fu, _f.get("verdict")))
        return _out

    return cv.VerificationController(verify=_verify,
                                     novel_digest=novel_identity(_novel())["digest"],
                                     rule_version=o.VERIFY_RULE_VERSION)


class _Res:
    def __init__(self, payload, cid="c1"):
        self.success = True
        self.text = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) \
            else payload
        self.error = ""
        self.call_id = cid


def _stub_view_echo(monkeypatch, *, calls=None):
    """桩服务：按**完整视图协议**把请求里的基线视图**原样回带**（`no_change` 形态）.

    本文件测的是**路由**（槽位表达不了 ⇒ 落回非槽位那条 v2 路），不是"模型改得对"。
    回一份**形状合法**的完整视图让"落回"这件事真的被走到（合法形状 ⇒ 不会因为
    "协议不对"被拒收，于是断言的是路由而不是拒收）。基线摘要必须取自**这次真实请求**
    ——夹一份自己算的基线正是老探针被拒收三次的成因。
    """
    _calls = calls if calls is not None else []
    _seen: dict = {}
    _real = o.revision_request

    def _rec(*a, **k):
        _r = _real(*a, **k)
        _seen.clear()
        _seen.update(_r)
        return _r

    monkeypatch.setattr(o, "revision_request", _rec)

    def _answer(_req):
        return {"schema": pc.REVISION_SCHEMA, "rule_version": o.PLAN_VIEW_RULE_VERSION,
                "base_plan_hash": _seen["base_plan_hash"],
                "fact_basis_hash": _seen["fact_basis_hash"],
                "view": _seen["view"]}

    return _stub(monkeypatch, _answer, calls=_calls)


def _stub(monkeypatch, answer, *, calls=None):
    """桩服务：**只**回槽位回答（并数调用次数，证明"一轮=一次调用、坏回答不重发"）."""
    _calls = calls if calls is not None else []

    class _Svc:
        def generate(self, req):
            _calls.append(req)
            _payload = answer(req) if callable(answer) else answer
            return _Res(_payload, "slotcall%d" % len(_calls))

    monkeypatch.setattr(o, "create_service", lambda *a, **k: _Svc())
    return _calls


# ---------------------------------------------------------------------------
# ① 槽位生成：按稳定 UID，不从措辞猜
# ---------------------------------------------------------------------------
def test_slots_are_generated_from_the_real_residuals_by_stable_uid():
    _f = _fixture()
    _view, _err = pc.view_from_plan(_f["plan"])
    assert _view is not None and _err == [], _err
    _found = pcr.content_slots(_view, _f["problems"])
    assert _found["unexpressible"] == [], _found["unexpressible"]
    assert [str(_s["slot_id"]) for _s in _found["slots"]] == \
        ["slot:fact:f02", "slot:fact:f08"]
    for _s in _found["slots"]:
        assert _s["claim"] and _s["evidence"], _s        # 事实身份与原文证据都在槽里
        assert _s["target_uids"], _s                     # 允许的现有画面集合非空
        assert _s["insert_positions"][0] == "", _s       # 空串 = 排在最后
        assert set(_s["problem_codes"]) == {"verify_fact_unsupported"}, _s


def test_a_code_the_adapter_cannot_express_falls_back_to_the_existing_route(monkeypatch):
    """**能表达才接入**（§5.6 第 4 条）：有一条表达不了 ⇒ 整轮走既有的非槽位 v2 路.

    hybrid 的非槽位路是**完整视图**（`ROUTE_VIEW`，2026-10-11 隔离对照的裁决）；
    这里回一份**形状合法**的完整视图，证明"落回"真的被走到（不是被拒收掩盖）。
    """
    _f = _fixture()
    _probs = list(_f["problems"]) + [{"code": "presentation_beat_events_shape",
                                      "uid": "b01", "path": "presentation.beat_events",
                                      "reason": "形状违约"}]
    _calls = _stub_view_echo(monkeypatch)
    _out = o.revise_plan_once(_f["plan"], _probs, _Budget(), None, novel_text=_novel(),
                              verification=_controller())
    assert _out["evidence"]["content_slots"]["applicable"] is False
    assert _out["evidence"]["route"] == o.ROUTE_VIEW
    assert "slots_applied" not in str(_out.get("detail") or "")
    assert len(_calls) == 1


# ---------------------------------------------------------------------------
# ② 合法回答：经**真实入口**装配增量、既有应用器、同一条验收链
# ---------------------------------------------------------------------------
def _legal_answer(view) -> dict:
    _beats = [str(_b["uid"]) for _b in view["beats"]]
    return {"schema": pcr.SLOT_ANSWER_SCHEMA, "answers": [
        {"slot_id": "slot:fact:f02", "action": pcr.ACT_CREATE,
         "text": "四年前的旧账房里，少年杨旭在父亲灵前接过账册，此后三年在青州城里"
                 "一间间铺面奔走，账面上的银钱越滚越多，终挂上十大豪门的匾额。",
         "insert_after_uid": _beats[0], "event_index": 0},
        {"slot_id": "slot:fact:f08", "action": pcr.ACT_BIND, "text": "",
         "target_uid": _beats[-1]}]}


def _complete_answer(view) -> dict:
    """**完整合法候选**：F2 的新画面演出主体 + 时间跨度 + 结果；F8 绑到**真的演出了
    「要连夜送公子回城」的那条现有画面**（`b07`）——而不是"马车离开"（`b08`）.

    合法回答 `_legal_answer` 只保证**准入合法**（动作/字段/取值都在允许集合里），
    **不保证内容完整**：它给 F8 绑的是 `b08`（马车疾驰而出），那条正文没有回城目的。
    """
    _a = copy.deepcopy(_legal_answer(view))
    _a["answers"][1] = {"slot_id": "slot:fact:f08", "action": pcr.ACT_BIND, "text": "",
                        "target_uid": "b07"}
    return _a


def _time_cut_answer(view) -> dict:
    """**单变量对照候选**：只把 F2 正文里的时间跨度删掉（「此后三年」→「此后」），其余一字不动.

    校准脚本与离线矩阵**共用这一份**（复核 2026-10-08 P1 点名的同条件对照）——
    两处各造一份就会各自漂移，测出来的东西就不是同一个了。
    """
    _a = copy.deepcopy(_legal_answer(view))
    _a["answers"][0]["text"] = _a["answers"][0]["text"].replace("此后三年", "此后")
    return _a


def _sample(texts, verdict, missing="") -> dict:
    """冻结样本的一条：**整份展示集合** + **明确裁决**（支持片段随样本一起冻结）.

    支持片段取该样本自己的载体正文（样本身份是整份集合，不是在未知变化上"换最长片段"）。
    """
    return {"texts": tuple(texts), "verdict": verdict,
            "support_spans": ([max(texts, key=len)] if texts
                              and verdict == cv.VERDICT_SUPPORTED else []),
            "missing": missing}


def _frozen_samples() -> dict:
    """冻结样本表：**由真实候选跑出来**（编译 → 既有应用器 → 读实际载体正文）.

    不手抄短语、不按作品措辞判——更换人物/作品后同一条规则仍然成立。
    样本的差别**只在候选正文**（单变量）：完整 / 只删时间跨度 / 只留结果 / 缺回城目的。
    """
    _view, _err = pc.view_from_plan(_fixture()["plan"])
    assert _view is not None and not _err, _err
    _legal = _legal_answer(_view)
    _full = _complete_answer(_view)
    _time_cut = _time_cut_answer(_view)
    _result_only = copy.deepcopy(_legal)
    _result_only["answers"][0]["text"] = "终挂上十大豪门的匾额。"
    return {
        "f02": [_sample(_texts_of(_answer_view(_legal), "f02"), cv.VERDICT_SUPPORTED),
                _sample(_texts_of(_answer_view(_time_cut), "f02"), cv.VERDICT_PARTIAL,
                        missing="没有表达「四年前接手家业」与「此后三年」的时间跨度"),
                _sample(_texts_of(_answer_view(_result_only), "f02"), cv.VERDICT_PARTIAL,
                        missing="只写了结果，主体与时间跨度都没表达")],
        "f08": [_sample(_texts_of(_answer_view(_full), "f08"), cv.VERDICT_SUPPORTED),
                _sample(_texts_of(_answer_view(_legal), "f08"), cv.VERDICT_PARTIAL,
                        missing="只演了「公子醉酒」与「马车离开」，"
                                "没有「要连夜送公子回城」的目的表达")],
    }


FROZEN_SAMPLES = _frozen_samples()
#: **缺关键限定关系**的那几个样本（时间跨度 / 回城目的）。同名旧常量表达的是"跨样本的
#: 短语包含"，改的是**检查器配置**而不是候选正文，不是单变量对照；现在它是样本表里
#: 判 `partial` 的那几个（复核 2026-10-08 r2 P1）。
FROZEN_SAMPLES_MISSING_TIME = {
    _fu: [_s for _s in _lst if _s["verdict"] != cv.VERDICT_SUPPORTED]
    for _fu, _lst in FROZEN_SAMPLES.items()}


def test_a_legal_slot_answer_applies_through_the_real_entry(monkeypatch):
    _f = _fixture()
    _plan = _f["plan"]
    _view, _ = pc.view_from_plan(_plan)
    _calls = _stub(monkeypatch, _legal_answer(_view))
    _out = o.revise_plan_once(_plan, _f["problems"], _Budget(), None, novel_text=_novel(),
                              verification=_controller())
    assert _out["kind"] == "applied", (_out.get("kind"), _out.get("detail"))
    assert len(_calls) == 1, "一轮只许一次调用"
    _new = _out["plan"]
    assert _new["plan_revision"]["schema"] == pcr.SLOT_REQUEST_SCHEMA
    assert _new["plan_revision"]["transport_protocol"] == pcr.SLOT_REQUEST_SCHEMA
    assert _new["plan_revision"]["rule_version"] == pcr.REPAIR_RULE_VERSION
    assert len(_new["shot_beats"]) == len(_plan["shot_beats"]) + 1
    # 新画面插在 b01 之后；f08 的绑定补上了选中的现有画面
    _nv, _ = pc.view_from_plan(_new)
    _ids = [str(_b["uid"]) for _b in _nv["beats"]]
    # 临时 UID 由 codec 换成**正式编号**（代码负责映射，模型不写编号）
    assert str(_nv["beats"][1]["text"]).startswith("四年前的旧账房"), _nv["beats"][1]["text"]
    assert _ids.count(_ids[1]) == 1, _ids
    _b8 = [str(pc.ref_uid(_c)) for _c in
           next(_b["carriers"] for _b in _nv["fact_bindings"] if _b["fact_uid"] == "f08")]
    assert _ids[-1] in _b8, _b8
    _b2 = [str(pc.ref_uid(_c)) for _c in
           next(_b["carriers"] for _b in _nv["fact_bindings"] if _b["fact_uid"] == "f02")]
    assert _ids[1] in _b2, _b2


def test_the_round_consumes_exactly_one_budget_call(monkeypatch):
    """一次槽位调用 = **一次额度**（控制器扣一次；坏回答不重发）。

    ⚠️ 用**完整**候选：桩判据改成整份样本判定之后，`_legal_answer`（F8 绑的 `b08`
    没有回城目的）不再被判 `supported`——"合法回答"只保证准入合法，不保证内容完整。
    本用例测的是**额度记账**，所以要的是一轮就验收通过的那份候选。
    """
    from step01_script_gen.plan_revision import RevisionBudget
    _f = _fixture()
    _view, _ = pc.view_from_plan(_f["plan"])
    _calls = _stub(monkeypatch, _complete_answer(_view))
    _budget = RevisionBudget(max_calls=3, deadline_sec=120.0)
    _ctl = _controller()
    _res = o.run_plan_revision(_f["plan"], _novel(), budget=_budget, max_calls=3,
                               verification=_ctl)
    assert len(_calls) == 1, _calls
    assert _budget.calls_used == 1, _budget.as_dict()
    assert _res["stop_reason"] == "ok", (_res["stop_reason"], _res["problems"][:2])
    assert _res["problems"] == []


# ---------------------------------------------------------------------------
# ③ 坏回答：结构化拒收、**零应用**、不重发（§5.6 对照表）
# ---------------------------------------------------------------------------
def _answer(**over) -> dict:
    _f = _fixture()
    _view, _ = pc.view_from_plan(_f["plan"])
    _a = _legal_answer(_view)
    for _k, _v in over.items():
        _a[_k] = _v
    return _a


def _run_round(monkeypatch, answer):
    _f = _fixture()
    _calls = _stub(monkeypatch, answer)
    _out = o.revise_plan_once(_f["plan"], _f["problems"], _Budget(), None,
                              novel_text=_novel(), verification=_controller())
    return _f, _out, _calls


def _drop_second(_a):
    _a["answers"] = _a["answers"][:1]


def _dup_first(_a):
    _a["answers"] = _a["answers"] + [_a["answers"][0]]


def _unknown_slot(_a):
    _a["answers"][1]["slot_id"] = "slot:fact:nope"


def _bad_action(_a):
    _a["answers"][0]["action"] = pcr.ACT_NARROW_CARD


def _bad_target(_a):
    _a["answers"][1]["target_uid"] = "bZZ"


def _bad_position(_a):
    _a["answers"][0]["insert_after_uid"] = "bZZ"


def _int_text(_a):
    _a["answers"][0]["text"] = 123


def _str_event_index(_a):
    _a["answers"][0]["event_index"] = "0"


def _bool_event_index(_a):
    _a["answers"][0]["event_index"] = True


def _range_event_index(_a):
    _a["answers"][0]["event_index"] = 99


def _na_target_on_card(_a):
    _a["answers"][0]["target_uid"] = "b01"


def _na_position_on_bind(_a):
    _a["answers"][1]["insert_after_uid"] = "b01"


def _na_event_on_bind(_a):
    _a["answers"][1]["event_index"] = 0


def _empty_text(_a):
    _a["answers"][0]["text"] = "   "


def _text_on_bind(_a):
    _a["answers"][1]["text"] = "顺手改一改"


def _unknown_key(_a):
    _a["answers"][0]["extra"] = "x"


def _bad_schema(_a):
    _a["schema"] = "plan_content_repair.answer.v0"


def _items_not_list(_a):
    _a["answers"] = {"slot:fact:f02": {}}


@pytest.mark.parametrize("mutate, want", [
    (_bad_schema, "repair_answer_schema_unknown"),
    (_items_not_list, "repair_answer_items_type"),
    (_unknown_key, "repair_answer_unknown_key"),
    (_unknown_slot, "repair_answer_slot_unknown"),
    (_drop_second, "repair_answer_slot_unanswered"),
    (_dup_first, "repair_answer_slot_duplicate"),
    (_bad_action, "repair_answer_action_not_allowed"),
    (_bad_target, "repair_answer_target_not_allowed"),
    (_bad_position, "repair_answer_position_not_allowed"),
    (_int_text, "repair_answer_field_type"),
    (_str_event_index, "repair_answer_field_type"),        # 类型先于取值
    (_bool_event_index, "repair_answer_field_type"),       # bool 不是 int
    (_range_event_index, "repair_answer_event_index_invalid"),
    (_na_target_on_card, "repair_answer_field_not_applicable"),
    (_na_position_on_bind, "repair_answer_field_not_applicable"),
    (_na_event_on_bind, "repair_answer_field_not_applicable"),
    (_empty_text, "repair_answer_text_empty"),
    (_text_on_bind, "repair_answer_text_unexpected"),
])
def test_a_bad_slot_answer_is_refused_with_zero_application(monkeypatch, mutate, want):
    """坏回答 ⇒ **整轮拒收、零应用、不重发**；原因带槽位与原值（进下一轮反馈）."""
    _a = _answer()
    mutate(_a)
    _f, _out, _calls = _run_round(monkeypatch, _a)
    assert _out["kind"] == "illegal", _out
    assert want in str(_out["detail"]), _out["detail"]
    assert _out.get("plan") is None, "被拒的回答不许产生候选"
    assert len(_calls) == 1, "坏回答**不在内部重发**（一次槽位调用 = 一次额度）"
    assert _out["evidence"]["route"] == "content_slots"


# ---------------------------------------------------------------------------
# ④ 编译：同实体多槽合并/冲突、同事实多载体、卡片收窄
# ---------------------------------------------------------------------------
def _slot(sid, fact_uid, actions, targets, *, card_id="", events=()):
    return {"slot_id": sid, "kind": "card_text" if card_id else "fact_visual",
            "fact_uid": fact_uid, "card_id": card_id, "problem_codes": [],
            "claim": "", "evidence": "", "allowed_actions": list(actions),
            "target_uids": list(targets), "insert_positions": [""] + list(targets),
            "current_carriers": [], "current_text": "",
            "event_uids": list(events), "sources": []}


def _answ(pairs) -> dict:
    return {"schema": pcr.SLOT_ANSWER_SCHEMA, "answers": list(pairs)}


def test_two_slots_on_the_same_visual_merge_or_conflict():
    _f = _fixture()
    _view, _ = pc.view_from_plan(_f["plan"])
    _slots = [_slot("slot:fact:f02", "f02", [pcr.ACT_REWRITE], ["b01"]),
              _slot("slot:fact:f05", "f05", [pcr.ACT_REWRITE], ["b01"])]
    _diff = _answ([{"slot_id": "slot:fact:f02", "action": pcr.ACT_REWRITE,
                    "text": "改法一", "target_uid": "b01"},
                   {"slot_id": "slot:fact:f05", "action": pcr.ACT_REWRITE,
                    "text": "改法二", "target_uid": "b01"}])
    _d, _p = pcr.compile_delta(_view, _slots, _diff)
    assert _d is None and [x["code"] for x in _p] == ["repair_slot_conflict"], _p
    _same = _answ([{"slot_id": "slot:fact:f02", "action": pcr.ACT_REWRITE,
                    "text": "同一改法", "target_uid": "b01"},
                   {"slot_id": "slot:fact:f05", "action": pcr.ACT_REWRITE,
                    "text": "同一改法", "target_uid": "b01"}])
    _d2, _p2 = pcr.compile_delta(_view, _slots, _same)
    assert _p2 == [] and len(_d2["upsert"]["beats"]) == 1, _d2


def test_two_slots_on_the_same_fact_keep_both_carriers():
    """同一事实被两个槽绑到**不同**画面 ⇒ 一次原子 delta 里两个载体都在（不覆盖）."""
    _f = _fixture()
    _view, _ = pc.view_from_plan(_f["plan"])
    _slots = [_slot("slot:a", "f08", [pcr.ACT_BIND], ["b07"]),
              _slot("slot:b", "f08", [pcr.ACT_BIND], ["b08"])]
    _d, _p = pcr.compile_delta(_view, _slots, _answ([
        {"slot_id": "slot:a", "action": pcr.ACT_BIND, "text": "", "target_uid": "b07"},
        {"slot_id": "slot:b", "action": pcr.ACT_BIND, "text": "", "target_uid": "b08"}]))
    assert _p == [] and _d is not None
    _bind = next(_b for _b in _d["fact_bindings"] if _b["fact_uid"] == "f08")
    _got = sorted(str(pc.ref_uid(_c)) for _c in _bind["carriers"])
    assert _got == ["b07", "b08", "d02"], _got


def test_a_card_slot_narrows_the_text_and_keeps_the_other_fields():
    _f = _fixture()
    _view, _ = pc.view_from_plan(_f["plan"])
    _card = _view["screen_cards"][0]
    _sid = "slot:card:%s" % _card["card_id"]
    _slots = [_slot(_sid, "", [pcr.ACT_NARROW_CARD], [_card["card_id"]],
                    card_id=_card["card_id"])]
    _d, _p = pcr.compile_delta(_view, _slots, _answ([
        {"slot_id": _sid, "action": pcr.ACT_NARROW_CARD, "text": "今年刚及弱冠。"}]))
    assert _p == [] and _d is not None
    _up = _d["upsert"]["screen_cards"][0]
    assert _up["text"] == "今年刚及弱冠。"
    for _k in ("template", "placement", "beat_uid"):
        assert _up[_k] == _card[_k], _k
    assert "beats" not in (_d.get("upsert") or {}), "只改卡片就不许动节拍"
    assert "order" not in _d, "集合没变就不给 order"


def test_a_card_slot_reaches_the_real_entry_and_keeps_residuals(monkeypatch):
    """卡片收窄走真实入口 ⇒ `applied`；**局部改善仍有残余 ⇒ 保留候选**（不可执行由整链判）."""
    _f = _fixture()
    _view, _ = pc.view_from_plan(_f["plan"])
    _cid = str(_view["screen_cards"][0]["card_id"])
    _probs = [{"code": "presentation_card_not_in_novel", "uid": _cid, "kind": "card",
               "path": "presentation.screen_cards[0].text", "reason": "不是原文逐字片段"}]
    _calls = _stub(monkeypatch, _answ([
        {"slot_id": "slot:card:%s" % _cid, "action": pcr.ACT_NARROW_CARD,
         "text": "今年刚及弱冠。"}]))
    _out = o.revise_plan_once(_f["plan"], _probs, _Budget(), None, novel_text=_novel(),
                              verification=_controller())
    assert _out["kind"] == "applied", _out.get("detail")
    assert len(_calls) == 1
    _nv, _ = pc.view_from_plan(_out["plan"])
    assert str(_nv["screen_cards"][0]["text"]) == "今年刚及弱冠。"
    # 残余（f02/f08 的载体轴）仍在 ⇒ 候选保留、由既有整链决定是否可执行
    assert _out["evidence"]["residuals"] >= 1, _out["evidence"]


def test_the_adapter_pre_empts_the_delta_route_for_slot_expressible_problems():
    """生产分派**先**问适配器：本轮问题都能表达 ⇒ 走槽位路.

    （既有 `tests/shared/test_plan_revision_delta_end_to_end.py` 钉的是 delta **传输腿**，
    它在自己的用例里显式把适配器让开——两边的覆盖边界在这里对齐。）
    """
    _f = _fixture()
    _ok, _why, _ctx = o._content_slots_applicable(_f["plan"], _f["problems"])
    assert (_ok, _why) == (True, ""), (_ok, _why)
    assert [str(_s["slot_id"]) for _s in _ctx["found"]["slots"]] == \
        ["slot:fact:f02", "slot:fact:f08"]


# ---------------------------------------------------------------------------
# ⑤ 复核 2026-10-08 的五类缺陷（生产者身份 / 共享写入 / 上下文 / 顺序 / 字段契约）
# ---------------------------------------------------------------------------
def test_a_card_alert_carries_the_card_identity_not_the_fact():
    """**空卡片告警**要按**卡片**身份进槽（旧写法给的是事实 uid ⇒ 找错实体、白花一次调用）."""
    _f = _fixture()
    _view, _ = pc.view_from_plan(_f["plan"])
    _cid = str(_view["screen_cards"][0]["card_id"])
    _probs = [{"code": "presentation_card_text_empty", "kind": "card", "uid": _cid,
               "fact_uid": "f01", "path": "presentation.screen_cards[0].text",
               "reason": "卡片 %s 没有正文" % _cid}]
    _found = pcr.content_slots(_view, _probs)
    assert _found["unexpressible"] == [], _found["unexpressible"]
    assert [str(_s["slot_id"]) for _s in _found["slots"]] == ["slot:card:%s" % _cid]
    _card_slot = _found["slots"][0]
    assert _card_slot["kind"] == "card_text"
    # **卡片槽要带上它必须保留的冻结主张**（从绑定反查，不让模型猜卡片用途）
    assert [str(_x["uid"]) for _x in _card_slot["facts"]] == ["f01"], _card_slot["facts"]
    _req = pcr.slot_request(_view, _found["slots"])
    assert [str(_x["uid"]) for _x in _req["frozen_facts"]] == ["f01"], _req["frozen_facts"]
    assert _req["frozen_facts"][0]["claim"], _req["frozen_facts"]


def test_an_unknown_target_is_unexpressible_and_costs_no_call():
    """目标不存在 ⇒ **调用前**就判不适用（不许花掉一次额度）."""
    _f = _fixture()
    _view, _ = pc.view_from_plan(_f["plan"])
    for _code, _uid in (("presentation_card_text_empty", "cZZ"),
                        ("verify_fact_unsupported", "fZZ")):
        _found = pcr.content_slots(_view, [{"code": _code, "kind": "card", "uid": _uid}])
        assert _found["slots"] == [], _found["slots"]
        assert any(_uid in _x for _x in _found["unexpressible"]), _found["unexpressible"]
    _ok, _why, _ctx = o._content_slots_applicable(
        _f["plan"], [{"code": "presentation_card_text_empty", "kind": "card", "uid": "cZZ"}])
    assert _ok is False and "cZZ" in _why, (_ok, _why)


def test_two_slots_sharing_one_visual_keep_both_relations():
    """共享画面：正文一致 ⇒ **一份 upsert + 两份完整绑定**（旧写法把第二条关系一起丢了）."""
    _f = _fixture()
    _view, _ = pc.view_from_plan(_f["plan"])
    _slots = [_slot("slot:fact:f02", "f02", [pcr.ACT_REWRITE], ["b01"]),
              _slot("slot:fact:f05", "f05", [pcr.ACT_REWRITE], ["b01"])]
    _same = _answ([{"slot_id": "slot:fact:f02", "action": pcr.ACT_REWRITE,
                    "text": "同一改法", "target_uid": "b01"},
                   {"slot_id": "slot:fact:f05", "action": pcr.ACT_REWRITE,
                    "text": "同一改法", "target_uid": "b01"}])
    _d, _p = pcr.compile_delta(_view, _slots, _same)
    assert _p == [] and len(_d["upsert"]["beats"]) == 1, _d
    _got = {str(_b["fact_uid"]): [str(pc.ref_uid(_c)) for _c in _b["carriers"]]
            for _b in _d["fact_bindings"]}
    assert "f02" in _got and "f05" in _got, _got
    assert "b01" in _got["f05"], _got
    # **两条事实都真的引用它**（交既有应用器）
    _cv, _vp, _rk = pc.apply_delta_view(_view, _d, mode=pc.MODE_HYBRID)
    assert _rk["kind"] == "applied", (_rk, _vp)
    for _fu in ("f02", "f05"):
        _c = next(_b for _b in _cv["fact_bindings"] if _b["fact_uid"] == _fu)
        assert "b01" in [str(pc.ref_uid(_x)) for _x in _c["carriers"]], (_fu, _c)


def test_two_creates_on_the_same_anchor_follow_the_answer_order():
    """同锚点插入：最终顺序按**回答顺序**（旧写法每来一个就插在锚点后 ⇒ 顺序被反转）."""
    _f = _fixture()
    _view, _ = pc.view_from_plan(_f["plan"])
    _slots = [_slot("slot:fact:f02", "f02", [pcr.ACT_CREATE], ["b01"], events=["甲"]),
              _slot("slot:fact:f05", "f05", [pcr.ACT_CREATE], ["b01"], events=["乙"])]

    def _order_of(_answers):
        _d, _p = pcr.compile_delta(_view, _slots, _answ(_answers))
        assert _p == [], _p
        return ([str(_b["text"]) for _b in _d["upsert"]["beats"]],
                [str(_u) for _u in _d["order"]["beats"]],
                {str(_b["text"]): str(_b["uid"]) for _b in _d["upsert"]["beats"]})

    _texts, _order, _uid_of = _order_of([
        {"slot_id": "slot:fact:f02", "action": pcr.ACT_CREATE, "text": "先",
         "insert_after_uid": "b01", "event_index": 1},
        {"slot_id": "slot:fact:f05", "action": pcr.ACT_CREATE, "text": "后",
         "insert_after_uid": "b01", "event_index": 1}])
    assert sorted(_texts) == ["先", "后"], _texts
    # 两个新画面都紧跟锚点 b01，且**按回答顺序**排列
    assert _order.index("b01") + 1 == _order.index(_uid_of["先"]), _order
    assert _order.index(_uid_of["先"]) + 1 == _order.index(_uid_of["后"]), _order
    # 单变量：把回答换序 ⇒ 编译出的顺序也跟着换
    _t2, _o2, _u2 = _order_of([
        {"slot_id": "slot:fact:f05", "action": pcr.ACT_CREATE, "text": "后",
         "insert_after_uid": "b01", "event_index": 1},
        {"slot_id": "slot:fact:f02", "action": pcr.ACT_CREATE, "text": "先",
         "insert_after_uid": "b01", "event_index": 1}])
    assert _o2.index(_u2["后"]) + 1 == _o2.index(_u2["先"]), _o2
    # 交既有应用器：合成出来的视图里事件顺序不回退
    _d, _ = pcr.compile_delta(_view, _slots, _answ([
        {"slot_id": "slot:fact:f02", "action": pcr.ACT_CREATE, "text": "先",
         "insert_after_uid": "b01", "event_index": 1},
        {"slot_id": "slot:fact:f05", "action": pcr.ACT_CREATE, "text": "后",
         "insert_after_uid": "b01", "event_index": 1}]))
    _cv, _vp, _rk = pc.apply_delta_view(_view, _d, mode=pc.MODE_HYBRID)
    assert _rk["kind"] == "applied", (_rk, _vp)


# ---------------------------------------------------------------------------
# ⑥ 分派状态：**目标身份失效**（零调用）与**能力不足**（保留备用路，一次调用）
# ---------------------------------------------------------------------------
def test_the_dispatch_state_separates_identity_failure_from_capability():
    """四种出口状态**分开**（复核 2026-10-08 P2 第 5 条；`why` 只是文案，分派看 `state`）."""
    _f = _fixture()
    _ok, _why, _ctx = o._content_slots_applicable(
        _f["plan"], [{"code": "presentation_card_text_empty", "kind": "card", "uid": "cZZ"}])
    assert (_ok, _ctx["state"]) == (False, pcr.STATE_UNKNOWN_TARGET), (_ok, _ctx)
    assert "cZZ" in _why, _why
    _ok2, _why2, _ctx2 = o._content_slots_applicable(
        _f["plan"], [{"code": "presentation_beat_events_shape", "uid": "b01"}])
    assert (_ok2, _ctx2["state"]) == (False, pcr.STATE_UNSUPPORTED), (_ok2, _ctx2)
    _ok3, _why3, _ctx3 = o._content_slots_applicable(_f["plan"], _f["problems"])
    assert (_ok3, _ctx3["state"], _why3) == (True, pcr.STATE_OK, "")
    # 混合：身份失效**优先**（先修记录，再谈改内容）
    _ok4, _why4, _ctx4 = o._content_slots_applicable(
        _f["plan"], list(_f["problems"]) + [{"code": "presentation_card_text_empty",
                                             "kind": "card", "uid": "cZZ"}])
    assert (_ok4, _ctx4["state"]) == (False, pcr.STATE_UNKNOWN_TARGET), (_ok4, _ctx4)
    assert _ctx4["blocked"] and _ctx4["blocked"][0]["code"] == "repair_slot_target_unknown"


def test_a_real_controller_stops_on_an_invalid_target_with_zero_calls_and_no_budget(monkeypatch):
    """**真实控制器**：目标身份失效 ⇒ `invalid_target`、零调用、**额度不扣**、基线逐字不变."""
    from step01_script_gen.plan_revision import (STOP_INVALID_TARGET, RevisionBudget,
                                                 RevisionController)
    _f = _fixture()
    _calls = _stub(monkeypatch, _answ([]))
    _bad = [{"code": "presentation_card_text_empty", "kind": "card", "uid": "cZZ",
             "path": "presentation.screen_cards[9].text", "reason": "卡片 cZZ 没有正文"}]
    _budget = RevisionBudget(max_calls=3, deadline_sec=120.0)
    _ctl = RevisionController(
        accept=lambda _plan, _nt="": list(_bad),
        revise=lambda _p, _pr, _b, _rj: o.revise_plan_once(
            _p, _pr, _b, _rj, novel_text=_novel(), verification=_controller()),
        budget=_budget)
    _res = _ctl.run(_f["plan"], _novel())
    assert _res["stop_reason"] == STOP_INVALID_TARGET, _res["stop_reason"]
    assert len(_calls) == 0, "身份失效不许发 POST"
    assert _budget.calls_used == 0, _budget.as_dict()
    assert _res["plan"] == _f["plan"], "基线计划必须逐字保留"
    assert _res["plan_level_conclusion"] is False, "它不是「这份计划不行」的结论"
    assert _res["history"][-1]["no_model_call"] is True


def test_an_unsupported_capability_keeps_using_the_delta_fallback(monkeypatch):
    """**正反对照**：实体是好的、只是槽位表达不了 ⇒ 保留既有的非槽位备用路（额度**照扣**）.

    与 `invalid_target`（零调用、额度退回）成对照：这里每轮**都真的发了 POST**，
    额度管的是"花掉的规划机会"，所以三轮回满就用满。桩回的是一份**声明了 v2 schema、
    但内容不成形**的响应（`view` 缺失）⇒ 每轮如实判"响应违约"并继续，额度照走。
    """
    from step01_script_gen.plan_revision import RevisionBudget, RevisionController
    _f = _fixture()
    _calls = _stub(monkeypatch, {"schema": pc.REVISION_SCHEMA, "rule_version": "x"})
    _probs = [{"code": "presentation_beat_events_shape", "uid": "b01",
               "path": "presentation.beat_events", "reason": "形状违约"}]
    _budget = RevisionBudget(max_calls=3, deadline_sec=120.0)
    _ctl = RevisionController(
        accept=lambda _plan, _nt="": list(_probs),
        revise=lambda _p, _pr, _b, _rj: o.revise_plan_once(
            _p, _pr, _b, _rj, novel_text=_novel(), verification=_controller()),
        budget=_budget)
    _res = _ctl.run(_f["plan"], _novel())
    assert len(_calls) == 3, _calls
    assert _budget.calls_used == 3, _budget.as_dict()
    _ev = _res["history"][0]["evidence"]
    assert _ev["route"] == o.ROUTE_VIEW, _ev.get("route")
    assert _ev["content_slots"]["state"] == pcr.STATE_UNSUPPORTED, _ev["content_slots"]


def test_the_answer_schema_is_derived_from_the_action_field_contract():
    """**同源**：每个动作分支的 required / 禁用键 / 允许槽位都等于 `ACTION_FIELDS` 的声明."""
    _f = _fixture()
    _view, _ = pc.view_from_plan(_f["plan"])
    _cid = str(_view["screen_cards"][0]["card_id"])
    _found = pcr.content_slots(_view, list(_f["problems"]) + [
        {"code": "presentation_card_text_empty", "kind": "card", "uid": _cid}])
    assert not _found["blocked"] and not _found["unexpressible"], _found
    _schema = pcr.answer_json_schema(_found["slots"])["schema"]
    _branches = _schema["properties"]["answers"]["items"]["anyOf"]
    _by_action = {b["properties"]["action"]["enum"][0]: b for b in _branches}
    _need = {a for a in pcr.ACTIONS
             if any(a in _s["allowed_actions"] for _s in _found["slots"])}
    assert set(_by_action) == _need, (sorted(_by_action), sorted(_need))
    assert _need >= {pcr.ACT_CREATE, pcr.ACT_BIND, pcr.ACT_REWRITE, pcr.ACT_NARROW_CARD}
    # 本 fixture 没有当前声音记录 ⇒ 迁移动作不该凭空出现分支；`bind_voice` 可以因为
    # 现有声音正文包含事实主张而合法出现（槽位按真实实体生成）。
    assert pcr.ACT_RECLASSIFY not in _need, sorted(_need)
    for _act, _b in _by_action.items():
        _spec = pcr.ACTION_FIELDS[_act]
        assert _b["required"] == ["slot_id", "action"] + list(_spec["required"]), _b
        assert _b["additionalProperties"] is False, _b
        for _f2 in _spec["forbidden"]:
            assert _f2 not in _b["properties"], (_act, _f2)
        _allow = {str(_s["slot_id"]) for _s in _found["slots"]
                  if _act in _s["allowed_actions"]}
        assert set(_b["properties"]["slot_id"]["enum"]) == _allow, (_act, _b)
    # 空值口径也同源：`bind_visual` 的 `text` 只收空串（本地准入是同一条判据）
    _bind = _by_action[pcr.ACT_BIND]
    assert _bind["properties"]["text"] == {"type": "string", "enum": [""]}, _bind
    assert "text" not in _bind["required"], _bind
    for _blank in ("   ", "顺手改一改"):
        _p = pcr.answer_problems(_answ([
            {"slot_id": "slot:fact:f08", "action": pcr.ACT_BIND, "text": _blank,
             "target_uid": "b07"}]), _found["slots"])
        assert any(str(x["code"]) == "repair_answer_text_unexpected" for x in _p), _p


def test_a_card_target_never_becomes_a_fake_beat_uid():
    """卡片目标**不许**被当成画面 UID 查出一段空正文（旧写法造过 `uid=c01/text=""` 的假节拍）."""
    _f = _fixture()
    _view, _ = pc.view_from_plan(_f["plan"])
    _cid = str(_view["screen_cards"][0]["card_id"])
    _found = pcr.content_slots(_view, [{"code": "presentation_card_text_empty",
                                        "kind": "card", "uid": _cid}])
    assert not _found["blocked"], _found["blocked"]
    _req = pcr.slot_request(_view, _found["slots"])
    _beats = {str(_b["uid"]) for _b in _view["beats"]}
    assert {str(_x["uid"]) for _x in _req["existing_beats"]} <= _beats, _req["existing_beats"]
    assert _cid not in {str(_x["uid"]) for _x in _req["existing_beats"]}
    assert _cid in {str(_x["card_id"]) for _x in _req["existing_cards"]}


def test_a_card_slot_request_carries_the_jointly_bound_voice_body():
    """**联合承载闭包**：卡片槽也要送它共享事实的**对白正文**（只送卡片等于让模型瞎猜）."""
    _f = _fixture()
    _view, _ = pc.view_from_plan(_f["plan"])
    _cid = "c06"                       # 夹具里与台词 d01 联合承载同一条事实（f07）
    _found = pcr.content_slots(_view, [{"code": "presentation_card_text_empty",
                                        "kind": "card", "uid": _cid}])
    assert not _found["blocked"] and not _found["unexpressible"], _found
    assert [str(_x["uid"]) for _x in _found["slots"][0]["facts"]] == ["f07"], _found
    _req = pcr.slot_request(_view, _found["slots"])
    _dlg = next(_u for _u in _view["dialogue_units"] if pc.ref_uid(_u) == "d01")
    _got = [(_x["uid"], _x["text"]) for _x in _req["existing_voices"]]
    assert ("d01", str(_dlg["text"])) in _got, _got


def test_a_dangling_baseline_carrier_is_reported_with_a_path_not_filtered_away():
    """基线里的**悬空载体**：请求带**带路径的记录**（不净化、不拿别种类顶替）."""
    _f = _fixture()
    _view, _ = pc.view_from_plan(_f["plan"])
    _bad = copy.deepcopy(_view)
    _bind = next(_b for _b in _bad["fact_bindings"] if _b["fact_uid"] == "f01")
    _bind["carriers"] = [{"kind": pc.KIND_VISUAL, "beat_uid": "bZZ"},
                         {"kind": pc.KIND_CARD, "card_id": "c01"}]
    _found = pcr.content_slots(_bad, [{"code": "presentation_visual_body_missing",
                                       "uid": "f01"}])
    assert not _found["blocked"], _found["blocked"]
    _req = pcr.slot_request(_bad, _found["slots"])
    _probs = _req["context_problems"]
    assert [str(_x["code"]) for _x in _probs] == ["repair_slot_carrier_missing"], _probs
    assert "bZZ" in str(_probs[0].get("path") or _probs[0].get("uid")), _probs
    _beats = {str(_b["uid"]) for _b in _bad["beats"]}
    assert {str(_x["uid"]) for _x in _req["existing_beats"]} <= _beats
    assert "bZZ" not in {str(_x["uid"]) for _x in _req["existing_beats"]}

def test_the_slot_request_prompt_states_the_exact_answer_envelope():
    """**形状由 prompt 承担**（本通道 `json_schema` 不下发）——真实闭环实测的缺口.

    agent_plan 恒用 `json_object`、`json_schema` 根本不下发（见交付文档 §84 记录），
    因此顶层信封必须在提示里**逐字写出**。真实运行实测：未写时模型回
    `{"type": "json_object", "slot_choices": [...]}` ⇒ `repair_answer_schema_unknown` +
    `repair_answer_unknown_key` 整轮拒收（判据正确，缺口在请求侧）。
    """
    _f = _fixture()
    _v, _err = pc.view_from_plan(_f["plan"])
    assert _v is not None and not _err, _err
    _found = pcr.content_slots(_v, _f["problems"])
    _req = pcr.slot_request(_v, _found["slots"])
    _txt = pcr.slot_prompt(_req, _novel())
    # 顶层信封逐字写出（schema 字面量 + answers 键）
    assert pcr.SLOT_ANSWER_SCHEMA in _txt, "提示里没有写出回答 schema 字面量"
    assert '"answers"' in _txt, "提示里没有写出 answers 顶层键"
    # 并且点名**不要**用的信封形态（真实实测的违约形状）
    assert "slot_choices" in _txt and "type" in _txt, "提示没有点名要避免的信封键"

# ---------------------------------------------------------------------------
# 计划级**聚合**问题 vs 身份写错（2026-10-11 真实生产链路实测）
# ---------------------------------------------------------------------------
_AGG = {"code": "presentation_reading_budget_exceeded", "kind": "plan", "index": None,
        "path": "presentation.screen_cards", "uid": "", "ref": None,
        "reason": "文字卡合计阅读需求超预算"}


def _card_problem(card_id):
    return {"code": "presentation_card_not_in_novel", "kind": "card", "index": None,
            "path": "presentation.screen_cards[%s].text" % card_id, "uid": card_id,
            "ref": None, "reason": "卡片正文不是原文逐字片段"}


def test_a_plan_level_aggregate_problem_does_not_block_the_whole_round():
    """聚合问题**没有单个实体可命名** ⇒ 归 `unexpressible`（走完整计划修订），**不**判身份失效.

    真实生产链路实测：`presentation_reading_budget_exceeded`（`path` 是裸集合、无 uid）
    与 3 条身份齐全的卡片问题同轮 ⇒ 旧写法整轮 `invalid_target`、**零调用**停止，
    3 条本可表达的问题被一条聚合问题一起卡死。
    """
    _f = _fixture()
    _v, _err = pc.view_from_plan(_f["plan"])
    assert _v is not None and not _err, _err
    _cid = str((_v.get("screen_cards") or [])[0].get("card_id"))
    _found = pcr.content_slots(_v, [_card_problem(_cid), dict(_AGG)])
    assert _found["blocked"] == [], _found["blocked"]
    assert any("计划级聚合问题" in x for x in _found["unexpressible"]), _found["unexpressible"]
    # 走真实分派：应为"修法表达不了"（保留备用路），**不是**零调用的身份失效
    _ok, _why, _ctx = o._content_slots_applicable(_f["plan"], [_card_problem(_cid), dict(_AGG)])
    assert _ok is False and _ctx["state"] == pcr.STATE_UNSUPPORTED, _ctx["state"]


def test_a_missing_uid_on_an_entity_path_is_still_an_identity_failure():
    """**判别**：`path` 已指向具体实体却没给 uid ⇒ 仍是生产者契约失效 ⇒ 零调用拒收."""
    _f = _fixture()
    _v, _err = pc.view_from_plan(_f["plan"])
    _cid = str((_v.get("screen_cards") or [])[0].get("card_id"))
    _bad = dict(_AGG, path="presentation.screen_cards[%s].text" % _cid)
    _found = pcr.content_slots(_v, [_bad])
    # ⚠️ 两处列表都记是**既有行为**（原实现同样如此）；判据看的是**路由**用哪一个。
    assert _found["blocked"], _found
    assert any("缺稳定 UID" in x for x in _found["unexpressible"]), _found["unexpressible"]
    assert not any("计划级聚合问题" in x for x in _found["unexpressible"]), _found["unexpressible"]
    _ok, _why, _ctx = o._content_slots_applicable(_f["plan"], [_bad])
    assert _ctx["state"] == pcr.STATE_UNKNOWN_TARGET, _ctx["state"]
