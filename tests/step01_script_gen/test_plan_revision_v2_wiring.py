# -*- coding: utf-8 -*-
"""R 包接线层：`plan_revision.v2` 路由、呈现模式规范化、内容核验挂接（离线，不调模型）.

三段各自证明一件事：

1. **路由**：**新请求统一走 v2**；`ops` / 受限 `full_plan` 只能由
   `with_legacy_request_protocol(...)` 显式进入（冻结响应回放）。预算只改变反馈内容。
2. **模式规范化**：`normalize_hybrid_plan` 把旧形状 + `screen_cards` 变成版本 6 的
   结构化关系（**稳定身份随块保存、事件归属只搬不编、旁白不清空**）。
3. **核验挂接**：内容核验记录**不随投影丢失**——投影只带关系层与 `plan_basis`，
   核验记录必须按候选重新挂上，否则候选会带一条补不掉的假残余。
"""
from __future__ import annotations

import json
import sys

import pytest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import content_verification as cv                               # noqa: E402
from shared import presentation_contract as pc                               # noqa: E402
from step01_script_gen import outline as o                                   # noqa: E402
from tests import verification_helpers as vh                             # noqa: E402

#: 本模块的每一条用例都走**生产路径**（新请求恒为 v2）——不受 conftest 的
#: 旧协议回放开关影响。`@pytest.mark.v2_request` 让 autouse fixture 对本模块让路。
pytestmark = pytest.mark.v2_request

_SRC = ("周明去年递了辞呈。“这里以后就交给林青了。”账目随后由林青接管。"
        "北岸仓库的货位已经清点完毕。")


def _codes(probs):
    return [x.get("code") for x in probs]


def _legacy_plan():
    """**旧模式**源计划：带图外旁白（B3），三条事件/三条节拍逐条覆盖."""
    return {
        # `main_events` 必须 **3–6 条**、`shot_beats` 逐条覆盖（既有结构门）
        "main_events": ["周明辞职", "林青接管", "北岸货位清点"],
        "climax": "林青接管", "causal_chain": ["辞职→接管"],
        "time_budget": "对白约40%",
        "characters": ["周明", "林青"], "emotional_arc": "平→转",
        "end_hook": "交接", "omittable": [], "key_scenes": ["账房"],
        "character_motives": ["林青：接账"],
        "shot_beats": [
            "节拍：周明把辞呈放在桌上｜本镜信息目的：推进｜"
            "原文依据：“周明去年递了辞呈。”｜载体：画面",
            "节拍：林青接过账本｜本镜信息目的：推进｜"
            "原文依据：“这里以后就交给林青了。”｜载体：台词1",
            "节拍：镜头扫过北岸仓库｜本镜信息目的：收束｜"
            "原文依据：“北岸仓库的货位已经清点完毕。”｜载体：旁白1"],
        "dialogue_lines": ["这里以后就交给林青了。"],
        "narration_lines": [{"text": "北岸仓库的货位已经清点完毕。",
                             "source": "北岸仓库的货位已经清点完毕。",
                             "fact_refs": [3]}],
        "required_facts": [
            "周明去年辞职｜原文证据：“周明去年递了辞呈。”｜承载：B1/画面",
            "周明把仓里事务交给林青｜原文证据：“这里以后就交给林青了。”｜承载：B2/台词1",
            "北岸货位已清点｜原文证据：“北岸仓库的货位已经清点完毕。”｜承载：B3/旁白1"],
        "hook_spoken": ["这里以后就交给林青了。"], "hook_carrier": "B2/台词1",
        "screen_cards": [
            {"beat": 1, "template": "character",
             "text": "周明去年递了辞呈。", "placement": "info_panel"}],
    }


def _hybrid_source(declare_events: bool = True):
    """**新模式**源计划：无图外旁白、第三条事实由画面承载（可选地显式声明事件归属）."""
    _p = json.loads(json.dumps(_legacy_plan()))
    _p["narration_lines"] = []
    _p["shot_beats"][2] = ("节拍：镜头扫过北岸仓库｜本镜信息目的：收束｜"
                           "原文依据：“北岸仓库的货位已经清点完毕。”｜载体：画面")
    _p["required_facts"][2] = ("北岸货位已清点｜原文证据：“北岸仓库的货位已经清点完毕。”"
                               "｜承载：B3/画面")
    if declare_events:
        _p["beat_events"] = [[1], [2], [3]]
    return _p


# ---------------------------------------------------------------------------
# ① 路由：新请求统一 v2（形态按 mode），旧协议仅回放
# ---------------------------------------------------------------------------
def test_hybrid_mode_always_routes_to_the_full_view_protocol():
    """hybrid 生产默认 = **完整视图**（2026-10-11 隔离对照的裁决）.

    与问题清单**无关**：三种问题（含纯预算）都走 `ROUTE_VIEW`。判据是协议级别的——
    隔离生产链上同一模型走增量 5/5 被拒、走完整视图 0 拒收。
    """
    for _probs in ([{"code": "carrier_unlabeled"}], [{"code": "budget_infeasible"}],
                   [{"code": "presentation_carrier_dangling"}], None):
        assert o._replan_route(_probs, mode="screen_hybrid") == o.ROUTE_VIEW, _probs


def test_legacy_mode_keeps_the_delta_protocol():
    """legacy **没有**同条件的对照读数 ⇒ 仍走增量（没有证据就不动生产默认）."""
    assert o._replan_route([{"code": "carrier_unlabeled"}]) == o.ROUTE_VIEW_DELTA
    assert o._replan_route([{"code": "presentation_carrier_dangling"}],
                           mode=pc.MODE_LEGACY) == o.ROUTE_VIEW_DELTA


