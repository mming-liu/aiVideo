# -*- coding: utf-8 -*-
"""复核 2026-10-06（第十二轮）①②⑤：**消费准入**的四入口矩阵.

复核给的共同根因（本轮已核实）：**读取过程允许丢弃、清空或重编数据，下游又假定输入
已经通过准入。** 修法按**不变式**分两层，不维护放行名单：

| 层 | 问什么 | 行为 |
|---|---|---|
| **读取准入** | 这份输入**能不能被完整、明确地表示**？ | 不能 ⇒ **拒读**（`view_from_plan` 返回 `None` + 结构化诊断）。不过滤、不清空、不按位置重新编号 |
| **关系与内容验收** | 表示得出来、但关系对不对？ | 保留原貌，交生产者修订（`parse_view` / `view_problems` / 核验） |

合法转换（绑定按事实顺序排列、同值别名归一、按已定关系派生编号）**不属于有损**，
发生在准入通过**之后**。

四条入口各走一遍：**读盘 / 增量 / 完整视图 / 实际消费**（投影、卡片、核验请求）。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import content_verification as cv                                # noqa: E402
from shared import presentation_contract as pc                                # noqa: E402
from shared.screen_cards.plan import card_relations                           # noqa: E402
from step01_script_gen import content_verify as cverify                       # noqa: E402
from step01_script_gen import outline as o                                    # noqa: E402
from step01_script_gen.plan_revision import RevisionBudget                    # noqa: E402
from tests.shared.test_reference_identity_contract import (                   # noqa: E402
    _SRC, _hybrid_base as _hb_base, _hb_plan)


def _codes(ps):
    return [str((x or {}).get("code") or "") for x in (ps or [])]


def _norm(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _raises(fn):
    try:
        fn()
    except Exception as _e:                                                   # noqa: BLE001
        return "%s: %s" % (type(_e).__name__, _e)
    return ""


class _Stub:
    """**计数桩**：被调用一次就记一次。拒读路径上必须是 0."""

    def __init__(self):
        self.n = 0

    def generate(self, req, *a, **k):
        self.n += 1
        return type("R", (), {"success": True, "text": "{}", "call_id": "stub",
                              "error": "", "finish_reason": ""})()


@pytest.fixture
def stub(monkeypatch):
    _s = _Stub()
    monkeypatch.setattr(o, "create_service", lambda *a, **k: _s)
    return _s


# ---------------------------------------------------------------------------
# ① 读盘：**容器 / 成员 / 身份 / 表**的不合法输入一律拒读（不是"清洗后继续"）
# ---------------------------------------------------------------------------
def _m_fact_container(plan):
    plan["presentation"]["fact_bindings"][0]["carriers"] = 1


def _m_fact_member(plan):
    plan["presentation"]["fact_bindings"][0]["carriers"] = [1]


def _m_hook_container(plan):
    plan["presentation"]["hook_bindings"] = {"spoken_uids": [], "carriers": 1}


def _m_hook_member(plan):
    plan["presentation"]["hook_bindings"] = {"spoken_uids": [], "carriers": ["d01"]}


def _m_card_member(plan):
    plan["presentation"]["screen_cards"] = [1]


def _m_card_item_mixed(plan):
    plan["presentation"]["screen_cards"] = list(plan["presentation"]["screen_cards"]) + [7]


def _m_ref_conflict(plan):
    _uids = list(plan["presentation"]["beat_uids"])
    plan["presentation"]["fact_bindings"][0]["carriers"] = [
        {"kind": "visual", "beat_uid": _uids[0], "uid": _uids[1]}]


def _m_dup_binding(plan):
    _b = plan["presentation"]["fact_bindings"]
    _b.append(copy.deepcopy(_b[0]))


def _m_unknown_fact(plan):
    _b = plan["presentation"]["fact_bindings"]
    _b.append({"fact_uid": "f99", "carriers": [{"kind": "visual", "uid":
                                                plan["presentation"]["beat_uids"][0]}]})


def _m_binding_item(plan):
    plan["presentation"]["fact_bindings"].append(3)


def _m_uid_table_len(plan):
    plan["presentation"]["beat_uids"] = list(plan["presentation"]["beat_uids"])[:-1]


def _m_uid_table_dup(plan):
    _u = list(plan["presentation"]["beat_uids"])
    _u[1] = _u[0]
    plan["presentation"]["beat_uids"] = _u


def _m_beat_events_len(plan):
    plan["presentation"]["beat_events"] = [[1]]


def _m_screen_cards_type(plan):
    plan["presentation"]["screen_cards"] = "卡片"


def _m_spoken_type(plan):
    plan["presentation"]["hook_bindings"] = {"spoken_uids": 1, "carriers": []}


#: 每类：`(名字, 变异, 必含 code)`——**全部**都必须"拒读 + 零模型调用 + 基线不变"。
_REFUSE = [
    ("事实载体是整数", _m_fact_container, "view_binding_carriers_type"),
    ("事实载体成员是整数", _m_fact_member, "view_ref_item_type"),
    ("钩子载体是整数", _m_hook_container, "view_hook_carriers_type"),
    ("钩子载体成员是字符串", _m_hook_member, "view_ref_item_type"),
    ("卡片成员是整数", _m_card_member, "view_card_item_invalid"),
    ("卡片表尾多一个非对象", _m_card_item_mixed, "view_card_item_invalid"),
    ("引用身份冲突", _m_ref_conflict, "view_ref_identity_conflict"),
    ("重复绑定", _m_dup_binding, "view_binding_duplicate"),
    ("绑定指向不存在的事实", _m_unknown_fact, "view_binding_unknown_fact"),
    ("绑定项不是对象", _m_binding_item, "view_binding_item_invalid"),
    ("已有 UID 表长度失配", _m_uid_table_len, "view_uid_table_mismatch"),
    ("已有 UID 表重复", _m_uid_table_dup, "view_uid_table_invalid"),
    ("事件归属表长度失配", _m_beat_events_len, "view_beat_events_mismatch"),
    ("卡片字段不是数组", _m_screen_cards_type, "view_field_type"),
    ("spoken_uids 不是数组", _m_spoken_type, "view_hook_spoken_type"),
]


@pytest.mark.parametrize("name,mutate,code", _REFUSE, ids=[c[0] for c in _REFUSE])
def test_the_read_entry_refuses_instead_of_cleaning(name, mutate, code, stub):
    """读盘：**不抛异常、不交出视图、问题里带具体 code 与路径**；生产入口零模型调用."""
    _plan = _hb_plan()
    mutate(_plan)
    _before = _norm(_plan)
    assert _raises(lambda: pc.view_from_plan(_plan)) == "", name
    _v, _p = pc.view_from_plan(_plan)
    assert _v is None, "%s：读盘交出了视图（那就是「用被清洗过的基线」）" % name
    assert code in _codes(_p), (name, _codes(_p))
    assert all(str(x.get("path") or "") for x in _p), \
        "%s：诊断没带字段路径：%s" % (name, _p)
    assert _v is None and _norm(_plan) == _before, "%s：读盘改了计划" % name
    # **生产入口**：读不成就不发请求（基线不是"缺了一行但看起来正常"的那一份）
    _ev: dict = {}
    _new, _why, _cid, _ = o._replan_by_view(
        _plan, [], _SRC, RevisionBudget(max_calls=3), [], evidence=_ev,
        mode=pc.MODE_HYBRID, protocol=o.ROUTE_VIEW_DELTA)
    assert _new is None and _why.startswith("基线读不成可编辑视图"), (name, _why)
    assert stub.n == 0, "%s：基线读不成却仍调用了模型 %d 次" % (name, stub.n)
    assert _ev.get("baseline_read_problems"), name
    assert code in _ev["baseline_read_problems"], (name, _ev["baseline_read_problems"])


# ---------------------------------------------------------------------------
# ② 合法转换**不能被误伤**（"发生改写即失败"是过度收紧）
# ---------------------------------------------------------------------------
def test_legal_transformations_still_pass():
    """绑定按事实顺序排列 / 同值别名归一 / 按已定关系派生编号：**读得出来**."""
    _v, _p = pc.view_from_plan(_hb_plan())
    assert _v is not None and _codes(_p) == [], _codes(_p)
    # **顺序调整**：`fact_bindings` 的排列不携带含义 ⇒ 翻转之后仍读得出来
    _plan = _hb_plan()
    _plan["presentation"]["fact_bindings"] = list(
        reversed(_plan["presentation"]["fact_bindings"]))
    _v2, _p2 = pc.view_from_plan(_plan)
    assert _v2 is not None and _codes(_p2) == [], _codes(_p2)
    assert pc.view_digest(_v) == pc.view_digest(_v2), "只重排了绑定表，内容摘要却变了"
    # **同值别名**：两种写法指同一个目标 ⇒ 合法，并**归一**成规范形状
    _plan3 = _hb_plan()
    _b1 = list(_plan3["presentation"]["beat_uids"])[0]
    _plan3["presentation"]["fact_bindings"][0]["carriers"] = [
        {"kind": "visual", "beat_uid": _b1, "uid": _b1}]
    _v3, _p3 = pc.view_from_plan(_plan3)
    assert _v3 is not None and _codes(_p3) == [], _codes(_p3)
    assert [dict(c) for c in _v3["fact_bindings"][0]["carriers"]] == [
        {"kind": "visual", "uid": _b1}], _v3["fact_bindings"][0]


def test_legal_but_relation_level_problems_are_still_preserved():
    """**第二层**：悬空 / 空关系 / 缺绑定读得出来、**保留原貌**，由验收链报出来."""
    # 空关系
    _plan = _hb_plan()
    _plan["presentation"]["fact_bindings"][0]["carriers"] = []
    _v, _p = pc.view_from_plan(_plan)
    assert _v is not None and _codes(_p) == [], _codes(_p)
    assert _v["fact_bindings"][0]["carriers"] == []
    assert "view_binding_empty" in _codes(pc.parse_view(copy.deepcopy(_v),
                                                        expected_mode=pc.MODE_HYBRID)[1])
    # 悬空：**保留原貌**，不是删掉
    _plan2 = _hb_plan()
    _plan2["presentation"]["fact_bindings"][0]["carriers"] = [
        {"kind": "visual", "uid": "b99"}]
    _v2, _p2 = pc.view_from_plan(_plan2)
    assert _v2 is not None and _codes(_p2) == [], _codes(_p2)
    assert [dict(c) for c in _v2["fact_bindings"][0]["carriers"]] == [
        {"kind": "visual", "uid": "b99"}], "悬空关系被读盘丢掉了"
    assert "view_binding_dangling" in _codes(
        pc.parse_view(copy.deepcopy(_v2), expected_mode=pc.MODE_HYBRID)[1])
    # 缺绑定
    _plan3 = _hb_plan()
    _plan3["presentation"]["fact_bindings"] = \
        _plan3["presentation"]["fact_bindings"][1:]
    _v3, _p3 = pc.view_from_plan(_plan3)
    assert _v3 is not None and _codes(_p3) == [], _codes(_p3)
    assert "view_binding_missing" in _codes(
        pc.parse_view(copy.deepcopy(_v3), expected_mode=pc.MODE_HYBRID)[1])


def test_absent_uid_table_keeps_the_legacy_positional_fallback():
    """**已确认的旧格式缺表适配**：整张表缺席走既有兼容规则（按位置派生），不记问题."""
    _plan = _hb_plan()
    for _k in ("beat_uids", "fact_uids", "dialogue_uids", "narration_uids"):
        _plan["presentation"].pop(_k, None)
    _v, _p = pc.view_from_plan(_plan)
    assert _v is not None and _codes(_p) == [], _codes(_p)
    assert [str(b["uid"]) for b in _v["beats"]] == \
        ["b%02d" % _i for _i in range(1, len(_v["beats"]) + 1)]


def test_absent_beat_events_is_unknown_not_a_malformed_table():
    """**缺席 ≠ 表失配**：整块不写 ⇒ `event_binding=unknown`（既有语义）."""
    _plan = _hb_plan()
    _plan["presentation"].pop("beat_events", None)
    _v, _p = pc.view_from_plan(_plan)
    assert _v is not None and _codes(_p) == [], _codes(_p)
    assert _v["event_binding"] == "unknown"
    assert all(b["event_uids"] == [] for b in _v["beats"])


def test_unparsable_carrier_declaration_is_the_third_state_not_a_clearing():
    """旧形状：**写了承载声明却解析不出** ⇒ `kind=invalid` 第三状态，**不默清**."""
    # 用旧形状计划（`｜承载：` 文案）构造
    _legacy = {"main_events": ["事件一"], "narration_lines": [],
               "shot_beats": ["节拍：镜头一｜本镜信息目的：交代｜原文依据：“原文一”｜载体：画面"],
               "dialogue_lines": [],
               "required_facts": ["主张一｜原文证据：“原文一”｜承载：B9/画面"],
               "hook_spoken": [], "hook_carrier": "B9/画面"}
    _v2, _p2 = pc.view_from_plan(_legacy, mode=pc.MODE_LEGACY)
    assert _v2 is not None and _codes(_p2) == [], _codes(_p2)
    _car = [dict(c) for c in _v2["fact_bindings"][0]["carriers"]]
    assert _car and _car[0]["kind"] == pc.KIND_INVALID, \
        "解析不出的承载声明被静默读成了「没有载体」：%s" % _car
    assert "B9/画面" == _car[0].get("raw"), _car
    assert "view_binding_dangling" in _codes(
        pc.parse_view(copy.deepcopy(_v2), expected_mode=pc.MODE_LEGACY)[1])


# ---------------------------------------------------------------------------
# ③ 消费准入：投影 / 卡片 / 核验请求——**一处准入，内部只消费已检查对象**
# ---------------------------------------------------------------------------
_BAD_VIEWS = [
    ("卡片成员是整数", lambda v: v.update(screen_cards=[1]), "view_item_type"),
    ("节拍成员是整数", lambda v: v.update(beats=[1]), "view_item_type"),
    ("事实绑定成员是整数", lambda v: v.update(fact_bindings=[1]), "view_item_type"),
    ("绑定载体是整数", lambda v: v["fact_bindings"][0].update(carriers=1),
     "view_binding_carriers_type"),
    ("绑定载体成员是整数", lambda v: v["fact_bindings"][0].update(carriers=[1]),
     "view_ref_item_type"),
    ("钩子载体是整数", lambda v: v.update(hook_bindings={"spoken_uids": [],
                                                         "carriers": 1}),
     "view_hook_carriers_type"),
    ("hook_bindings 不是对象", lambda v: v.update(hook_bindings=7), "view_field_type"),
    ("卡片字段不是数组", lambda v: v.update(screen_cards=7), "view_field_type"),
]


@pytest.mark.parametrize("name,mutate,code", _BAD_VIEWS, ids=[c[0] for c in _BAD_VIEWS])
def test_the_projection_boundary_refuses_instead_of_delivering_a_partial_plan(name, mutate, code):
    """投影：**结构化拒收、不交付部分投影**（不再 `dict(1)` 崩在半路）."""
    _v = _hb_base()
    mutate(_v)
    _before = _norm(_v)
    _adm = pc.view_admission_problems(_v)
    assert code in _codes(_adm), (name, _codes(_adm))
    with pytest.raises(pc.PresentationContractError) as _e:
        pc.plan_from_view(_v)
    assert code in _e.value.codes, (name, _e.value.codes)
    assert _norm(_v) == _before, "%s：投影改了视图" % name


#: 卡片消费**只吃** `screen_cards` 与 `fact_bindings` 这两块——矩阵按**它真的消费的
#: 东西**取子集：准入门是"每条边界检查自己会索引的对象"，不是"每条边界都跑一遍全量
#: 判据"（后者会让卡片链路因为一条与它无关的钩子关系而拒收）。
_BAD_CARD_VIEWS = [c for c in _BAD_VIEWS
                   if c[0] in ("卡片成员是整数", "事实绑定成员是整数", "绑定载体是整数",
                               "绑定载体成员是整数", "卡片字段不是数组")]


@pytest.mark.parametrize("name,mutate,code", _BAD_CARD_VIEWS,
                         ids=[c[0] for c in _BAD_CARD_VIEWS])
def test_the_card_consumer_refuses_instead_of_crashing(name, mutate, code):
    """卡片消费：**返回问题**（准备阶段转 `ScreenCardError`），不是抛 `TypeError`."""
    _v = _hb_base()
    mutate(_v)
    _plan = pc.plan_from_view(_hb_base())
    _pres = _plan["presentation"]
    _pres["screen_cards"] = _v.get("screen_cards")
    _pres["fact_bindings"] = _v.get("fact_bindings")
    _probs: list = []
    _rel = card_relations(_plan, problems=_probs)
    assert _rel == [], "%s：卡片关系没拒收：%s" % (name, _rel)
    assert code in _codes(_probs), (name, _codes(_probs))
    # 适配入口把同一批问题透传给准备阶段（它按 `code` 转 `ScreenCardError`）
    from step04_merge.screen_cards import ScreenCardError, card_relations_for
    _r2, _m2, _p2 = card_relations_for({"chapter_outline": _plan})
    assert _r2 == [] and code in _codes(_p2), (name, _codes(_p2))
    with pytest.raises(ScreenCardError) as _e:
        if _p2:
            raise ScreenCardError(str(_p2[0].get("code") or "card_plan_invalid"),
                                  "；".join(str(x.get("reason") or "") for x in _p2),
                                  problems=_p2)
    assert code in str(_e.value.code), (name, _e.value.code)


@pytest.mark.parametrize("name,mutate,code", _BAD_VIEWS, ids=[c[0] for c in _BAD_VIEWS])
def test_the_verification_request_refuses_without_calling_the_model(
        name, mutate, code, monkeypatch):
    """核验请求：**输入准入失败 ⇒ 一次模型调用都不发**，并记成 `FAIL_INPUT`（分账）."""
    _v = _hb_base()
    mutate(_v)
    _called = {"n": 0}

    def _boom(*a, **k):
        _called["n"] += 1
        raise AssertionError("核验请求不该在建服务之前被调用")

    monkeypatch.setattr(o, "create_service", _boom)
    with pytest.raises(pc.PresentationContractError) as _e:
        cverify.verify_view_with_service(_v, "digest", novel_text="原文")
    assert code in _e.value.codes, (name, _e.value.codes)
    assert _called["n"] == 0, "%s：输入不合法却调用了模型" % name
    assert _raises(lambda: cverify.build_verification_prompt(_v)) != "", name

    # 控制器把它记成 **FAIL_INPUT**（与 call_failed / bad_response 分账）
    _ctl = cv.VerificationController(verify=lambda view, dig: cverify
                                     .verify_view_with_service(view, dig, novel_text="原"),
                                     deadline_at=None)
    _rec_ = _ctl.record_for(_v, "d1")
    assert _rec_["status"] == cv.FAIL_INPUT, _rec_
    assert _rec_["code"] == "verify_input_rejected", _rec_
    _probs = cv.verification_problems(_rec_, _v, plan_digest="d1")
    assert "verify_input_rejected" in _codes(_probs), _codes(_probs)
    assert "verify_call_failed" not in _codes(_probs), "输入准入失败被记成了调用失败"


def test_the_legal_view_passes_every_consumption_boundary():
    """**合法对照**：同一份合法视图在四条入口上都干净（准入不误伤正常关系）."""
    _v = _hb_base()
    assert pc.view_admission_problems(_v) == []
    _plan = pc.plan_from_view(_v)
    assert isinstance(_plan, dict) and _plan.get("presentation")
    from step04_merge.screen_cards import card_relations_for
    _rel, _mode, _probs = card_relations_for({"chapter_outline": _plan})
    assert _probs == [] and _mode == pc.MODE_HYBRID
    assert _rel and all(r["card_id"] for r in _rel), _rel


# ---------------------------------------------------------------------------
# ④ 两条应用入口：拒收时**基线逐字不变、零内容核验调用**
# ---------------------------------------------------------------------------
def _env(base, **over):
    _r = pc.delta_request(base)
    _d = {"schema": pc.DELTA_SCHEMA, "rule_version": pc.DELTA_RULE_VERSION,
          "base_plan_hash": _r["base_plan_hash"], "fact_basis_hash": _r["fact_basis_hash"]}
    _d.update(over)
    return _d


class _Acc:
    def __init__(self):
        self.n = 0

    def __call__(self, view):
        self.n += 1
        return o._accept_view_candidate(view, _SRC, None)


def test_both_apply_entries_refuse_the_malformed_relation_and_keep_the_baseline():
    _b = _hb_base()
    _before = _norm(_b)
    _acc = _Acc()
    _v, _p, _rk = pc.apply_delta_view(
        _b, _env(_b, fact_bindings=[{"fact_uid": "f01", "carriers": [1]}]),
        mode=pc.MODE_HYBRID, accept=_acc)
    # 增量入口报的是**增量协议的**生产者码（`delta_*`）——code 由生产者给，不在这里
    # 要求两侧同名（同一条判据、各自带路径）。
    assert _rk["kind"] == "illegal", (_rk, _codes(_p))
    assert any(c.endswith("carrier_item_type") or c == "view_ref_item_type"
               for c in _codes(_p)), _codes(_p)
    assert _v is None and _acc.n == 0, (_v, _acc.n)
    assert _norm(_b) == _before, "增量拒收改动了基线"

    _bad = _hb_base()
    _bad["fact_bindings"][0]["carriers"] = 1
    _acc2 = _Acc()
    _r = pc.revision_request(_b, problems=[], rule_version=o.PLAN_VIEW_RULE_VERSION)
    _v2, _p2, _rk2 = pc.apply_editable_view(
        _b, {"schema": pc.REVISION_SCHEMA, "rule_version": o.PLAN_VIEW_RULE_VERSION,
             "base_plan_hash": _r["base_plan_hash"],
             "fact_basis_hash": _r["fact_basis_hash"], "view": _bad},
        mode=pc.MODE_HYBRID, accept=_acc2)
    assert _rk2["kind"] == "illegal" and "view_binding_carriers_type" in _codes(_p2), \
        _codes(_p2)
    assert _v2 is None and _acc2.n == 0
    assert _norm(_b) == _before, "完整视图拒收改动了基线"


# ---------------------------------------------------------------------------
# ⑤ 复核第十二轮**补查 P1** 的五类输入：真实入口断言
#
# 复核独立复现的现场（本轮全部先复现、再修）：
#   `beat_events[0]=1` 读盘抛 `TypeError`；`evidence_updates=[1]` 返回可用视图、问题为空、
#   修正记录被**过滤成 `[]`**；视图的 `evidence_updates=1`/`[1]`/`plan_basis=1` 消费准入
#   通过、投影随后抛异常；卡片 `card_id`/`uid` 冲突被消费者接受；声音单位 `text=123`
#   一路走到核验、**真的发起了一次模型调用**。
#
# 共同根因：准入只覆盖部分容器和引用，**没有覆盖转换、投影及消费实际读取的完整字段契约**。
# ---------------------------------------------------------------------------
def _m_beat_events_item(plan):
    plan["presentation"]["beat_events"][0] = 1


def _m_evidence_item(plan):
    plan["presentation"]["evidence_updates"] = [1]


def _m_evidence_container(plan):
    plan["presentation"]["evidence_updates"] = 7


def _m_card_alias(plan):
    plan["presentation"]["screen_cards"][0]["uid"] = "zzz"


def _m_card_text_type(plan):
    plan["presentation"]["screen_cards"][0]["text"] = 1


_P1_REFUSE = [
    ("事件归属行不是数组", _m_beat_events_item, "view_beat_events_item_type"),
    ("出处成员不是对象", _m_evidence_item, "view_evidence_item_type"),
    ("出处容器不是数组", _m_evidence_container, "view_evidence_updates_type"),
    ("卡片身份别名冲突", _m_card_alias, "entity_identity_alias_conflict"),
    ("卡片正文类型不对", _m_card_text_type, "entity_field_type"),
]


@pytest.mark.parametrize("name,mutate,code", _P1_REFUSE, ids=[c[0] for c in _P1_REFUSE])
def test_the_review_five_inputs_are_refused_at_the_read_entry(name, mutate, code, stub):
    """读盘：**不抛异常、不交出视图、诊断带字段路径、零模型调用、基线不变**."""
    _plan = _hb_plan()
    mutate(_plan)
    _before = _norm(_plan)
    assert _raises(lambda: pc.view_from_plan(_plan)) == "", name
    _v, _p = pc.view_from_plan(_plan)
    assert _v is None, "%s：读盘交出了视图" % name
    assert code in _codes(_p), (name, _codes(_p))
    assert all(str(x.get("path") or "") for x in _p), (name, _p)
    assert _norm(_plan) == _before, "%s：读盘改了计划" % name
    _ev: dict = {}
    _new, _why, _cid, _ = o._replan_by_view(
        _plan, [], _SRC, RevisionBudget(max_calls=3), [], evidence=_ev,
        mode=pc.MODE_HYBRID, protocol=o.ROUTE_VIEW_DELTA)
    assert _new is None and _why.startswith("基线读不成可编辑视图"), (name, _why)
    assert stub.n == 0, "%s：基线读不成却调用了模型 %d 次" % (name, stub.n)
    assert code in _ev["baseline_read_problems"], (name, _ev)


_P1_BAD_VIEWS = [
    ("出处容器是整数", lambda v: v.update(evidence_updates=1), "view_field_type"),
    ("出处成员是整数", lambda v: v.update(evidence_updates=[1]), "view_evidence_item_type"),
    ("plan_basis 是整数", lambda v: v.update(plan_basis=1), "view_field_type"),
    ("卡片身份别名冲突", lambda v: v["screen_cards"][0].update(uid="zzz"),
     "entity_identity_alias_conflict"),
    ("声音单位正文是数字", lambda v: v["dialogue_units"][0].update(text=123),
     "entity_field_type"),
    ("冻结主张是数字", lambda v: v["frozen_facts"][0].update(claim=1), "view_field_type"),
    ("spoken_uids 成员是整数", lambda v: v.update(hook_bindings={"spoken_uids": [1],
                                                                "carriers": []}),
     "view_hook_spoken_item_type"),
]


@pytest.mark.parametrize("name,mutate,code", _P1_BAD_VIEWS, ids=[c[0] for c in _P1_BAD_VIEWS])
def test_the_review_five_inputs_are_refused_at_every_consumption_boundary(
        name, mutate, code, monkeypatch):
    """投影 / 卡片 / 核验请求**共用同一份判据**：一处拒收，三处都拒收，且不调模型."""
    _v = _hb_base()
    mutate(_v)
    _adm = pc.view_admission_problems(_v)
    assert code in _codes(_adm), (name, _codes(_adm))
    assert all(str(x.get("path") or "") for x in _adm), (name, _adm)
    # ① 投影：结构化拒收，**不交付部分投影**
    with pytest.raises(pc.PresentationContractError) as _e:
        pc.plan_from_view(_v)
    assert code in _e.value.codes, (name, _e.value.codes)
    # ② 卡片消费：返回问题（准备阶段转 `ScreenCardError`），不是崩
    _plan = pc.plan_from_view(_hb_base())
    _plan["presentation"]["screen_cards"] = _v.get("screen_cards")
    _plan["presentation"]["fact_bindings"] = _v.get("fact_bindings")
    _probs: list = []
    assert card_relations(_plan, problems=_probs) == [] or "screen_cards" not in name, name
    # ③ 核验请求：**一次模型调用都不发**
    _called = {"n": 0}

    def _boom(*a, **k):
        _called["n"] += 1
        raise AssertionError("核验请求不该建服务")

    monkeypatch.setattr(o, "create_service", _boom)
    with pytest.raises(pc.PresentationContractError) as _e2:
        cverify.verify_view_with_service(_v, "d", novel_text="原文")
    assert _called["n"] == 0, "%s：输入不合法却调用了模型" % name
    assert _e2.value.codes, name


def test_the_card_adapter_creates_no_asset_dir_when_the_plan_is_malformed(tmp_path):
    """**零绘制副作用**：卡片关系读不出来时，一张 PNG 都不画、目录都不建."""
    from step04_merge.screen_cards import ScreenCardError, card_relations_for
    _plan = pc.plan_from_view(_hb_base())
    _plan["presentation"]["screen_cards"] = [1]
    _rel, _mode, _probs = card_relations_for({"chapter_outline": _plan})
    assert _rel == [] and _probs, _probs
    assert "view_item_type" in _codes(_probs), _codes(_probs)
    _root = tmp_path / "assets"
    assert not _root.exists(), "关系读不出来却已经建了资产目录"
    with pytest.raises(ScreenCardError):
        if _probs:
            raise ScreenCardError(str(_probs[0].get("code") or "card_plan_invalid"),
                                  "；".join(str(x.get("reason") or "") for x in _probs),
                                  problems=_probs)
    assert not list(tmp_path.rglob("*.png")), "拒收路径画了卡片"


# ---------------------------------------------------------------------------
# ⑥ 合法对照：**准入收紧不许误伤正常关系**
# ---------------------------------------------------------------------------
def test_the_legal_zero_dialogue_and_joint_carrier_control_still_passes(stub):
    """合法对照：**零对白**、**联合承载**、**同值别名**、**重编号**全部照常读得出来."""
    _b = _hb_base()
    # 零对白 + 一条事实由「画面 + 卡片」联合承载 + 节拍上的同值别名
    _b["dialogue_units"] = []
    for _bt in _b["beats"]:          # 零对白：每条节拍的 `voice_ref` 都要清掉（否则是悬空）
        _bt["voice_ref"] = None
    _c = dict(_b["screen_cards"][0])
    _b["fact_bindings"][0]["carriers"] = [
        {"kind": "visual", "beat_uid": str(_b["beats"][0]["uid"]),
         "uid": str(_b["beats"][0]["uid"])},
        {"kind": "screen_card", "card_id": str(_c["card_id"])}]
    assert _codes(pc.view_admission_problems(_b)) == [], _codes(
        pc.view_admission_problems(_b))
    _plan = pc.plan_from_view(_b)
    _rel, _mode, _probs = _card_relations_for_ok(_plan)
    assert _probs == [] and _mode == pc.MODE_HYBRID, _probs
    assert _rel and _rel[0]["fact_uids"], _rel

    # **重编号**：新增实体的临时 uid 经本轮映射换成正式 uid，之后仍读得回来
    _r = pc.delta_request(_b)
    _env = {"schema": pc.DELTA_SCHEMA, "rule_version": pc.DELTA_RULE_VERSION,
            "base_plan_hash": _r["base_plan_hash"],
            "fact_basis_hash": _r["fact_basis_hash"],
            "upsert": {"beats": [{"uid": "new:beat", "event_uids": [],
                                  "text": "新节拍", "purpose": "补", "evidence": "",
                                  "source": {}, "voice_ref": None}]},
            "order": {"beats": [str(x["uid"]) for x in _b["beats"]] + ["new:beat"]}}
    _v2, _p2, _rk2 = pc.apply_delta_view(_b, _env, mode=pc.MODE_HYBRID)
    assert _rk2["kind"] == "applied", (_rk2, _codes(_p2))
    assert _rk2["mapping"]["new:beat"].startswith("b")
    assert _codes(pc.parse_view(copy.deepcopy(_v2),
                                expected_mode=pc.MODE_HYBRID)[1]) == [], "映射后读不回来"
    assert pc.view_admission_problems(_v2) == [], _codes(pc.view_admission_problems(_v2))


def _card_relations_for_ok(plan):
    from step04_merge.screen_cards import card_relations_for
    return card_relations_for({"chapter_outline": plan})


# ---------------------------------------------------------------------------
# ⑦ 复核第十二轮**二轮 P1**：检查器自己会崩、读盘丢数据
#
# 复核实测：`frozen_facts=1` 让**准入自己**抛 `TypeError`（"先记录容器类型错误，
# 接着字段检查器仍然遍历它"）；`frozen_events=1` 准入放行、投影随后抛异常；
# 读盘 `narration_lines=[1]` 返回可用视图、问题为空、**旁白被过滤成 `[]`**，
# 且这份坏基线走进了真实修订入口（桩服务创建 1 次、生成 1 次）。
#
# 修法：形状契约**先容器、再成员、最后字段**，且检查器**对任何输入都返回记录**
# （`_obj_members`）；读盘在**转换/过滤/字符串化之前**检查。
# ---------------------------------------------------------------------------
def _m_narration_item(plan):
    plan["narration_lines"] = [1]


def _m_narration_text(plan):
    plan["narration_lines"] = [{"text": 1, "source": ""}]


_P1R_REFUSE = [
    ("旁白成员不是对象", _m_narration_item, "view_narration_item_type"),
    ("旁白正文类型不对", _m_narration_text, "view_narration_text_type"),
]


@pytest.mark.parametrize("name,mutate,code", _P1R_REFUSE, ids=[c[0] for c in _P1R_REFUSE])
def test_a_bad_narration_member_is_refused_instead_of_filtered(name, mutate, code, stub):
    """读盘：坏旁白**拒读**（旧写法返回可用视图、把它过滤成 `[]`、还去发了请求）."""
    _plan = _hb_plan()
    mutate(_plan)
    _before = _norm(_plan)
    assert _raises(lambda: pc.view_from_plan(_plan)) == "", name
    _v, _p = pc.view_from_plan(_plan)
    assert _v is None, "%s：读盘交出了视图（旁白被静默丢掉就是丢了一条声音单位）" % name
    assert code in _codes(_p), (name, _codes(_p))
    assert all(str(x.get("path") or "") for x in _p), (name, _p)
    assert _norm(_plan) == _before, name
    _ev: dict = {}
    _new, _why, _cid, _ = o._replan_by_view(
        _plan, [], _SRC, RevisionBudget(max_calls=3), [], evidence=_ev,
        mode=pc.MODE_HYBRID, protocol=o.ROUTE_VIEW_DELTA)
    assert _new is None and stub.n == 0, (name, _why, stub.n)
    assert code in _ev["baseline_read_problems"], (name, _ev)


_P1R_BAD_VIEWS = [
    ("冻结事实容器是整数", lambda v: v.update(frozen_facts=1), "view_field_type"),
    ("冻结事件容器是整数", lambda v: v.update(frozen_events=1), "view_field_type"),
    ("冻结事件成员是整数", lambda v: v.update(frozen_events=[1]),
     "view_event_item_type"),
    ("旁白单位成员是整数", lambda v: v.update(narration_units=[1]), "view_item_type"),
]


@pytest.mark.parametrize("name,mutate,code", _P1R_BAD_VIEWS,
                         ids=[c[0] for c in _P1R_BAD_VIEWS])
def test_the_checker_itself_never_raises(name, mutate, code):
    """**检查器对非法输入必须返回记录、不抛异常**——否则"结构化拒收"根本发不出来."""
    _v = _hb_base()
    mutate(_v)
    assert _raises(lambda: pc.view_admission_problems(_v)) == "", name
    _adm = pc.view_admission_problems(_v)
    assert code in _codes(_adm), (name, _codes(_adm))
    assert all(str(x.get("path") or "") for x in _adm), (name, _adm)
    # 投影**结构化拒收**（抛的是带 problems 的契约异常，不是 `TypeError`）
    with pytest.raises(pc.PresentationContractError) as _e:
        pc.plan_from_view(_v)
    assert code in _e.value.codes, (name, _e.value.codes)
