# -*- coding: utf-8 -*-
"""B 包 ③：**成功出口验收**——合法增量响应一路走到提交与卡片消费.

复核 2026-10-06（第十轮）③ 的两条要求：

1. 等价矩阵要**逐项固定预期**（`applied` / `no_change` / `illegal` 与具体残余），再加
   「一律拒收」变异——只证明"两条路一致"，在两条路同时退化时照样全绿。那部分落在
   `test_plan_revision_delta_equivalence.py`。
2. 补一份**合法 hybrid 增量响应**，连续经过**生产修订 → 完成候选 → 缓存写读 → 提交 →
   卡片消费**。此前四段证据各自成立，但都在**完整视图**那条路上；增量传输表示需要自己
   的一条贯通。

链里只有文本模型被本地桩替代：请求由生产代码自己装配，桩从**请求录制器**取回真实的
`base_plan_hash` / `fact_basis_hash`（不预先算一份——预算就是"猜基线"，第一版探针正是
这么被拒收三次的：生产那一侧的基线视图与夹具自己算的不是同一份）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import content_verification as cv                                # noqa: E402
from shared import presentation_contract as pc                                # noqa: E402
from step01_script_gen import outline as o                                    # noqa: E402
from tests.shared.test_cache_and_card_handoff import (                        # noqa: E402
    _NOVEL, _first_version_payload, _hybrid_plan, _record_for)
from tests.step01_script_gen.test_plan_phase_e_commit_chain import (          # noqa: E402
    _chapter, _fake_audit, _script_for)

_CARD_TEXT = "这里以后就交给林青了。"
_CARD3_TEXT = "北岸仓库的货位已经清点完毕。"
_VIEW_SCHEMA_NAME = "plan_revision_v2"


class _Res:
    def __init__(self, payload, cid):
        self.success = True
        self.text = json.dumps(payload, ensure_ascii=False)
        self.error = ""
        self.call_id = cid


def _legal_delta(request):
    """一份**合法 hybrid 增量响应**：补正空卡片正文 + 新增一张卡片让 `f03` 变成联合承载.

    目标与基线摘要都取自**这次真实的请求**（`request["view"]` / `request[..._hash]`）。
    """
    _bv = request["view"]
    _c01 = dict(next(c for c in _bv["screen_cards"]
                     if str(c.get("card_id")) == "c01"), text=_CARD_TEXT)
    _new = {"card_id": "new:card3", "beat_uid": "b03", "template": "character",
            "text": _CARD3_TEXT, "placement": "info_panel"}
    return {"schema": pc.DELTA_SCHEMA, "rule_version": o.PLAN_DELTA_RULE_VERSION,
            "base_plan_hash": request["base_plan_hash"],
            "fact_basis_hash": request["fact_basis_hash"],
            "upsert": {"screen_cards": [_c01, _new]},
            "order": {"screen_cards": ["c01", "new:card3"]},
            "fact_bindings": [{"fact_uid": "f03",
                               "carriers": [{"kind": "visual", "uid": "b03"},
                                            {"kind": "screen_card",
                                             "card_id": "new:card3"}]}]}


def _legal_full(request):
    """同一处改动的**完整视图**写法（显式回放 v2 用）——只给整份视图，不给改动块."""
    _bv = json.loads(json.dumps(request["view"]))
    _c01 = dict(next(c for c in _bv["screen_cards"]
                     if str(c.get("card_id")) == "c01"), text=_CARD_TEXT)
    _bv["screen_cards"] = [_c01, {"card_id": "new:card3", "beat_uid": "b03",
                                  "template": "character", "text": _CARD3_TEXT,
                                  "placement": "info_panel"}]
    for _b in _bv["fact_bindings"]:
        if str(_b["fact_uid"]) == "f03":
            _b["carriers"] = [{"kind": "visual", "uid": "b03"},
                              {"kind": "screen_card", "card_id": "new:card3"}]
    return {"schema": pc.REVISION_SCHEMA, "rule_version": o.PLAN_VIEW_RULE_VERSION,
            "base_plan_hash": request["base_plan_hash"],
            "fact_basis_hash": request["fact_basis_hash"], "view": _bv}


def _run_production(monkeypatch, tmp_path, *, resp=_legal_delta,
                    route=o.ROUTE_VIEW_DELTA, cache_name="c.json"):
    """走**生产入口**跑到 `executable` 计划；`resp` 为可调用对象或固定字典（变异用）.

    `route=None` = **不设显式开关**，走生产默认分派；给值则用
    `with_legacy_request_protocol` 显式固定那一条（delta 腿 / 显式 v2 回放都用它）。

    返回 `(第一轮结果, 缓存路径, 请求列表, 调用名列表)`。
    """
    _raw = _first_version_payload(_hybrid_plan())
    _raw["screen_cards"][0]["text"] = ""          # 首版缺陷：卡片正文为空 ⇒ 必须修订
    _norm, _ = o.normalize_hybrid_plan(_raw)
    _ver = _record_for(_norm, novel_digest=cv.plan_digest_of(_norm))
    _seen: dict = {}
    _reqs: list = []
    _calls: list = []
    _real = (o.delta_request if route == o.ROUTE_VIEW_DELTA else o.revision_request)

    def _rec_request(*a, **k):
        _r = _real(*a, **k)
        _seen.clear()
        _seen.update(_r)
        _reqs.append(_r)
        return _r

    monkeypatch.setattr(o, "delta_request" if route == o.ROUTE_VIEW_DELTA
                        else "revision_request", _rec_request)

    class _Svc:
        def generate(self, req):
            _js = getattr(req, "json_schema", None)
            _name = str(_js.get("name") or "") if isinstance(_js, dict) else ""
            _calls.append(_name)
            if "content_verification" in _name or "verif" in _name.lower():
                return _Res(_ver, "v%d" % len(_calls))
            if "delta" in _name:
                return _Res(resp(_seen) if callable(resp) else resp, "d%d" % len(_calls))
            if _VIEW_SCHEMA_NAME in _name:
                return _Res(resp(_seen) if callable(resp) else resp, "r%d" % len(_calls))
            return _Res(_raw, "g%d" % len(_calls))

    monkeypatch.setattr(o, "create_service", lambda *a, **k: _Svc())
    monkeypatch.setenv("STEP01_PRESENTATION_MODE", "screen_hybrid")
    # **本文件测的是既有 delta 那条腿**（合法增量响应 → 提交 → 卡片消费）。生产分派
    # 现在会**先**问内容槽位适配器（规划收口计划 §5.6 第 4 条：本轮问题都能被"内容与
    # 关系选择"表达时走槽位路）——本夹具的首版缺陷正好是槽位能表达的（卡片正文空）。
    # 这里**显式**把适配器让开：传输专项的覆盖不变；适配器自己的贯通用例在
    # `tests/step01_script_gen/test_plan_content_slots.py`，两边不互相冒充。
    monkeypatch.setattr(o, "_content_slots_applicable",
                        lambda *a, **k: (False, "本用例钉的是既有 delta 传输腿", None))
    _cache = tmp_path / cache_name
    if route is None:
        # **不设开关** ⇒ 走生产默认分派（hybrid ⇒ `ROUTE_VIEW`，见 `_replan_route`）
        _r1 = o.generate_outline_result(_NOVEL, cache_path=_cache)
    else:
        with o.with_legacy_request_protocol(route):
            _r1 = o.generate_outline_result(_NOVEL, cache_path=_cache)
    return _r1, _cache, _reqs, _calls


def _bindings_of(plan):
    return {str(b["fact_uid"]): b["carriers"]
            for b in plan["presentation"]["fact_bindings"]}


# ---------------------------------------------------------------------------
# ③-1 生产记录：传输协议 / 规则版本 / 候选视图格式**各取实际分派**
# ---------------------------------------------------------------------------
def test_the_record_names_the_transport_that_was_actually_dispatched(tmp_path, monkeypatch):
    """复核第十轮 P2：增量轮成功之后，记录**不许再冒充 v2**.

    三项分开断言：`transport_protocol`（实际发出）、`rule_version`（本次规则的版本）、
    `view_schema`（候选产物的格式——增量合并出来的**仍是一份完整视图**）。
    """
    _r1, _, _reqs, _calls = _run_production(monkeypatch, tmp_path)
    assert _r1["executable"] is True, _r1.get("failure")
    assert _reqs, "生产入口没有走 delta 请求装配"
    assert "plan_revision_delta_v1" in _calls, _calls
    _pr = _r1["plan"].get("plan_revision") or {}
    assert _pr.get("transport_protocol") == o.ROUTE_VIEW_DELTA, _pr
    assert _pr.get("schema") == o.ROUTE_VIEW_DELTA, _pr
    assert _pr.get("rule_version") == o.PLAN_DELTA_RULE_VERSION, _pr
    assert _pr.get("view_schema") == pc.REVISION_SCHEMA, _pr
    assert _pr.get("view_rule_version") == o.PLAN_VIEW_RULE_VERSION, _pr
    assert _pr.get("mapping", {}).get("new:card3") == "c02", _pr
    # **请求对象内部也不许自相矛盾**：`limits.rule_version` 此前写死 v2，而请求的
    # `rule_version` 是 delta —— 离线矩阵/回放按请求对象读规则版本会读到另一套。
    assert _reqs[0]["limits"]["rule_version"] == o.PLAN_DELTA_RULE_VERSION, _reqs[0]["limits"]
    assert _reqs[0]["rule_version"] == o.PLAN_DELTA_RULE_VERSION, _reqs[0]["rule_version"]
    # 归因那一栏同样按**实际分派**记（响应自报 schema 与期望一致）
    _resp = _r1["revision"]["history"][0]["evidence"]["response"]
    assert _resp["expected"] == pc.DELTA_SCHEMA, _resp
    assert _resp["shape"] == "delta", _resp
    assert _resp["declared_schema"] == pc.DELTA_SCHEMA, _resp


def test_the_hybrid_production_default_is_the_full_view_protocol(tmp_path, monkeypatch):
    """hybrid 的**生产默认**是完整视图（2026-10-11 隔离对照的裁决；不设任何开关）.

    隔离生产链实测：同一模型走增量 **5/5 被拒**、走完整视图 **0 拒收** ⇒ 默认改走
    `ROUTE_VIEW`。这里钉住"不设开关 = 完整视图"，并顺带证明请求与记录都消费它
    （只断言路由器判得对不够——复核之十二的教训）。
    """
    assert o._replan_route([{"code": "carrier_unlabeled"}],
                           mode=pc.MODE_HYBRID) == o.ROUTE_VIEW
    assert o.default_revision_transport(pc.MODE_HYBRID) == o.ROUTE_VIEW
    # **缓存身份跟着换**：传输变了，同一份原文的旧 hybrid 缓存不再可用
    assert o.default_revision_transport(pc.MODE_LEGACY) == o.ROUTE_VIEW_DELTA, \
        "legacy 没有同条件对照读数，不该被一起改掉"
    _r, _, _reqs, _calls = _run_production(monkeypatch, tmp_path, resp=_legal_full,
                                           route=None, cache_name="default.json")
    assert _r["executable"] is True, _r.get("failure")
    assert _VIEW_SCHEMA_NAME in _calls, _calls
    _pr = _r["plan"].get("plan_revision") or {}
    assert _pr.get("transport_protocol") == o.ROUTE_VIEW, _pr
    assert _pr.get("rule_version") == o.PLAN_VIEW_RULE_VERSION, _pr
    assert _reqs[0]["limits"]["rule_version"] == o.PLAN_VIEW_RULE_VERSION, _reqs[0]["limits"]
    _resp = _r["revision"]["history"][0]["evidence"]["response"]
    assert _resp["expected"] == pc.REVISION_SCHEMA, _resp
    assert _resp["shape"] == "view", _resp


def test_an_explicit_v2_replay_records_the_older_protocol(tmp_path, monkeypatch):
    """同一个生产入口，**显式**固定 v2 ⇒ 记录同样是 v2（记录取实际分派，不写死）."""
    _r, _, _reqs, _calls = _run_production(monkeypatch, tmp_path, resp=_legal_full,
                                           route=o.ROUTE_VIEW, cache_name="v2.json")
    assert _r["executable"] is True, _r.get("failure")
    assert _VIEW_SCHEMA_NAME in _calls, _calls
    _h = _r["revision"]["history"][0]
    _pr = _r["plan"].get("plan_revision") or {}
    assert _pr.get("transport_protocol") == o.ROUTE_VIEW, _pr
    assert _pr.get("rule_version") == o.PLAN_VIEW_RULE_VERSION, _pr
    assert _pr.get("view_schema") == pc.REVISION_SCHEMA, _pr
    assert _reqs[0]["limits"]["rule_version"] == o.PLAN_VIEW_RULE_VERSION, _reqs[0]["limits"]
    _resp = _h["evidence"]["response"]
    assert _resp["expected"] == pc.REVISION_SCHEMA, _resp
    assert _resp["shape"] == "view", _resp


# ---------------------------------------------------------------------------
# ③-2 合法 hybrid 增量响应：修订 → 完成候选 → 缓存写读 → 提交 → 卡片消费
# ---------------------------------------------------------------------------
def test_a_legal_hybrid_delta_response_reaches_the_committed_script(tmp_path, monkeypatch):
    """**贯通**：生产修订（增量）→ 完成候选 → 真缓存写/读 → 真提交 → 真消费者."""
    import copy as _copy
    from step04_merge.screen_cards import card_relations_for

    _r1, _cache, _reqs, _ = _run_production(monkeypatch, tmp_path)
    assert _r1["executable"] is True, _r1.get("failure")
    _b1 = _bindings_of(_r1["plan"])
    # 新增的那张卡片经**增量**接成了联合承载（`new:card3` → 正式 `c02`）
    assert {c["kind"] for c in _b1["f03"]} == {"visual", "screen_card"}, _b1
    assert [c["card_id"] for c in _b1["f03"] if c["kind"] == "screen_card"] == ["c02"], _b1
    assert _r1["plan"]["presentation"]["screen_cards"][0]["text"] == _CARD_TEXT

    # **缓存写盘 → 真读回**：关系一个字都不能少
    # （读回这一趟不需要修订，但仍按同一条显式路由跑——否则默认分派与写缓存时不同，
    #  用例会去测另一条腿；命中与否只看缓存键，与协议无关）
    with o.with_legacy_request_protocol(o.ROUTE_VIEW_DELTA):
        _r2 = o.generate_outline_result(_NOVEL, cache_path=_cache)
    assert _r2.get("cache", {}).get("hit") is True, _r2.get("cache")
    assert _r2["executable"] is True, _r2.get("failure")
    assert _bindings_of(_r2["plan"]) == _b1, "缓存读回改动了载体关系"

    # **真消费者**先看缓存读回的那份稿子
    _rel, _mode, _probs = card_relations_for({"chapter_outline": _r2["plan"]})
    assert _probs == [], _probs
    assert _mode == pc.MODE_HYBRID
    assert sorted(r["card_id"] for r in _rel) == ["c01", "c02"], _rel

    # **真提交**：落盘稿必须带着两张卡片与两条联合承载
    import step01_script_gen.main as m
    _chapter(monkeypatch, tmp_path)
    _fake_audit(monkeypatch)
    _plan = _copy.deepcopy(_r2["plan"])
    script = _script_for(_plan)
    script["chapter_outline"] = _copy.deepcopy(_plan)
    _disk, summary = m._commit_step01_outputs(script, _NOVEL, plan=_copy.deepcopy(_plan))
    assert summary["status"] == "COMMITTED", summary
    assert not summary["blocking"], summary["blocking"]
    _committed = json.loads((tmp_path / "script_result.json").read_text(encoding="utf-8"))
    _cp = _committed["chapter_outline"]["presentation"]
    _cb = {str(b["fact_uid"]): b["carriers"] for b in _cp["fact_bindings"]}
    assert {c["kind"] for c in _cb["f03"]} == {"visual", "screen_card"}, _cb
    assert sorted(str(c["card_id"]) for c in _cp["screen_cards"]) == ["c01", "c02"], _cp
    # 提交稿送到消费者手上，关系仍然完整
    _rel2, _m2, _p2 = card_relations_for(_committed)
    assert _p2 == [] and _m2 == pc.MODE_HYBRID
    assert sorted(r["card_id"] for r in _rel2) == ["c01", "c02"], _rel2


def test_the_chain_goes_red_when_the_delta_response_is_not_legal(tmp_path, monkeypatch):
    """**判别性**：把增量响应换成**形状合法但引用悬空**的响应 ⇒ 这条链必须断.

    只证明"合法响应能走通"不够——要证明这条用例真的在判别"合法"。这里让新增卡片挂到
    一个不存在的节拍上（`b09`）：`upsert` 形状没问题，引用闭不上。
    """
    def _bad(request):
        _d = _legal_delta(request)
        _d["upsert"]["screen_cards"][1]["beat_uid"] = "b09"
        return _d

    _r1, _, _reqs, _ = _run_production(monkeypatch, tmp_path, resp=_bad)
    assert _r1["executable"] is False, "悬空引用被放行了"
    assert _r1["failure"]["code"] == "illegal_response", _r1["failure"]
    _h = _r1["revision"]["history"][0]
    assert _h["kind"] == "illegal", _h
    # 生产者记录里带**具体 code 与字段路径**（不是"被拒了"四个字）
    _blob = " ".join(str(p.get("code")) for p in (_h.get("problems_after") or []))         or str(_h.get("detail") or "")
    assert "view_card_beat_dangling" in _blob, _h
    assert "screen_cards[new:card3].beat_uid" in _blob, _h
    # 基线**一个字都没动**（拒收不产生候选：`c02` 不在，`c01` 的正文还是空的）
    _pres = _r1["plan"]["presentation"]
    assert sorted(str(c["card_id"]) for c in _pres["screen_cards"]) == ["c01"], _pres
    assert _pres["screen_cards"][0]["text"] == "", "被拒收的响应改到了基线"