def test_new_requests_always_go_to_v2():
    """**新请求统一 v2**（工作包 1 第 3 条）——不再按问题清单挑旧协议.

    v2 的**形态**由 mode 定（hybrid⇒完整视图 / legacy⇒增量），但**都不再**是
    ops / 受限 full_plan：那两条只剩显式回放一条入口。
    """
    _v2 = (o.ROUTE_VIEW, o.ROUTE_VIEW_DELTA)
    for _probs in ([{"code": "carrier_unlabeled"}], [{"code": "budget_infeasible"}],
                   [{"code": "presentation_carrier_dangling"}], None):
        assert o._replan_route(_probs) in _v2, _probs
        assert o._replan_route(_probs, mode=pc.MODE_HYBRID) == o.ROUTE_VIEW, _probs
        assert o._replan_route(_probs, mode=pc.MODE_LEGACY) == o.ROUTE_VIEW_DELTA, _probs
        for _r in (o.ROUTE_OPS, o.ROUTE_FULL_PLAN):
            assert o._replan_route(_probs) != _r, (_probs, _r)


def test_old_protocols_only_via_the_explicit_replay_switch():
    """旧 ops/full **只能**由 `with_legacy_request_protocol` 显式进入（冻结响应回放）."""
    with o.with_legacy_request_protocol(o.ROUTE_OPS):
        assert o._replan_route([{"code": "carrier_unlabeled"}]) == o.ROUTE_OPS
    with o.with_legacy_request_protocol(o.ROUTE_FULL_PLAN):
        assert o._replan_route([{"code": "budget_infeasible"}]) == o.ROUTE_FULL_PLAN
    # 出了上下文就恢复 v2（开关不许泄漏）
    assert o._replan_route([{"code": "carrier_unlabeled"}]) == o.ROUTE_VIEW_DELTA
    # 回放入口**不发模型调用**，只把历史响应交给对应真实应用器
    import inspect
    assert "response" in inspect.signature(o._replan_by_ops).parameters
    assert "response" in inspect.signature(o.revise_plan_full).parameters


def test_undeclared_codes_still_fail_closed():
    """表外 code 仍然**不许**当"可修"——`view` 动作不放松这条 fail-closed."""
    assert o._fix_capability([{"code": "某个没人登记的 code"}])["undeclared"]
    assert not o._fix_capability([{"code": "某个没人登记的 code"}])["expressible"]
    assert "某个没人登记的 code" not in o.PLAN_FIX_CAPABILITY


def test_schema_and_instruction_are_bound_to_the_route():
    """schema 与规则块**消费同一个裁决值**（复核之十二 P1 的形态）."""
    assert o.REPLAN_VIEW_SCHEMA["schema"]["properties"]["schema"]["const"] \
        == pc.REVISION_SCHEMA
    assert "plan_revision.v2" in o._PLAN_VIEW_INSTRUCTION
    # 旧两路**没被动过**：它们的 schema 与规则块仍在
    assert o.OUTLINE_SCHEMA["name"] == "chapter_outline"
    assert "screen_cards" not in o.OUTLINE_SCHEMA["schema"]["properties"]


# ---------------------------------------------------------------------------
# ② 模式规范化
# ---------------------------------------------------------------------------
def test_normalize_hybrid_moves_content_into_the_structured_block():
    _p, _probs = o.normalize_hybrid_plan(_hybrid_source())
    assert _probs == [], _probs
    assert _p["plan_contract_version"] == pc.PLAN_CONTRACT_VERSION_HYBRID
    assert "screen_cards" not in _p, "内容搬进 presentation 后不许留第二份"
    _pres = _p["presentation"]
    assert _pres["schema"] == pc.PRESENTATION_SCHEMA
    assert _pres["mode"] == pc.MODE_HYBRID
    # **稳定身份随块保存**（投影读回时不重新编号）
    assert _pres["fact_uids"] == ["f01", "f02", "f03"]
    assert _pres["beat_uids"] == ["b01", "b02", "b03"]
    assert _pres["dialogue_uids"] == ["d01"] and _pres["narration_uids"] == []
    # 卡片接进**联合承载**：F1 由 B1 画面 + C1 卡片一起承载
    _f1 = _pres["fact_bindings"][0]
    assert _f1["fact_uid"] == "f01"
    assert [x["kind"] for x in _f1["carriers"]] == ["visual", "screen_card"]
    assert _pres["screen_cards"][0]["beat_uid"] == "b01"
    # 其余计划字段**原样保留**
    for _k in ("climax", "causal_chain", "time_budget", "characters", "end_hook"):
        assert _p[_k] == _legacy_plan()[_k], _k


def test_normalize_hybrid_does_not_fabricate_event_ownership():
    """**事件归属只搬不编**——没给 `beat_events` 就标「未确定」，交验收链报出来."""
    _p, _ = o.normalize_hybrid_plan(_hybrid_source(declare_events=False))
    _v, _vp = pc.view_from_plan(_p)
    assert _v["event_binding"] == "unknown"
    assert all(b["event_uids"] == [] for b in _v["beats"]), "不许按位置编事件归属"


def test_normalize_hybrid_carries_declared_event_ownership():
    _plan = _hybrid_source(declare_events=True)
    _p, _ = o.normalize_hybrid_plan(_plan)
    _v, _vp = pc.view_from_plan(_p)
    assert _v["event_binding"] == "declared"
    assert [b["event_uids"] for b in _v["beats"]] == [
        ["周明辞职"], ["林青接管"], ["北岸货位清点"]]


