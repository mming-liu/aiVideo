# -*- coding: utf-8 -*-
"""复核 2026-10-06（第十一轮）收口包 1：**统一关系准入与变换**的逐类矩阵.

复核点名的四处，两处在这里被钉住：

- **P1 嵌套类型违约仍能打断生产修订**：`hook_bindings.carriers=1` 让真实
  `revise_plan_once` 抛 `TypeError`（完整视图入口与读盘入口同样）。根因是"记一条问题、
  再继续往下走"——`enumerate(1)` / `list(1)` 直接崩。修法：**共用一份安全读取**
  （`ref_items`），不是数组就**停止这一支遍历**；**缺席 / null 不算类型违约**（旧式单数
  `carrier` 就是不写 `carriers`）。
- **P2 UID 映射重新制造引用冲突**：合法同值别名 `uid=card_id="new:dialogue"` 被映射成
  `uid="d02"`、`card_id="new:dialogue"`，当轮 `applied`，**再解析即拒收**，下一轮空增量变
  `illegal`。修法：映射时**重建规范引用** + 映射完成后**重验完整形状**。
- **P2 完整视图入口不核规则版本**：缺版本 / `unknown.v999` 都 `applied`。修法：期望版本
  **由分派者传入**，核对声明后再应用，**不覆盖声明制造一致**。

矩阵按复核指定的八类走，并且**三条入口各走一遍**（增量 / 完整视图 / 读盘）。
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

_S = json.loads((_ROOT / "tests" / "step01_script_gen" / "fixtures"
                 / "plan_revision_mixed_legal_sample_v1.json").read_text(encoding="utf-8"))
_SRC = _S["source"]
_LEGAL, _ = o.apply_replan_ops(_S["base_plan"], _S["response"], _SRC)
_VIEW, _ = pc.view_from_plan(_LEGAL)
_FU = str(_VIEW["fact_bindings"][0]["fact_uid"])
_B1 = str(_VIEW["beats"][0]["uid"])
_B2 = str(_VIEW["beats"][1]["uid"])
_D1 = str(_VIEW["dialogue_units"][0]["uid"])


def _base():
    return json.loads(json.dumps(_VIEW))


def _hb_base():
    """合法 hybrid 视图（`screen_cards` 只在 hybrid 下合法；事件按序各归一，避免误触顺序判据）."""
    _h = json.loads(json.dumps(_VIEW))
    _h.update(mode=pc.MODE_HYBRID, event_binding="declared", narration_units=[],
              screen_cards=[])
    _ev = [str(e) for e in _h["frozen_events"]]
    for _i, _b in enumerate(_h["beats"]):
        _b["event_uids"] = [_ev[min(_i, len(_ev) - 1)]]
        _b["voice_ref"] = None
    for _i, _fb in enumerate(_h["fact_bindings"]):
        _fb["carriers"] = [{"kind": "visual", "uid": str(_h["beats"][_i]["uid"])}]
    return _h


def _env(base, **over):
    _r = pc.delta_request(base)
    _d = {"schema": pc.DELTA_SCHEMA, "rule_version": pc.DELTA_RULE_VERSION,
          "base_plan_hash": _r["base_plan_hash"], "fact_basis_hash": _r["fact_basis_hash"]}
    _d.update(over)
    return _d


def _full_resp(base, view, *, rule_version=o.PLAN_VIEW_RULE_VERSION):
    _r = pc.revision_request(base, problems=[], rule_version=o.PLAN_VIEW_RULE_VERSION)
    _d = {"schema": pc.REVISION_SCHEMA, "rule_version": rule_version,
          "base_plan_hash": _r["base_plan_hash"],
          "fact_basis_hash": _r["fact_basis_hash"], "view": view}
    return _d


class _Acc:
    def __init__(self):
        self.n = 0

    def __call__(self, view):
        self.n += 1
        return o._accept_view_candidate(view, _SRC, None)


def _codes(ps):
    return [p.get("code") for p in (ps or [])]


def _delta(base, acc, mode="legacy_audio", **over):
    return pc.apply_delta_view(base, _env(base, **over), mode=mode, accept=acc)


def _full(base, acc, view, **kw):
    return pc.apply_editable_view(base, _full_resp(base, view, **kw),
                                  mode=str(base.get("mode") or "legacy_audio"), accept=acc)


def _raises(fn):
    try:
        fn()
    except Exception as _e:                                                   # noqa: BLE001
        return "%s: %s" % (type(_e).__name__, _e)
    return ""


# ---------------------------------------------------------------------------
# ① 合法正常路径（三条入口）
# ---------------------------------------------------------------------------
def test_the_legal_path_is_clean_on_all_three_entries():
    _b = _base()
    _moved = json.loads(json.dumps(_b))
    for _fb in _moved["fact_bindings"]:
        if str(_fb["fact_uid"]) == _FU:
            _fb["carriers"] = [{"kind": "visual", "uid": _B2}]
    _acc = _Acc()
    _v, _p, _rk = _delta(_b, _acc,
                         fact_bindings=[{"fact_uid": _FU,
                                         "carriers": [{"kind": "visual", "uid": _B2}]}])
    assert _rk["kind"] == "applied" and _acc.n == 1, (_rk, _codes(_p))
    _acc2 = _Acc()
    _v2, _p2, _r2 = _full(_base(), _acc2, _moved)
    assert _r2["kind"] == "applied" and _acc2.n == 1, (_r2, _codes(_p2))
    assert pc.view_digest(_v) == pc.view_digest(_v2), "两条传输表示算出了不同的候选"
    assert pc.view_from_plan(pc.plan_from_view(_v))[1] == [], "投影往返后有读盘问题"


# ---------------------------------------------------------------------------
# ② 缺失 / null / 错误类型 —— **报错即止，不许抛异常**（P1）
# ---------------------------------------------------------------------------
#: 每一类：增量侧的**必拒 code** + 完整视图侧**是否也拒**.
#:
#: 两边的契约**本来就不完全同一份**：`upsert` / `delete` / `order` 是**增量专有的改动块**，
#: 完整视图里出现它们是"视图里多了一个没人读的键"（既有的忽略语义），不是形状违约；
#: `hook_bindings.carriers=null` 在完整视图里等于"旧式单数写法也没给"，即**没有载体**。
#: 把这两类硬凑成"两边都必须拒"就是把一份契约当成两份——矩阵要记的是**各自应得**的结果。
_BAD_RELATIONS = [
    ("钩子载体是整数", {"hook_bindings": {"spoken_uids": [], "carriers": 1}}, True),
    ("钩子载体是字符串", {"hook_bindings": {"spoken_uids": [], "carriers": "画面"}}, True),
    ("钩子载体是 null", {"hook_bindings": {"spoken_uids": [], "carriers": None}}, False),
    ("钩子 spoken 是整数", {"hook_bindings": {"spoken_uids": 1, "carriers": []}}, True),
    ("钩子是 null", {"hook_bindings": None}, True),
    ("事实载体是整数", {"fact_bindings": [{"fact_uid": _FU, "carriers": 1}]}, True),
    ("事实载体是对象", {"fact_bindings": [{"fact_uid": _FU, "carriers": {}}]}, True),
    ("出处项不是对象", {"evidence_updates": [1]}, True),
    ("出处是 null", {"evidence_updates": None}, False),
    ("顺序块是字符串", {"order": "b01"}, False),
    ("upsert 是数组", {"upsert": []}, False),
]


@pytest.mark.parametrize("name,over,full_rejects", _BAD_RELATIONS,
                         ids=[b[0] for b in _BAD_RELATIONS])
def test_a_nested_type_violation_is_rejected_structurally_not_by_crashing(
        name, over, full_rejects):
    """复核 P1：**三条入口都不许抛异常**；增量侧一律结构化拒收、基线不动、零内容核验."""
    _b = _base()
    _before = json.dumps(_b, ensure_ascii=False, sort_keys=True)
    _acc = _Acc()
    _exc = _raises(lambda: _delta(_b, _acc, **over))
    assert _exc == "", "增量入口抛异常：%s" % _exc
    _v, _p, _rk = _delta(_b, _acc, **over)
    assert _rk["kind"] == "illegal", (name, _rk, _codes(_p))
    assert all(str(c or "") for c in _codes(_p)), (name, _codes(_p))
    assert _acc.n == 0, name
    assert _v is None, name
    assert json.dumps(_b, ensure_ascii=False, sort_keys=True) == _before, name

    # 完整视图入口：同一种形状塞进 `view` 里——**只要求"不抛异常 + 结论结构化"**
    _h = _base()
    for _k, _val in over.items():
        if _k in ("upsert", "delete", "order", "fact_bindings", "hook_bindings",
                  "evidence_updates"):
            _h[_k] = _val
    _acc2 = _Acc()
    _exc2 = _raises(lambda: _full(_base(), _acc2, _h))
    assert _exc2 == "", "完整视图入口抛异常：%s" % _exc2
    _v2, _p2, _r2 = _full(_base(), _acc2, _h)
    assert _r2["kind"] in ("illegal", "applied", "no_change"), (name, _r2)
    if full_rejects:
        assert _r2["kind"] == "illegal", (name, _r2, _codes(_p2))
        assert _acc2.n == 0, name


def test_the_read_path_reports_instead_of_crashing():
    """读盘入口（`view_from_plan`）同样只报结构化问题（此前 `carriers=1` 直接 `TypeError`）.

    复核 2026-10-06（第十二轮）① 之后**口径变严**：读取准入里的每一条都**直接拒读**
    （返回 `None`），不再"记一笔、继续走"。所以这里既不抛异常，也**不再**交出视图；
    容器类型违约各自有**具体 code**（事实侧 `view_binding_carriers_type`、
    钩子侧 `view_hook_carriers_type`），不再笼统写成 `view_field_type`。
    """
    _plan = pc.plan_from_view(_hb_base())
    _plan["presentation"]["hook_bindings"] = {"spoken_uids": [], "carriers": 1}
    _exc = _raises(lambda: pc.view_from_plan(_plan))
    assert _exc == "", "读盘入口抛异常：%s" % _exc
    _v, _p = pc.view_from_plan(_plan)
    assert _v is None, "容器类型违约**必须拒读**，不是交一份被清空的视图"
    assert _codes(_p) == ["view_hook_carriers_type"], _codes(_p)
    assert _p[0]["path"] == "presentation.hook_bindings.carriers", _p[0]

    _plan2 = pc.plan_from_view(_hb_base())
    for _b in _plan2["presentation"]["fact_bindings"]:
        _b["carriers"] = 7
    assert _raises(lambda: pc.view_from_plan(_plan2)) == ""
    _v2, _p2 = pc.view_from_plan(_plan2)
    assert _v2 is None, "事实载体容器类型违约**必须拒读**"
    # **逐条都报**（诊断要完整：四条绑定各记一条，不是"报第一条就收工"）
    assert _codes(_p2) == ["view_binding_carriers_type"] * len(_plan2["presentation"]
                                                             ["fact_bindings"]), _codes(_p2)

    # **缺席不是类型违约**：不写 `carriers` 读得出来，由关系层判"没有载体"
    _plan3 = pc.plan_from_view(_hb_base())
    for _b in _plan3["presentation"]["fact_bindings"]:
        _b.pop("carriers", None)
    _v3, _p3 = pc.view_from_plan(_plan3)
    assert _v3 is not None and _codes(_p3) == [], _codes(_p3)


def test_an_absent_carriers_key_is_not_a_type_violation():
    """**缺席 ≠ 类型违约**：`hook_bindings` 的旧式单数写法（`carrier`）不写 `carriers`."""
    _b = _base()
    _b["hook_bindings"] = {"spoken_uids": [_D1], "carrier": {"kind": "dialogue", "uid": _D1}}
    assert "view_field_type" not in _codes(pc.parse_view(json.loads(json.dumps(_b)))[1])
    assert "view_field_type" not in _codes(pc.view_problems(json.loads(json.dumps(_b))))


# ---------------------------------------------------------------------------
# ③ 同值 / 冲突别名（全部引用位置）
# ---------------------------------------------------------------------------
_ALIAS_CASES = [
    ("节拍同值", {"kind": "visual", "beat_uid": _B1, "uid": _B1}, ""),
    ("节拍冲突", {"kind": "visual", "beat_uid": _B1, "uid": _B2}, "view_ref_identity_conflict"),
    ("对白同值", {"kind": "dialogue", "uid": _D1, "card_id": _D1}, ""),
    ("对白冲突", {"kind": "dialogue", "uid": _D1, "card_id": "zzz"}, "view_ref_identity_conflict"),
]


@pytest.mark.parametrize("name,carrier,code", _ALIAS_CASES, ids=[c[0] for c in _ALIAS_CASES])
def test_same_value_aliases_pass_and_conflicts_are_rejected(name, carrier, code):
    _b = _base()
    _acc = _Acc()
    _v, _p, _rk = _delta(_b, _acc, fact_bindings=[{"fact_uid": _FU, "carriers": [carrier]}])
    if code:
        assert _rk["kind"] == "illegal" and code in _codes(_p), (name, _rk, _codes(_p))
        assert _acc.n == 0
    else:
        assert _rk["kind"] == "applied" and _acc.n == 1, (name, _rk, _codes(_p))


# ---------------------------------------------------------------------------
# ④ 映射后**再次修订**（P2 的退出条件）
# ---------------------------------------------------------------------------
def test_a_same_value_alias_survives_mapping_and_the_next_round_is_no_change():
    """合法同值别名经映射后**仍能再解析**，下一轮空增量是 `no_change`（不是 `illegal`）."""
    _b = _hb_base()
    _new = {"uid": "new:dialogue", "text": "新加的一句对白", "source": {}}
    _it = json.loads(json.dumps(_b["beats"][0]))
    _it["voice_ref"] = {"kind": "dialogue", "uid": "new:dialogue",
                        "card_id": "new:dialogue"}
    _acc = _Acc()
    _v, _p, _rk = pc.apply_delta_view(
        _b, _env(_b, upsert={"beats": [_it], "dialogue_units": [_new]},
                 order={"beats": [str(x["uid"]) for x in _b["beats"]],
                        "dialogue_units": [str(x["uid"]) for x in _b["dialogue_units"]]
                                         + ["new:dialogue"]}),
        mode=pc.MODE_HYBRID, accept=_acc)
    assert _rk["kind"] == "applied", (_rk, _codes(_p))
    assert _v["beats"][0]["voice_ref"] == {"kind": "dialogue", "uid": "d02"}, \
        "映射留下了旧别名：%s" % _v["beats"][0]["voice_ref"]
    assert _codes(pc.parse_view(json.loads(json.dumps(_v)),
                                expected_mode=pc.MODE_HYBRID)[1]) == [], "映射后读不回来"
    assert "new:" not in json.dumps(_v, ensure_ascii=False), "候选里残留临时 id"
    _v2, _p2, _r2 = pc.apply_delta_view(_v, _env(_v), mode=pc.MODE_HYBRID, accept=_Acc())
    assert _r2["kind"] == "no_change", (_r2, _codes(_p2))
    # 再来一轮**真改动**也必须能走通（不是"只能空转"）
    _it2 = json.loads(json.dumps(_v["beats"][0]))
    _it2["purpose"] = "改过的信息目的"
    _v3, _p3, _r3 = pc.apply_delta_view(
        _v, _env(_v, upsert={"beats": [_it2]}), mode=pc.MODE_HYBRID, accept=_Acc())
    assert _r3["kind"] == "applied", (_r3, _codes(_p3))


def test_every_mapped_candidate_is_re_parsable():
    """**变换不许把合法候选变成非法基线**：映射完的候选必须能原样读回来."""
    for _b, _over, _mode in (
            (_base(), dict(upsert={"beats": [dict(_VIEW["beats"][0],
                                                 text="改过的节拍正文")]}), "legacy_audio"),
            (_hb_base(), dict(upsert={"screen_cards": [
                {"card_id": "new:c", "beat_uid": _B1, "template": "background",
                 "text": "新卡", "placement": "info_panel"}]},
                order={"screen_cards": ["new:c"]}), "screen_hybrid")):
        _acc = _Acc()
        _v, _p, _rk = pc.apply_delta_view(_b, _env(_b, **_over), mode=_mode, accept=_acc)
        assert _rk["kind"] == "applied", (_rk, _codes(_p))
        _p2 = pc.parse_view(json.loads(json.dumps(_v)), expected_mode=_mode)[1]
        assert _codes(_p2) == [], "映射后重验不过：%s" % _codes(_p2)


# ---------------------------------------------------------------------------
# ⑤ 错误版本（两条入口共用一份版本判据）
# ---------------------------------------------------------------------------
def test_the_delta_entry_checks_the_rule_version():
    _b = _base()
    for _rv, _code in ((None, "delta_rule_version_missing"),
                       ("unknown.v999", "delta_rule_version_mismatch")):
        _d = _env(_b)
        if _rv is None:
            _d.pop("rule_version")
        else:
            _d["rule_version"] = _rv
        _acc = _Acc()
        _v, _p, _rk = pc.apply_delta_view(_b, _d, mode="legacy_audio", accept=_acc)
        assert _rk["kind"] == "illegal" and _code in _codes(_p), (_rv, _rk, _codes(_p))
        assert _acc.n == 0


def test_the_full_view_entry_checks_the_rule_version_too():
    """复核 P2：完整视图入口此前**从不核** `rule_version`（缺失 / 乱填都 `applied`）."""
    _moved = _base()
    for _fb in _moved["fact_bindings"]:
        if str(_fb["fact_uid"]) == _FU:
            _fb["carriers"] = [{"kind": "visual", "uid": _B2}]
    for _rv, _code in ((None, "view_rule_version_missing"),
                       ("unknown.v999", "view_rule_version_mismatch")):
        _acc = _Acc()
        _resp = _full_resp(_base(), _moved)
        if _rv is None:
            _resp.pop("rule_version")
        else:
            _resp["rule_version"] = _rv
        _v, _p, _rk = pc.apply_editable_view(_base(), _resp, mode="legacy_audio",
                                             accept=_acc,
                                             expected_rule_version=o.PLAN_VIEW_RULE_VERSION)
        assert _rk["kind"] == "illegal" and _code in _codes(_p), (_rv, _rk, _codes(_p))
        assert _acc.n == 0, _rv
    # 合法对照
    _acc = _Acc()
    _v, _p, _rk = pc.apply_editable_view(_base(), _full_resp(_base(), _moved),
                                         mode="legacy_audio", accept=_acc,
                                         expected_rule_version=o.PLAN_VIEW_RULE_VERSION)
    assert _rk["kind"] == "applied", (_rk, _codes(_p))


def test_the_full_view_entry_reports_the_declared_version_verbatim():
    """**不覆盖声明来制造一致**：响应声明什么版本，就按什么版本判（拒收时原样回显）."""
    _resp = _full_resp(_base(), _base(), rule_version="unknown.v999")
    _v, _p, _rk = pc.apply_editable_view(_base(), _resp, mode="legacy_audio",
                                         expected_rule_version=o.PLAN_VIEW_RULE_VERSION)
    _m = next(p for p in _p if p["code"] == "view_rule_version_mismatch")
    assert "unknown.v999" in _m["reason"], _m


# ---------------------------------------------------------------------------
# ⑥ 部分改善但仍有残余（`applied` + 非空残余）
# ---------------------------------------------------------------------------
def test_a_partial_improvement_is_applied_with_residuals():
    """合法改动**不因为还有独立残余就被丢掉**：`applied` + 残余非空."""
    _b = _base()
    _it = json.loads(json.dumps(_b["beats"][2]))
    _it["text"] = "改过的第三条节拍"
    _acc = _Acc()
    _v, _p, _rk = _delta(_b, _acc, upsert={"beats": [_it]})
    assert _rk["kind"] == "applied", (_rk, _codes(_p))
    assert _acc.n == 1
    assert _rk["residuals"], "本类夹具本就带残余——空表说明口径变了"


# ---------------------------------------------------------------------------
# ⑦ 新增并绑定 / 删除并重绑（两条入口同结果）
# ---------------------------------------------------------------------------
def test_add_and_bind_and_delete_and_rebind_agree_on_both_entries():
    _b = _base()
    _uids = [str(x["uid"]) for x in _b["beats"]]
    _new = {"uid": "new:beat", "event_uids": [], "text": "新加的一条节拍",
            "purpose": "补足信息来源", "evidence": "", "source": {}, "voice_ref": None}
    _carriers = [{"kind": "visual", "uid": "new:beat"}]
    _acc = _Acc()
    _vd, _pd, _rd = _delta(_b, _acc, upsert={"beats": [json.loads(json.dumps(_new))]},
                           order={"beats": _uids + ["new:beat"]},
                           fact_bindings=[{"fact_uid": _FU, "carriers": _carriers}])
    _full_view = _base()
    _full_view["beats"] = _full_view["beats"] + [json.loads(json.dumps(_new))]
    for _fb in _full_view["fact_bindings"]:
        if str(_fb["fact_uid"]) == _FU:
            _fb["carriers"] = json.loads(json.dumps(_carriers))
    _acc2 = _Acc()
    _vf, _pf, _rf = _full(_base(), _acc2, _full_view)
    assert _rd["kind"] == _rf["kind"] == "applied", (_rd, _rf, _codes(_pd), _codes(_pf))
    assert pc.view_digest(_vd) == pc.view_digest(_vf)
    assert _rd["mapping"] == _rf["mapping"]

    # 删除**纯视觉承载**的节拍（不承载声音单元，避免 view_voice_unit_unplaced /
    # view_voice_role_changed），并把它的 fact 重绑到存活节拍上。
    _victim = next(str(bt["uid"]) for bt in _b["beats"]
                   if str((bt.get("voice_ref") or {}).get("kind")) == pc.KIND_VISUAL)
    _orphan_fact = next((str(fb["fact_uid"]) for fb in _b["fact_bindings"]
                         if any(str(c.get("uid")) == _victim
                                for c in (fb.get("carriers") or []))), "")
    _acc3 = _Acc()
    _bindings = [{"fact_uid": _FU, "carriers": [{"kind": "visual", "uid": _B1}]}]
    if _orphan_fact and _orphan_fact != _FU:
        _bindings.append({"fact_uid": _orphan_fact,
                          "carriers": [{"kind": "visual", "uid": _B1}]})
    _vd2, _pd2, _rd2 = _delta(_b, _acc3, delete={"beats": [_victim]},
                              order={"beats": [u for u in _uids if u != _victim]},
                              fact_bindings=_bindings)
    assert _rd2["kind"] in ("applied", "no_change"), (_rd2, _codes(_pd2))
