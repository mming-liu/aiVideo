# -*- coding: utf-8 -*-
"""复核 2026-10-06（第十轮）①：**引用身份契约收齐到全部引用位置**.

复核给的读数（真实增量入口实测）：

| 载体引用 | 修复前 |
|---|---|
| `card_id="c01", uid="c01"` | 合法，绑定 `c01` |
| `card_id="c01", uid="c02"` | **applied，绑定悄悄变成 `c02`** |
| `beat_uid="b01", uid="b02"` | **applied，绑定变成 `b02`** |

根因：`ref_uid` 按**固定优先序** `uid > card_id > beat_uid` 取值，而四种载体的**规范键
各不相同**（画面是 `beat_uid`、卡片是 `card_id`、声音是 `uid`）。于是"多写一个 id 写法"
就等于给了"目标是谁"的第二个答案，代码静默取其中一个——与卡片实体的
`entity_identity_alias_conflict` 属于**同一缺陷类别**，只是落点不同。

另外两处同类遗漏（同一个根因、不同落点）：
- **改写引用那一步会把冲突抹平**：`_assign_formal_uids` 用 `_ref_in` 把引用重建成
  `{"kind","uid"}`，多余的 id 写法**就地消失**——所以形状门必须在那之前拒收；
- **卡片的 `beat_uid` 也是一条引用**，此前只检查"非空"，于是把新增卡片挂到一个不存在的
  节拍上照样 `applied`；而挂到**新增节拍**上时它又没有进 uid 映射表。

本文件把这三处钉住，并核「拒收时基线不变、**零内容核验调用**」。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import presentation_contract as pc                                # noqa: E402
from step01_script_gen import outline as o                                    # noqa: E402
from step01_script_gen.plan_revision import RevisionBudget as _RevisionBudget  # noqa: E402

_S = json.loads((_ROOT / "tests" / "step01_script_gen" / "fixtures"
                 / "plan_revision_mixed_legal_sample_v1.json").read_text(encoding="utf-8"))
_SRC = _S["source"]
_LEGAL, _ = o.apply_replan_ops(_S["base_plan"], _S["response"], _SRC)
_VIEW, _ = pc.view_from_plan(_LEGAL)

_B1 = str(_VIEW["beats"][0]["uid"])
_B2 = str(_VIEW["beats"][1]["uid"])
_D1 = str(_VIEW["dialogue_units"][0]["uid"])
_FU = str(_VIEW["fact_bindings"][0]["fact_uid"])


def _base():
    return json.loads(json.dumps(_VIEW))


def _hybrid_base():
    """**合法 hybrid 视图**（卡片只在 `screen_hybrid` 下合法）：3 节拍 + 1 卡 + 3 事实."""
    _h = json.loads(json.dumps(_VIEW))
    _h.update(mode=pc.MODE_HYBRID, event_binding="declared", narration_units=[])
    _ev = [str(e) for e in _h["frozen_events"]]
    for _i, _b in enumerate(_h["beats"]):
        _b["event_uids"] = list(_ev) if _i == 0 else list(_ev[-1:])
        if (_b.get("voice_ref") or {}).get("kind") == pc.KIND_NARRATION:
            _b["voice_ref"] = None
    _h["screen_cards"] = [{"card_id": "c01", "beat_uid": str(_h["beats"][2]["uid"]),
                           "template": "background", "text": str(_h["frozen_facts"][0]["claim"]),
                           "placement": "info_panel"}]
    for _fb in _h["fact_bindings"]:
        _fb["carriers"] = [{"kind": "visual", "uid": str(_h["beats"][_h["fact_bindings"].index(_fb)]
                                                          ["uid"])}]
    return _h


def _env(base, **over):
    _r = pc.delta_request(base)
    _d = {"schema": pc.DELTA_SCHEMA, "rule_version": pc.DELTA_RULE_VERSION,
          "base_plan_hash": _r["base_plan_hash"], "fact_basis_hash": _r["fact_basis_hash"]}
    _d.update(over)
    return _d


class _Accept:
    """计数版验收器：拒收路径必须**一次都不调**它（复核 ① 的退出条件之一）."""

    def __init__(self):
        self.n = 0

    def __call__(self, view):
        self.n += 1
        return o._accept_view_candidate(view, _SRC, None)


def _delta(base, acc, **over):
    return pc.apply_delta_view(base, _env(base, **over), mode="legacy_audio", accept=acc)


def _full(base, acc, view):
    _r = pc.revision_request(base, problems=[], rule_version=o.PLAN_VIEW_RULE_VERSION)
    return pc.apply_editable_view(
        base, {"schema": pc.REVISION_SCHEMA, "rule_version": o.PLAN_VIEW_RULE_VERSION,
               "base_plan_hash": _r["base_plan_hash"],
               "fact_basis_hash": _r["fact_basis_hash"], "view": view},
        mode="legacy_audio", accept=acc)


def _codes(probs):
    return [p.get("code") for p in (probs or [])]


# ---------------------------------------------------------------------------
# ①-1 三种 id 写法：同值允许、冲突前置拒收（逐个引用位置）
# ---------------------------------------------------------------------------
_CARRIER_CASES = [
    ("节拍：beat_uid 与 uid 冲突", {"kind": "visual", "beat_uid": _B1, "uid": _B2},
     "view_ref_identity_conflict"),
    ("卡片：card_id 与 uid 冲突", {"kind": "screen_card", "card_id": "c01", "uid": "c02"},
     "view_ref_identity_conflict"),
    ("两目标都合法但不同", {"kind": "visual", "uid": _B1, "card_id": _B2},
     "view_ref_identity_conflict"),
    ("一个合法一个悬空", {"kind": "visual", "beat_uid": _B1, "uid": "zzz9"},
     "view_ref_identity_conflict"),
    ("节拍：同值别名", {"kind": "visual", "beat_uid": _B1, "uid": _B1}, ""),
    ("跨类型引用（旁白位指到节拍）", {"kind": "narration", "uid": _B1},
     "view_binding_dangling"),
    ("悬空引用", {"kind": "visual", "uid": "b99"}, "view_binding_dangling"),
]


@pytest.mark.parametrize("name,carrier,code", _CARRIER_CASES, ids=[c[0] for c in _CARRIER_CASES])
def test_carrier_references_share_one_identity_contract(name, carrier, code):
    """`fact_bindings[].carriers` 上：冲突**前置拒收**、同值别名放行、悬空与跨类型照拒."""
    _b = _base()
    _before = json.dumps(_b, ensure_ascii=False, sort_keys=True)
    _acc = _Accept()
    _v, _p, _rk = _delta(_b, _acc, fact_bindings=[{"fact_uid": _FU, "carriers": [carrier]}])
    if code:
        assert _rk["kind"] == "illegal", (name, _rk, _codes(_p))
        assert code in _codes(_p), (name, _codes(_p))
        assert _acc.n == 0, "拒收路径调了内容核验（%d 次）" % _acc.n
        assert _v is None
    else:
        assert _rk["kind"] == "applied", (name, _rk, _codes(_p))
        assert _acc.n == 1, name
    assert json.dumps(_b, ensure_ascii=False, sort_keys=True) == _before, "基线被改动"


def test_voice_ref_uses_the_same_contract():
    """节拍内嵌的 `voice_ref` 与载体引用**共用一份判据**（同一个根因的另一个落点）."""
    _it = json.loads(json.dumps(_VIEW["beats"][0]))
    _it["voice_ref"] = {"kind": "narration", "uid": str(_VIEW["narration_units"][0]["uid"]),
                        "card_id": str(_VIEW["narration_units"][1]["uid"])}
    _b = _base()
    _before = json.dumps(_b, ensure_ascii=False, sort_keys=True)
    _acc = _Accept()
    _v, _p, _rk = _delta(_b, _acc, upsert={"beats": [_it]})
    assert _rk["kind"] == "illegal", (_rk, _codes(_p))
    assert "view_ref_identity_conflict" in _codes(_p), _codes(_p)
    assert _acc.n == 0
    assert json.dumps(_b, ensure_ascii=False, sort_keys=True) == _before


def test_hook_carriers_use_the_same_contract():
    """钩子载体同样走这一份契约（第三个落点）."""
    _b = _base()
    _acc = _Accept()
    _v, _p, _rk = _delta(_b, _acc, hook_bindings={
        "spoken_uids": [],
        "carriers": [{"kind": "visual", "beat_uid": _B1, "uid": _B2}]})
    assert _rk["kind"] == "illegal", (_rk, _codes(_p))
    assert "view_ref_identity_conflict" in _codes(_p), _codes(_p)
    assert _acc.n == 0
    # 合法对照：同值别名 + 真存在的声音单位
    _b2 = _base()
    _acc2 = _Accept()
    _v2, _p2, _r2 = _delta(_b2, _acc2, hook_bindings={
        "spoken_uids": [_D1],
        "carriers": [{"kind": "dialogue", "uid": _D1, "card_id": _D1}]})
    assert _r2["kind"] in ("applied", "no_change"), (_r2, _codes(_p2))
    assert "view_ref_identity_conflict" not in _codes(_p2), _codes(_p2)


def test_both_entry_points_report_the_same_conflict():
    """**两个入口共用同一份规则**：完整视图里的冲突同样按具体 code 拒收."""
    _bad = _base()
    for _b in _bad["fact_bindings"]:
        if str(_b["fact_uid"]) == _FU:
            _b["carriers"] = [{"kind": "visual", "beat_uid": _B1, "uid": _B2}]
    _acc = _Accept()
    _v, _p, _rk = _full(_base(), _acc, _bad)
    assert _rk["kind"] == "illegal", (_rk, _codes(_p))
    assert "view_ref_identity_conflict" in _codes(_p), _codes(_p)
    assert _acc.n == 0


# ---------------------------------------------------------------------------
# ①-2 临时引用（`new:`）
# ---------------------------------------------------------------------------
def _new_beat(uid="new:beat", text="新节拍"):
    return {"uid": uid, "event_uids": [], "text": text, "purpose": "补",
            "evidence": "", "source": {}, "voice_ref": None}


def test_a_temp_reference_resolves_only_when_the_same_response_introduces_it():
    """`new:` 引用：同一份响应里新增出来了才成立；没新增就是**悬空**."""
    _order = [str(b["uid"]) for b in _VIEW["beats"]] + ["new:beat"]
    _b = _base()
    _acc = _Accept()
    _v, _p, _rk = _delta(_b, _acc,
                         upsert={"beats": [_new_beat()]}, order={"beats": _order},
                         fact_bindings=[{"fact_uid": _FU, "carriers": [
                             {"kind": "visual", "uid": "new:beat"}]}])
    assert _rk["kind"] == "applied", (_rk, _codes(_p))
    assert _rk["mapping"].get("new:beat") == "b05", _rk["mapping"]
    assert {"kind": "visual", "uid": "b05"} in [
        dict(c) for c in next(b for b in _v["fact_bindings"]
                              if str(b["fact_uid"]) == _FU)["carriers"]], _v

    _b2 = _base()
    _acc2 = _Accept()
    _v2, _p2, _r2 = _delta(_b2, _acc2, fact_bindings=[{"fact_uid": _FU, "carriers": [
        {"kind": "visual", "uid": "new:ghost"}]}])
    assert _r2["kind"] == "illegal", (_r2, _codes(_p2))
    assert "view_binding_dangling" in _codes(_p2), _codes(_p2)
    assert _acc2.n == 0


def test_a_new_card_on_a_new_beat_is_remapped_and_accepted():
    """卡片挂在**新增节拍**上：`beat_uid` 必须跟着进映射（旧写法留下指向临时 id 的引用）."""
    _b = _hybrid_base()
    _uids = [str(x["uid"]) for x in _b["beats"]]
    _cards = [str(c["card_id"]) for c in _b["screen_cards"]]
    _new_card = {"card_id": "new:card9", "beat_uid": "new:beat", "template": "background",
                 "text": "新节拍的字", "placement": "info_panel"}
    _acc = _Accept()
    _v, _p, _rk = pc.apply_delta_view(
        _b, _env(_b, upsert={"beats": [_new_beat()], "screen_cards": [_new_card]},
                 order={"beats": _uids + ["new:beat"],
                        "screen_cards": _cards + ["new:card9"]},
                 fact_bindings=[{"fact_uid": str(_b["fact_bindings"][0]["fact_uid"]),
                                 "carriers": [{"kind": "screen_card",
                                               "card_id": "new:card9"}]}]),
        mode=pc.MODE_HYBRID, accept=_acc)
    assert _rk["kind"] == "applied", (_rk, _codes(_p))
    _mapped = _rk["mapping"]["new:card9"]
    _card = next(c for c in _v["screen_cards"] if str(c["card_id"]) == _mapped)
    assert _card["beat_uid"] == _rk["mapping"]["new:beat"],         "卡片仍指向临时节拍 id：%s（映射 %s）" % (_card, _rk["mapping"])
    assert "new:" not in json.dumps(_v, ensure_ascii=False), "候选里残留临时 id"


def test_a_card_on_a_beat_that_does_not_exist_is_rejected():
    """卡片的 `beat_uid` 是一条**引用**：不在节拍集合里 = 悬空（此前只查"非空"）."""
    _b = _hybrid_base()
    _bad_card = {"card_id": "new:card9", "beat_uid": "b09", "template": "background",
                 "text": "悬空卡", "placement": "info_panel"}
    _acc = _Accept()
    _v, _p, _rk = pc.apply_delta_view(
        _b, _env(_b, upsert={"screen_cards": [_bad_card]},
                 order={"screen_cards": [str(c["card_id"]) for c in _b["screen_cards"]]
                                        + ["new:card9"]}),
        mode=pc.MODE_HYBRID, accept=_acc)
    assert _rk["kind"] == "illegal", (_rk, _codes(_p))
    assert "view_card_beat_dangling" in _codes(_p), _codes(_p)
    assert _acc.n == 0
    assert _v is None


def test_the_card_beat_closure_holds_on_the_full_view_entry_too():
    """同一条闭合判据在**完整视图**入口同样生效（不许只在增量侧有）."""
    _bad = _hybrid_base()
    _bad["screen_cards"] = list(_bad["screen_cards"]) + [
        {"card_id": "c99", "beat_uid": "b09", "template": "background",
         "text": "悬空卡", "placement": "info_panel"}]
    _acc = _Accept()
    _v, _p, _rk = _full(_bad, _acc, _bad)
    assert _rk["kind"] == "illegal", (_rk, _codes(_p))
    assert "view_card_beat_dangling" in _codes(_p), _codes(_p)
    assert _acc.n == 0


def test_the_plan_side_read_reports_the_conflict_instead_of_normalizing_it():
    """**读盘这一侧**同样不许静默：`presentation` 里的冲突引用要报出来，不是就地归一.

    `_view_from_hybrid_plan` 用 `_ref_in` 把引用重建成 `{"kind","uid"}`——多余的 id 写法
    **就地消失**。不在这里也报一次的话，"计划文件里带着一个打架的载体引用"会被读成一份
    干净的视图，问题在进入验收链之前就被抹掉了。
    """
    _plan = pc.plan_from_view(_hybrid_base())
    _fu = str(_plan["presentation"]["fact_bindings"][0]["fact_uid"])
    _b1 = str(_plan["presentation"]["beat_uids"][0])
    _b2 = str(_plan["presentation"]["beat_uids"][1])
    for _b in _plan["presentation"]["fact_bindings"]:
        if str(_b["fact_uid"]) == _fu:
            _b["carriers"] = [{"kind": "visual", "beat_uid": _b1, "uid": _b2}]
    _v, _probs = pc.view_from_plan(_plan)
    assert "view_ref_identity_conflict" in _codes(_probs), _codes(_probs)
    # 并且**生产入口读出来就停**（不是"带一串提示继续往下走"）：基线是后面合并与验收
    # 要用的那一份，冲突在那里会被 `_ref_in` 归一掉，之后再无痕迹。
    _ev: dict = {}
    _new, _why, _cid, _ = o._replan_by_view(
        _plan, [], _SRC, _RevisionBudget(), [], evidence=_ev,
        mode=pc.MODE_HYBRID, protocol=o.ROUTE_VIEW_DELTA)
    assert _new is None, "冲突基线被放行进了修订"
    assert "view_ref_identity_conflict" in _why, _why
    assert "view_ref_identity_conflict" in _ev.get("baseline_read_problems", []), _ev

    # 合法对照：干净的联合承载读得出来，且证据里没有读盘问题
    _ok = pc.plan_from_view(_hybrid_base())
    _v2, _p2 = pc.view_from_plan(_ok)
    assert _v2 is not None and _codes(_p2) == [], _codes(_p2)


# ---------------------------------------------------------------------------
# 复核 2026-10-06（第十二轮）①：**成员级**引用准入
#
# `ref_items()` 只检查了**容器**"是不是列表"，成员没检查——读盘路径上
# `carriers=[1]` / `["d01"]` / `[True]` / `[[...]]` 四类**全部抛 AttributeError**
# （`(ref or {}).get(...)`）。明文要求：**不能仅加 `.get()` 防护或过滤非法成员**——
# 那会把损坏的关系悄悄删掉。三件事要同时成立：
#
#   1. 三入口都不崩，且给出**带成员下标**的结构化诊断；
#   2. 形状非法的**基线停止修订**（`view_from_plan` 返回 `None`）、零模型调用、基线不变；
#   3. 形状合法但**悬空**的关系**保留原貌**，交生产者修订（不是这里拒收，也不是删掉）。
# ---------------------------------------------------------------------------
_BAD_MEMBERS = [1, True, "d01", [["visual", "b01"]], 0.5]  # `{"kind":"visual"}` 是 dict、只是缺 id，另有专门用例


def _raises(fn):
    try:
        fn()
    except Exception as _e:                                                   # noqa: BLE001
        return "%s: %s" % (type(_e).__name__, _e)
    return ""


def _hb_plan():
    _v = _hybrid_base()
    _plan, _probs = pc.view_from_plan(_v)
    assert _plan is not None or _probs
    return pc.plan_from_view(_v)


@pytest.mark.parametrize("member", _BAD_MEMBERS, ids=[repr(m)[:22] for m in _BAD_MEMBERS])
def test_a_malformed_carrier_member_never_crashes_the_read_path(member):
    """读盘：成员不是合法引用 ⇒ **结构化诊断 + 视图为 None**（此前直接 `AttributeError`）."""
    _plan = _hb_plan()
    _plan["presentation"]["fact_bindings"][0]["carriers"] = [member]
    _before = json.dumps(_plan, ensure_ascii=False, sort_keys=True)
    _exc = _raises(lambda: pc.view_from_plan(_plan))
    assert _exc == "", "读盘入口抛异常：%s" % _exc
    _v, _p = pc.view_from_plan(_plan)
    assert _v is None, "形状非法的基线**必须停止修订**，不是一个带提示的视图"
    assert _codes(_p) == ["view_ref_item_type"], _codes(_p)
    assert _p[0]["path"].endswith("[0]"), "错误路径没带到成员下标：%s" % _p[0]["path"]
    assert json.dumps(_plan, ensure_ascii=False, sort_keys=True) == _before, "读盘改了计划"


@pytest.mark.parametrize("member", _BAD_MEMBERS, ids=[repr(m)[:22] for m in _BAD_MEMBERS])
def test_a_malformed_carrier_member_never_crashes_the_delta_entry(member):
    _b = _hybrid_base()
    _acc = _Accept()
    _exc = _raises(lambda: pic_delta(_b, _acc, member))
    assert _exc == "", "增量入口抛异常：%s" % _exc
    _v, _p, _rk = pic_delta(_b, _acc, member)
    assert _rk["kind"] == "illegal" and _acc.n == 0, (_rk, _codes(_p))
    assert _v is None


def pic_delta(base, acc, member):
    _r = pc.delta_request(base)
    return pc.apply_delta_view(base, {"schema": pc.DELTA_SCHEMA,
                                      "rule_version": pc.DELTA_RULE_VERSION,
                                      "base_plan_hash": _r["base_plan_hash"],
                                      "fact_basis_hash": _r["fact_basis_hash"],
                                      "fact_bindings": [{"fact_uid": "f01",
                                                         "carriers": [member]}]},
                               mode=pc.MODE_HYBRID, accept=acc)


@pytest.mark.parametrize("member", _BAD_MEMBERS, ids=[repr(m)[:22] for m in _BAD_MEMBERS])
def test_a_malformed_carrier_member_never_crashes_the_full_view_entry(member):
    _b = _hybrid_base()
    _bad = json.loads(json.dumps(_b))
    _bad["fact_bindings"][0]["carriers"] = [member]
    _acc = _Accept()
    _exc = _raises(lambda: _full(_b, _acc, _bad))
    assert _exc == "", "完整视图入口抛异常：%s" % _exc
    _v, _p, _rk = _full(_b, _acc, _bad)
    assert _rk["kind"] == "illegal", (_rk, _codes(_p))
    assert "view_ref_item_type" in _codes(_p), _codes(_p)
    assert _acc.n == 0


def test_a_reference_without_an_id_or_a_kind_stops_the_read():
    """dict 形状但**缺 id / 缺 kind / 别名冲突**：三种都停止读盘，code 各不相同."""
    for _member, _code in (({"kind": "visual"}, "view_ref_id_missing"),
                           ({"uid": _B1}, "view_ref_kind_invalid"),
                           ({"kind": "visual", "beat_uid": _B1, "uid": _B2},
                            "view_ref_identity_conflict")):
        _plan = _hb_plan()
        _plan["presentation"]["fact_bindings"][0]["carriers"] = [_member]
        _v, _p = pc.view_from_plan(_plan)
        assert _v is None, (_member, _codes(_p))
        assert _code in _codes(_p), (_member, _codes(_p))


def test_a_dangling_but_well_formed_reference_is_preserved_not_dropped():
    """**形状合法但悬空**：保留原貌交生产者——既不在这里拒收，也不悄悄删掉."""
    _plan = _hb_plan()
    _plan["presentation"]["fact_bindings"][0]["carriers"] = [
        {"kind": "visual", "uid": "b99"}]
    _v, _p = pc.view_from_plan(_plan)
    assert _v is not None and _codes(_p) == [], _codes(_p)
    assert [dict(x) for x in _v["fact_bindings"][0]["carriers"]] == [
        {"kind": "visual", "uid": "b99"}], "悬空引用被读盘丢掉了"
    assert "view_binding_dangling" in [
        x["code"] for x in o.plan_acceptance_problems(pc.plan_from_view(_v), _SRC)], \
        "悬空关系没有交到生产者手里"


def test_an_empty_carrier_list_is_not_a_shape_violation():
    """**空关系不是形状违约**：`carriers=[]` 读得出来，由验收链判"没有载体"."""
    _plan = _hb_plan()
    _plan["presentation"]["fact_bindings"][0]["carriers"] = []
    _v, _p = pc.view_from_plan(_plan)
    assert _v is not None and _codes(_p) == [], _codes(_p)
    _codes2 = [x["code"] for x in pc.parse_view(json.loads(json.dumps(_v)),
                                              expected_mode=pc.MODE_HYBRID)[1]]
    assert _codes2.count("view_binding_empty") == 1, _codes2


def test_an_unparsable_reference_marker_is_a_legal_third_state():
    """`kind=invalid`（写了标注但解析不出）是**合法表示**，读盘放行、由判据报出来."""
    _plan = _hb_plan()
    _plan["presentation"]["hook_bindings"] = {
        "spoken_uids": [], "carriers": [{"kind": pc.KIND_INVALID, "raw": "卡片9"}]}
    _v, _p = pc.view_from_plan(_plan)
    assert _v is not None, ["第三种状态被读盘拒收了"] + _codes(_p)
    assert _codes(_p) == [], _codes(_p)
    assert "view_hook_carrier_dangling" in [
        x["code"] for x in pc.parse_view(json.loads(json.dumps(_v)),
                                         expected_mode=pc.MODE_HYBRID)[1]]


def test_legal_aliases_still_pass_member_level_admission():
    """**合法对照**：同值别名（成员级判据不许误伤正常关系）."""
    _plan = _hb_plan()
    _plan["presentation"]["fact_bindings"][0]["carriers"] = [
        {"kind": "visual", "beat_uid": _B1, "uid": _B1}]
    _v, _p = pc.view_from_plan(_plan)
    assert _v is not None and _codes(_p) == [], _codes(_p)
    assert [dict(x) for x in _v["fact_bindings"][0]["carriers"]] == [
        {"kind": "visual", "uid": _B1}], "归一没有重建规范引用"