def test_normalize_hybrid_does_not_silently_drop_narration():
    """新模式用了旁白 ⇒ 规范化**不清空**，让模式合同把它报出来."""
    # 用**带旁白**的源：新模式用了旁白，规范化**不许**悄悄清空（由模式合同报出来）
    _p, _ = o.normalize_hybrid_plan(_legacy_plan())
    assert _p["narration_lines"], "规范化把模型给的旁白清掉了——那是丢内容"
    _v, _vp = pc.view_from_plan(_p)
    assert _v is None or "view_hybrid_narration_forbidden" in _codes(_vp), _codes(_vp)


def test_normalize_hybrid_rejects_a_card_on_a_nonexistent_beat():
    _plan = _legacy_plan()
    _plan["screen_cards"][0]["beat"] = 9
    _p, _probs = o.normalize_hybrid_plan(_plan)
    assert _p is None and _probs[0]["code"] == "presentation_card_beat_missing"


def test_hybrid_normalization_survives_a_full_projection_round_trip():
    # **事件归属必须显式声明**（新模式的硬要求：不按位置编）
    _src = _hybrid_source(declare_events=True)
    _p, _ = o.normalize_hybrid_plan(_src)
    _v, _vp = pc.view_from_plan(_p)
    assert _vp == [], _codes(_vp)
    _back = pc.plan_from_view(_v)
    _v2, _vp2 = pc.view_from_plan(_back)
    assert _vp2 == [], _codes(_vp2)
    assert [f["uid"] for f in _v2["frozen_facts"]] == ["f01", "f02", "f03"]
    assert [u["uid"] for u in _v2["dialogue_units"]] == ["d01"]


def test_legacy_mode_normalization_is_a_no_op():
    _p = _legacy_plan()
    assert o.normalize_plan_for_mode(_p, mode=pc.MODE_LEGACY)[0] is _p


# ---------------------------------------------------------------------------
# ③ 模式与版本**单源**（工作包 1 第 5 条）
# ---------------------------------------------------------------------------
def test_validate_outline_does_not_rewrite_hybrid_to_version_5():
    """复核 P1：hybrid 计划过了净化/锚点转换后被盖成版本 5 ⇒ 缓存写读不一致."""
    # **先规范化成 v6**（带 `presentation` 块）才是 hybrid 计划；没有该块的
    # 就是 legacy，按 legacy 给版本 5 —— 那是正确行为，不是这条要钉的东西
    _plan, _ = o.normalize_hybrid_plan(_hybrid_source())
    _out = o._validate_outline(json.loads(json.dumps(_plan)), _SRC)
    assert _out is not None, "合法的 hybrid 计划被净化事务拒收了"
    assert _out["plan_contract_version"] == pc.PLAN_CONTRACT_VERSION_HYBRID, \
        "净化事务把 hybrid 写成了版本 %r" % _out.get("plan_contract_version")
    assert _out["presentation"]["mode"] == pc.MODE_HYBRID


def test_legacy_plan_still_gets_version_5():
    _plan = _legacy_plan()
    _out = o._validate_outline(json.loads(json.dumps(_plan)), _SRC)
    assert _out is not None
    assert _out["plan_contract_version"] == pc.PLAN_CONTRACT_VERSION_LEGACY


def test_plan_mode_and_contract_version_agree():
    """`presentation.mode` 与 `plan_contract_version` 是**同一件事的两种表述**."""
    _legacy_out = o._validate_outline(json.loads(json.dumps(_legacy_plan())), _SRC)
    assert _legacy_out["plan_contract_version"] == \
        pc.PLAN_CONTRACT_VERSION_BY_MODE[pc.plan_mode(_legacy_out)]
    _hyb, _ = o.normalize_hybrid_plan(_hybrid_source(declare_events=True))
    assert _hyb["plan_contract_version"] == \
        pc.PLAN_CONTRACT_VERSION_BY_MODE[pc.plan_mode(_hyb)]


# ---------------------------------------------------------------------------
# ④ 核验挂接：投影**不带走**核验记录
# ---------------------------------------------------------------------------
def test_projection_drops_the_verification_record_by_design():
    """核验记录是**对这份投影**的结果，不是计划字段——投影后必须重新挂."""
    _p, _ = o.normalize_hybrid_plan(_hybrid_source())
    _v, _ = pc.view_from_plan(_p)
    _p2 = pc.plan_from_view(_v)
    assert "plan_verification" not in _p2, \
        "核验记录混进了 plan_basis——投影会把**过期**的那一份带到新计划上"


def _fake_controller(view):
    """给一份**自洽**的核验记录（三条轴齐全；支持片段逐字出自实际载体正文）."""
    _plan = pc.plan_from_view(view)

    def _v(_view, digest):
        _out = {"schema": cv.SCHEMA_VERIFICATION, "status": "ok",
                "rule_version": cv.DEFAULT_RULE_VERSION, "plan_digest": digest,
                "novel_digest": cv.plan_digest_of(_plan),
                "source_support": [], "facts": [], "voice_units": []}
        vh.stamp_basis(_out, _view)
        for _f in _view["frozen_facts"]:
            _ev = str(_f.get("evidence") or "")
            _out["source_support"].append({
                "fact_uid": _f["uid"], "verdict": cv.VERDICT_SUPPORTED,
                "evidence_spans": [_ev] if _ev else [], "reason": ""})
            _txts = [t for t in cv.all_carrier_texts(_view, _f["uid"]) if t]
            _out["facts"].append({
                "fact_uid": _f["uid"], "verdict": cv.VERDICT_SUPPORTED,
                "support_spans": [max(_txts, key=len)] if _txts else [],
                "missing": "", "suggested_carriers": [],
                "obligations": vh.obligations_for_result(
                    _view, _f["uid"], cv.VERDICT_SUPPORTED, _txts)})
        for _u in _view["dialogue_units"] + _view["narration_units"]:
            _out["voice_units"].append({
                "unit_uid": pc.ref_uid(_u), "verdict": cv.VERDICT_SUPPORTED,
                "source_kind": "novel_speech", "speaker": "周明",
                "echo_text": str(_u.get("text") or ""), "reason": ""})
        return _out

    return cv.VerificationController(verify=_v)


def test_candidate_acceptance_reattaches_verification():
    """`complete_candidate` 挂上核验记录 ⇒ 不再有那条假残余."""
    _src = _hybrid_source(declare_events=True)
    _p, _ = o.normalize_hybrid_plan(_src)
    _v, _vp = pc.view_from_plan(_p)
    assert _vp == [], _codes(_vp)
    _probs = o.plan_acceptance_problems(pc.plan_from_view(_v), _SRC)
    assert any(str(x.get("code")) == "verify_record_missing" for x in _probs), \
        "缺核验记录不得判通过"
    _plan2, _probs2 = o.complete_candidate(_v, _SRC, _fake_controller(_v))
    assert not any(str(x.get("code")) == "verify_record_missing" for x in _probs2), \
        "挂上核验记录后不该再有那条假残余"
    # **被验收的对象就是被返回的对象**（工作包 1 第 1 条）
    assert _plan2.get("plan_verification"), "返回的候选没有核验记录"


def test_returned_candidate_is_exactly_what_was_accepted():
    """复核 P1 的复现用例：控制器判 ok、返回候选没记录、最终真实验收判缺记录.

    这里逐字断言 **`complete_candidate` 返回的那个对象**就是挂过记录、被验收过的那个
    ——不是"再投影一次得到的那份"。
    """
    _src = _hybrid_source(declare_events=True)
    _p, _ = o.normalize_hybrid_plan(_src)
    _v, _ = pc.view_from_plan(_p)
    _cand, _probs = o.complete_candidate(_v, _SRC, _fake_controller(_v))
    assert _cand.get("plan_verification", {}).get("status") == "ok"
    _again = o.plan_acceptance_problems(_cand, _SRC)
    assert not any(str(x.get("code")) == "verify_record_missing" for x in _again), \
        _codes(_again)


def test_structurally_invalid_candidate_does_not_call_verification():
    """结构不合格的候选**不发起核验**（工作包 2 第 5 条）——一次真实调用不能浪费."""
    from step01_script_gen.content_verify import structurally_valid
    _bad = {"shot_beats": "不是数组", "dialogue_lines": [], "narration_lines": [],
            "required_facts": [], "main_events": [], "climax": "", "causal_chain": [],
            "hook_spoken": [], "hook_carrier": ""}
    assert not structurally_valid(_bad)
    _ok, _ = o.normalize_hybrid_plan(_hybrid_source())
    assert structurally_valid(_ok)


# ---------------------------------------------------------------------------
# ⑤ 预算只是反馈，不是接口
# ---------------------------------------------------------------------------
def test_budget_only_problems_do_not_switch_protocol():
    """**预算只是反馈，不是接口**（工作包 1 第 3 条）——纯预算问题不该走另一套协议."""
    _route = o._replan_route([{"code": "budget_infeasible"}], mode=pc.MODE_HYBRID)
    assert _route == o.ROUTE_VIEW, "hybrid 的纯预算问题被分派到了旧预算协议"
    # 每条 route 都必须有一份「本轮能改什么」的文案（否则 `_INTERFACE_ADVICE[_route]` KeyError）
    for _r in (o.ROUTE_OPS, o.ROUTE_FULL_PLAN, o.ROUTE_VIEW, o.ROUTE_VIEW_DELTA):
        assert _r in o._INTERFACE_ADVICE, "route %r 缺接口文案" % _r
        assert set(o._INTERFACE_ADVICE[_r]) >= {"reorder_rule", "beat_edit_rule",
                                                "carrier_escape", "baseline_note",
                                                "cannot_do", "plan_fields_note"}


# ---------------------------------------------------------------------------
# ⑥ hybrid 零对白合法解（工作包 2 第 6 条）
# ---------------------------------------------------------------------------
def test_hybrid_zero_dialogue_is_legal_for_feasibility():
    """**无对白、靠画面＋卡片完整表达**的 hybrid 计划合法——旧模式的门不适用."""
    from step01_script_gen.plan_feasibility import evaluate_plan
    _p = {
        "main_events": ["交接"], "climax": "交接", "causal_chain": ["交接"],
        "time_budget": "画面为主",
        "shot_beats": ["节拍：林青接过账本｜本镜信息目的：推进｜"
                       "原文依据：“这里以后就交给林青了。”｜载体：画面"],
        "dialogue_lines": [], "narration_lines": [],
        "required_facts": ["交接｜原文证据：“这里以后就交给林青了。”｜承载：B1/画面"],
        "hook_spoken": [], "hook_carrier": "",
        "plan_contract_version": pc.PLAN_CONTRACT_VERSION_HYBRID,
        "presentation": {
            "schema": pc.PRESENTATION_SCHEMA, "mode": pc.MODE_HYBRID,
            "fact_uids": ["f01"], "beat_uids": ["b01"], "dialogue_uids": [],
            "narration_uids": [], "beat_events": [["交接"]],
            "screen_cards": [{"card_id": "c01", "beat_uid": "b01",
                              "template": "character",
                              "text": "这里以后就交给林青了。",
                              "placement": "info_panel"}],
            "fact_bindings": [{"fact_uid": "f01",
                               "carriers": [{"kind": "visual", "beat_uid": "b01"},
                                            {"kind": "screen_card",
                                             "card_id": "c01"}]}],
            "hook_bindings": {"spoken_uids": [], "carriers": []}},
    }
    _rep = evaluate_plan(_p, novel_text=_SRC)
    assert _rep.get("verdict") != "unevaluable", _rep.get("reasons")
    assert not any(str(x.get("code")) == "plan_no_dialogue_lines"
                   for x in _rep.get("problems") or []), \
        _codes(_rep.get("problems") or [])


def test_legacy_zero_dialogue_is_still_rejected():
    """**不全局放宽旧模式**：legacy 的零对白仍然被拒."""
    from step01_script_gen.plan_feasibility import evaluate_plan
    _p = {"main_events": ["甲"], "climax": "甲", "causal_chain": [],
          "time_budget": "x", "shot_beats": [
              "节拍：甲放辞呈｜本镜信息目的：推进｜原文依据：“甲辞呈。”｜载体：画面"],
          "dialogue_lines": [], "narration_lines": [], "required_facts": [],
          "hook_spoken": [], "hook_carrier": ""}
    _rep = evaluate_plan(_p, novel_text=_SRC)
    assert any(str(x.get("code")) == "plan_no_dialogue_lines"
               for x in _rep.get("problems") or []), \
        _codes(_rep.get("problems") or [])


# ---------------------------------------------------------------------------
# ⑦ v2 请求**带着视图本体**
# ---------------------------------------------------------------------------
def test_v2_request_carries_the_view_itself():
    """协议说"回一份改完的整份视图"——模型没看见当前视图时做不到修订，只会重写."""
    import inspect
    _src = inspect.getsource(o._replan_by_view)
    assert '"view": _req["view"]' in _src, "v2 请求没有把视图本体发下去"
    assert '_req["base_plan_hash"]' in _src, "基线摘要必须原样回带"


def test_v2_schema_forbids_self_verdict_and_budget_widening():
    """响应不许自报通过/放宽预算——`additionalProperties: False` 是它的第一道."""
    _p = o.REPLAN_VIEW_SCHEMA["schema"]
    assert _p["additionalProperties"] is False
    for _forbidden in ("passed", "executable", "budget", "time_budget", "accepted"):
        assert _forbidden not in _p["properties"], _forbidden
    assert set(_p["required"]) == {"schema", "rule_version", "base_plan_hash",
                                   "fact_basis_hash", "view"}


def test_controller_fingerprint_sees_the_presentation_block():
    """循环指纹**必须**包含 `presentation`——否则"只改了一张卡"会被记成 no_change."""
    from step01_script_gen.plan_revision import revision_fingerprint
    _a = {"main_events": ["甲"], "shot_beats": ["b"], "dialogue_lines": [],
          "narration_lines": [], "required_facts": [], "hook_carrier": "",
          "hook_spoken": [], "climax": "甲", "causal_chain": [],
          "presentation": {"screen_cards": []}}
    _b = json.loads(json.dumps(_a))
    _b["presentation"]["screen_cards"] = [{"card_id": "c01", "text": "甲"}]
    assert revision_fingerprint(_a) != revision_fingerprint(_b), \
        "只改卡片在指纹里看不出来 ⇒ 控制器会把有效修订记成 no_change"
    # 诊断元数据仍然排除（它们的变化不是内容变化）
    _c = json.loads(json.dumps(_a))
    _c["plan_revision"] = {"x": 1}
    _c["plan_verification"] = {"y": 2}
    assert revision_fingerprint(_c) == revision_fingerprint(_a)


# ---------------------------------------------------------------------------
# ⑦' v2 prompt **不许混两套形状**（实跑 2026-10-06 根因）
# ---------------------------------------------------------------------------
def test_v2_prompt_carries_only_the_v2_vocabulary():
    """**一次实跑取证**：v2 修订的 prompt 里同时出现 v5 与 v2 两套词汇，
    模型于是自创第三种形状——三轮响应分别回成 `"beats":[0,1,2,...]`（下标占位）、
    `"frozen_facts":[0,...]`、以及 **v2 信封 + v5 计划体**（`required_facts` /
    `narration_lines` / `screen_cards`）的混合体，全部被拒收、额度耗尽。

    钉住：v2 prompt 只给 v2 词汇，并且**显式禁止**用下标/占位符代替对象。
    """
    _rule = o.render_view_protected_rule()
    # ① v2 词汇必须在
    for _k in ("frozen_facts", "frozen_events", "plan_basis", "evidence_updates"):
        assert _k in _rule, "v2 保护规则缺 %s" % _k
    # ② **v5 词汇一律不许出现**（它们不是视图字段，出现就是第二套形状）
    for _bad in ("`required_facts`", "`shot_beats`", "｜承载：", "｜载体："):
        assert _bad not in _rule, "v2 保护规则混进了 v5 词汇：%s" % _bad
    # ③ 指令里必须有**响应骨架**与**禁止缩写**
    _ins = o._PLAN_VIEW_INSTRUCTION
    for _k in ('"schema": "plan_revision.v2"', '"view": {', '"beats": [{"uid"',
               '"fact_bindings": [{"fact_uid"'):
        assert _k in _ins, "响应骨架缺 %s" % _k
    # 措辞可改，但"数组逐项写全 + 明确禁止放数字 + 明确列出不可接受的写法"必须在。
    # 复核 2026-10-06 更正③：原措辞说"每一个数组的每一项都必须是**带全部字段的对象**"，
    # 而契约里 `frozen_events` / `plan_basis.characters` 等**是字符串数组**——请求说明与
    # 真实解析器冲突。现在按两类数组分别写，这里同时钉住两半，防退回"一刀切说成对象"。
    assert "完整对象" in _ins, "没有要求对象数组逐项写完整对象"
    assert "完整字符串" in _ins, "没有说明字符串数组同样要原样写全（契约里它们就是字符串数组）"
    assert "不允许出现数字" in _ins, "没有明确禁止数组里放数字"
    assert "不可接受" in _ins and "自造键名" in _ins, "没有列出不可接受的写法"
    # ④ 生成端**真的用了** v2 那一份（不是只看函数存在）
    import inspect
    _src = inspect.getsource(o._replan_by_view)
    # **只看非注释行**：注释里正是用"拼 v5 的 render_protected_fields_rule()"这句话
    # 说明为什么不能那么做——按整段源码搜会把说明本身当成违规
    _code = [ln for ln in _src.splitlines() if not ln.lstrip().startswith("#")]
    _joined = chr(10).join(_code)
    assert "render_view_protected_rule()" in _joined, "v2 修订没拼 v2 保护规则"
    assert "render_protected_fields_rule()" not in _joined,         "v2 修订仍在拼 v5 的保护规则 ⇒ 两套形状冲突"


def test_v2_instruction_never_shows_a_wrong_form_literally():
    """**提示词里不许出现「数组装数字」的字面示例**（实跑 2026-10-06 自己埋的坑）.

    我为"禁止下标占位"写了一条反面例子，里面**照抄了** `"beats": [0, 1, 2, 3]` 与
    `"frozen_facts": [0, 1, 2]`。重跑两次，模型回的**正是这两个字段**的下标数组
    （`"beats": [0..21]`、`"frozen_facts": [0..9]`）——小模型抓住了具体 JSON 例子、
    忽略了旁边的 ✗ 标记。

    教训：**负例要描述，不要给示例**。这条用例扫指令文本，出现「数组里只有数字」的
    字面 JSON 就转红。
    """
    import re as _re
    for _txt, _who in ((o._PLAN_VIEW_INSTRUCTION, "v2 指令"),
                       (o.render_view_protected_rule(), "v2 保护规则")):
        _hit = _re.findall(r"\[[0-9][0-9,\s]*\]", _txt)
        assert not _hit, "%s 里出现了「数组装数字」的字面示例：%s" % (_who, _hit)


def test_v5_protected_rule_still_serves_the_legacy_protocol():
    """旧协议**仍用 v5 词汇**那一份——两套形状各自服务各自的协议，不是删掉一份."""
    _legacy = o.render_protected_fields_rule()
    assert "`shot_beats`" in _legacy or "`required_facts`" in _legacy, _legacy
    assert _legacy != o.render_view_protected_rule()


# ---------------------------------------------------------------------------
# ⑧ 早期 CLI（缺省仍是旧模式）
# ---------------------------------------------------------------------------
def test_presentation_mode_cli_defaults_to_legacy():
    from step01_script_gen.main import _presentation_mode_argv
    assert _presentation_mode_argv(["--shots", "2"]) == ""
    assert o.presentation_mode("") == pc.MODE_LEGACY
    assert o.presentation_mode("screen_hybrid") == pc.MODE_HYBRID


def test_illegal_presentation_mode_is_rejected_at_startup():
    import pytest
    from step01_script_gen.main import _presentation_mode_argv
    # `_presentation_mode_argv` 只**解析**（缺值是它的错）；取值合法性由
    # `presentation_mode` 判——`main()` 正是 `presentation_mode(_presentation_mode_argv())`
    with pytest.raises(ValueError):
        _presentation_mode_argv(["--presentation-mode"])
    for _bad in ("瞎写", "LEGACY_AUDIO", "1"):
        with pytest.raises(ValueError):
            o.presentation_mode(_bad)
    # 合法对照：**空串是"未指定"⇒ 默认旧模式**（不是非法值）
    assert o.presentation_mode("") == pc.MODE_LEGACY
    assert o.presentation_mode(
        _presentation_mode_argv(["--presentation-mode", "screen_hybrid"])) \
        == pc.MODE_HYBRID


def test_mode_enters_the_prompt_and_schema_only_for_hybrid():
    _legacy = o.outline_prompt_for_mode(pc.MODE_LEGACY, novel_text="原文",
                                        beat_edit_rule="（首版）")
    _hybrid = o.outline_prompt_for_mode(pc.MODE_HYBRID, novel_text="原文",
                                        beat_edit_rule="（首版）")
    assert "screen_cards" not in _legacy
    assert "screen_cards" in _hybrid
    assert "beat_events" in _hybrid
    assert o.outline_schema_for_mode(pc.MODE_LEGACY) is o.OUTLINE_SCHEMA
    _hs = o.outline_schema_for_mode(pc.MODE_HYBRID)
    assert _hs is not o.OUTLINE_SCHEMA
    assert {"screen_cards", "beat_events"} <= set(_hs["schema"]["required"])
    # 旧模式的 schema **逐字不变**（换模式不动旧稿的缓存身份）
    assert "screen_cards" not in o.OUTLINE_SCHEMA["schema"]["properties"]

def test_the_v2_request_does_not_override_the_configured_output_cap():
    """实测 2026-10-06（探针 4 条独立样本）：v2 响应要回带**整份**可编辑视图，
    实测 8.5k~23.4k 字符，而调用点原先硬写死 `max_tokens=8000` —— 模型真去干活的
    那几次全部 `finish_reason=length` 被**截断**，接回来就是非法 JSON，必须重问。
    该值本质是「延迟 vs 输出长度」的权衡参数，按项目约定归 `.env`
    （`AGENT_PLAN_TEXT_MAX_TOKENS`，provider 已明确删过同类的硬编码兜底）；
    调用点再写一个数字，等于把那条约定又推翻一次。
    """
    from step01_script_gen.plan_revision import RevisionBudget
    _seen = {}

    class _Svc:
        def generate(self, req, *a, **k):
            _seen["req"] = req
            raise RuntimeError("stop-after-capture")

    _orig = o.create_service
    o.create_service = lambda *a, **k: _Svc()
    try:
        o._replan_by_view(
            _legacy_plan(),
            [{"code": "carrier_unlabeled", "kind": "beat", "index": 1,
              "path": "shot_beats[0]", "reason": "节拍没标载体"}],
            _SRC, RevisionBudget(max_calls=3))
    finally:
        o.create_service = _orig
    _req = _seen.get("req")
    assert _req is not None, "请求根本没构造出来（前置门把它挡了）"
    assert _req.max_tokens is None, (
        "调用点又写死了一个输出上限 %r —— 整份视图会被截断" % (_req.max_tokens,))


def test_the_instruction_does_not_claim_every_array_holds_objects():
    """复核 2026-10-06 更正③：请求说明不得与契约冲突.

    契约里 `frozen_events`、`plan_basis.characters` / `key_scenes` / `causal_chain` /
    `character_motives` / `omittable` **都是字符串数组**（真实解析器接受字符串）。
    指令若笼统写"每一个数组的每一项都必须是对象"，模型要么照做（把字符串包成对象、
    被解析器拒收），要么认定指令不可信——两种都是自伤。
    """
    _ins = o._PLAN_VIEW_INSTRUCTION
    _arr = ("frozen_events", "characters", "key_scenes", "causal_chain",
            "character_motives", "omittable")
    for _k in _arr:
        assert _k in _ins, "字符串数组 %s 没在指令里说明，模型无从知道它不是对象数组" % _k
    # 反例：不能出现"每一个数组…都必须是对象"这种一刀切
    assert "每一个数组的每一项，都必须是**带全部字段的对象" not in _ins,         "又回到了与契约冲突的一刀切说法"


def test_production_dispatches_by_mode_and_replays_the_older_one_explicitly():
    """复核 2026-10-06（第八轮）③：**生产入口明确选协议，不按响应形状猜**.

    - 新请求一律 v2，**形态按 mode**：hybrid ⇒ `ROUTE_VIEW`（完整视图替换）；
      legacy ⇒ `ROUTE_VIEW_DELTA`（增量）；
    - 两条路由各有自己的 schema / 说明块 / 规则版本，且都被 `_replan_by_view` 真正消费
      （复核之十二的教训：判得对 ≠ 请求消费了它）。
    """
    import inspect
    assert o._replan_route([{"code": "carrier_unlabeled"}]) == o.ROUTE_VIEW_DELTA
    assert o._replan_route([{"code": "carrier_unlabeled"}],
                           mode=pc.MODE_HYBRID) == o.ROUTE_VIEW
    # 显式开关仍然优先于 mode（冻结响应回放要能固定任一条）
    with o.with_legacy_request_protocol(o.ROUTE_VIEW):
        assert o._replan_route([{"code": "carrier_unlabeled"}],
                               mode=pc.MODE_LEGACY) == o.ROUTE_VIEW
    with o.with_legacy_request_protocol(o.ROUTE_VIEW_DELTA):
        assert o._replan_route([{"code": "carrier_unlabeled"}],
                               mode=pc.MODE_HYBRID) == o.ROUTE_VIEW_DELTA

    # 两条协议各有独立的 schema 与规则版本
    assert o.REPLAN_DELTA_SCHEMA["schema"]["properties"]["schema"]["const"] \
        == pc.DELTA_SCHEMA
    assert o.PLAN_DELTA_RULE_VERSION == pc.DELTA_RULE_VERSION
    assert o.PLAN_DELTA_RULE_VERSION != o.PLAN_VIEW_RULE_VERSION
    # 增量说明块里**不许**出现完整视图的响应骨架（那正是被当模板照抄的东西）
    assert "plan_revision.delta.v1" in o._PLAN_DELTA_INSTRUCTION
    assert '"view": {' not in o._PLAN_DELTA_INSTRUCTION
    # 编排**真的**按协议分叉（不是"定义了常量没人用"）
    _src = [ln for ln in inspect.getsource(o._replan_by_view).splitlines()
            if not ln.lstrip().startswith("#")]
    _joined = chr(10).join(_src)
    # 分派形态（复核 2026-10-06 第十一轮后改成显式 if/else：两条分支的入口参数不同
    # ——完整视图那条要传 `expected_rule_version`，三元表达式塞不下）
    assert "apply_delta_view(_base_view, _data" in _joined
    assert "apply_editable_view(" in _joined
    assert "expected_rule_version=_rule" in _joined
    assert "delta_request if _is_delta" in _joined
    assert "replan_delta_schema_for_request(_req)" in _joined
    assert "_PLAN_DELTA_INSTRUCTION if _is_delta" in _joined
    # 预算短路不许绕过新路由（否则 `_INTERFACE_ADVICE[_route]` KeyError 的老毛病复发）——
    # 这一句在**分派函数**里，不在应用器里
    _disp = [ln for ln in inspect.getsource(o.revise_plan_once).splitlines()
             if not ln.lstrip().startswith("#")]
    # 2026-10 裁决 (a)：条件从 `_route not in (ROUTE_VIEW, ROUTE_VIEW_DELTA)` 改为按
    # 「非完整视图替换 + 非 hybrid」判定——原写法在 legacy 生产路由（恒为
    # ROUTE_VIEW_DELTA）下**永远为 False**，纯预算轮再也进不来（拿不到数值验算表、
    # 不写 `plan_replan`）。此处钉**新**契约，并保留「预算分支必须把它自己支持的
    # 旧协议传下去」这一合法对照——那才是不让 `_INTERFACE_ADVICE[_route]` KeyError
    # 复发的关键句。
    _disp_txt = chr(10).join(_disp)
    assert "_route != ROUTE_VIEW and plan_mode(plan) != MODE_HYBRID" in _disp_txt
    assert "_budget_route = _legacy_route(problems)" in _disp_txt
    assert "route=_budget_route" in _disp_txt, "预算分支没把协议传下去"
    assert "protocol=_route" in _disp_txt


def test_the_delta_schema_inner_types_match_the_local_entity_contract():
    """D：schema 要覆盖**内部类型与必填关系**，且与本地契约**同源**.

    两处不一致 = 服务端与本地各说一套形状；`shared.ENTITY_CONTRACT` 是本地准入的
    唯一判据，schema 必须逐条对上（字段集与身份键都要一致）。
    """
    _props = o.REPLAN_DELTA_SCHEMA["schema"]["properties"]
    assert o.REPLAN_DELTA_SCHEMA["schema"]["additionalProperties"] is False
    _up = _props["upsert"]["properties"]
    for _key, (_idk, _fields) in pc.ENTITY_CONTRACT.items():
        _item = _up[_key]["items"]
        assert set(_item["required"]) - {_idk} == set(_fields), (_key, _item["required"])
        assert _idk in _item["properties"], (_key, _idk)
    # 身份键按集合走：卡片是 `card_id`，其余是 `uid`（复核第六轮 P1①）
    assert "card_id" in _up["screen_cards"]["items"]["required"]
    assert "uid" in _up["beats"]["items"]["required"]
    # 关系块也写了内部类型，不只是信封
    assert _props["fact_bindings"]["items"]["required"] == ["fact_uid", "carriers"]
    assert _props["hook_bindings"]["required"] == ["spoken_uids", "carriers"]
    assert set(_props["delete"]["properties"]) == set(pc.DELTA_ENTITY_KEYS)
    assert set(_props["delta"]["properties"]) == set(pc.DELTA_ENTITY_KEYS)
    assert set(_props["order"]["properties"]) == set(pc.DELTA_ENTITY_KEYS)


# ---------------------------------------------------------------------------
# 生产路由回归（2026-10 裁决 (a)）——标 v2_request 以**绕开** _legacy_protocol_replay
# ---------------------------------------------------------------------------
# 本目录默认 autouse 回放旧协议（`ROUTE_AUTO`），把 `_replan_route` 推回 `_legacy_route`。
# 于是生产默认分支（`legacy_request_protocol is None`）在本目录里**结构上测不到**——
# 这正是"本地 70 passed、干净检出 3 failed"的原因。以下用例显式标 `v2_request`。
@pytest.mark.v2_request
def test_production_legacy_plan_routes_to_view_delta():
    """生产默认（无回放开关）下，legacy 计划的路由是 `view_delta`，不是旧协议."""
    assert o.legacy_request_protocol is None, "本用例必须跑在生产默认下"
    assert pc.plan_mode({}) == pc.MODE_LEGACY
    assert o._replan_route(None, mode=pc.MODE_LEGACY) == o.ROUTE_VIEW_DELTA


@pytest.mark.v2_request
def test_budget_feedback_block_defaults_to_a_protocol_it_can_express():
    """`_budget_feedback_block` 只实现 OPS/FULL_PLAN：不传 route 时必须自选其一.

    反例（修复前）：不传 route 时按 `_replan_route` 取到 `view_delta`，
    而 `_INTERFACE_ADVICE["view_delta"]` 是「只回增量、不用回抄」的文案——
    拿它去拼**全量** OUTLINE_SCHEMA 请求即协议错位。判别方式：要求「不传 route」
    与「显式传它支持的那条协议」产出**完全相同**的反馈块。
    """
    _rep = {"problems": None, "budget": {"max": 60.0}, "lower_bound_sec": 90.0,
            "dialogue_lines": 3, "dialogue_sec": 30.0, "narration_lines": 2}
    _plan = {"main_events": ["a"], "climax": "c", "causal_chain": ["a"]}
    _auto = o._budget_feedback_block(_rep, _plan, "原文", route=None)
    _explicit = o._budget_feedback_block(
        _rep, _plan, "原文", route=o._legacy_route(_rep.get("problems")))
    assert _auto == _explicit
    # 显式那条必须是本函数支持的两条协议之一
    assert o._legacy_route(_rep.get("problems")) in (o.ROUTE_OPS, o.ROUTE_FULL_PLAN)


@pytest.mark.v2_request
def test_a_pure_budget_legacy_round_now_enters_budget_replanning(monkeypatch):
    """纯预算的 legacy 轮必须真的进 `enforce_budget_by_replanning` 且拿到旧协议.

    反例（修复前）：判据写成 `_route not in (ROUTE_VIEW, ROUTE_VIEW_DELTA)`，
    而 legacy 生产路由恒为 `view_delta` ⇒ 该条件**永远为 False**，纯预算轮
    再也进不来（拿不到数值验算表、不写 `plan_replan`）。判别方式：截获调用并
    断言它被**进入**、且收到的 route 是本函数能表达的两条协议之一。
    """
    _seen = {}

    def _spy(plan, novel_text, **kw):
        _seen.update(kw)
        return {}, {}

    monkeypatch.setattr(o, "enforce_budget_by_replanning", _spy)
    from step01_script_gen.plan_revision import RevisionBudget
    _plan = {"main_events": ["a"], "climax": "c", "causal_chain": ["a"]}
    _problems = [{"code": "budget_over", "message": "总时长超限"}]
    assert pc.plan_mode(_plan) == pc.MODE_LEGACY
    assert o._replan_route(_problems, mode=pc.MODE_LEGACY) == o.ROUTE_VIEW_DELTA

    o.revise_plan_once(_plan, _problems, RevisionBudget(max_calls=3, deadline_sec=120),
                       None, novel_text="原文")
    assert _seen, "纯预算的 legacy 轮没有进入预算重规划（判据仍把生产路由排除在外）"
    assert _seen.get("route") in (o.ROUTE_OPS, o.ROUTE_FULL_PLAN), _seen.get("route")
