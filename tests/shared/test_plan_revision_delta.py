# -*- coding: utf-8 -*-
"""R 收口包 B：`plan_revision.delta.v1` —— **按稳定 UID 的增量修订**.

规格：复核 2026-10-06（第五轮）的收口包第 2 条。要钉住的是**传输表示换了、判据没换**：

1. **未提到的条目一律保留**；空增量 = `no_change`（不是失败，也不是"成功"）；
2. **只认显式 uid**——删不存在的、同一 uid 既删又改、`order` 不是排列、`upsert` 给非
   对象（模型退化的典型形态 `[0,1,2]`）一律**整笔拒收、基线不动**；
3. **冻结内容由本地基线保留**（模型不重抄 `frozen_facts` / `frozen_events` /
   `plan_basis`），响应里出现它们就是越界；
4. 合并出来的完整视图走**同一条** `apply_editable_view` 验收链——用同一处改动分别经
   增量与 v2 完整视图提交，结果必须**逐字相同**。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import presentation_contract as pc                                # noqa: E402
from step01_script_gen import outline as o                                    # noqa: E402

# 与 `test_presentation_contract.py` / R0 用**同一份** fixture，避免样本漂移
_SAMPLE = json.loads(
    (_ROOT / "tests" / "step01_script_gen" / "fixtures"
     / "plan_revision_mixed_legal_sample_v1.json").read_text(encoding="utf-8"))
_SRC = _SAMPLE["source"]
_LEGAL, _ = o.apply_replan_ops(_SAMPLE["base_plan"], _SAMPLE["response"], _SRC)
_VIEW, _ = pc.view_from_plan(_LEGAL)
_REQ = pc.delta_request(_VIEW)


def _codes(probs):
    return [p.get("code") for p in probs]


def _delta(**over):
    _d = {"schema": pc.DELTA_SCHEMA, "rule_version": pc.DELTA_RULE_VERSION,
          "base_plan_hash": _REQ["base_plan_hash"],
          "fact_basis_hash": _REQ["fact_basis_hash"]}
    _d.update(over)
    return _d


def _apply(delta, base=None):
    """走真实入口应用一轮（`base` 用来钉"基线不动"与"未提及的保留"）."""
    return pc.apply_delta_view(base if base is not None else _VIEW, delta,
                               mode="legacy_audio",
                               accept=lambda v: o._accept_view_candidate(v, _SRC, None))


def _first_beat_uid():
    return str(_VIEW["beats"][0]["uid"])


# ---------------------------------------------------------------------------
# ① 空增量与"未提到的保留"
# ---------------------------------------------------------------------------
def test_an_empty_delta_is_no_change_not_a_failure():
    """没给任何改动块 ⇒ `no_change`（与 v2 的"原样回带"同一语义），不是错误."""
    _v, _p, _rk = _apply(_delta())
    assert _rk["kind"] == "no_change", _rk
    assert _p == [] and _v is None


def test_unmentioned_entries_are_preserved_verbatim():
    """只改一条绑定 ⇒ 其余每个实体数组**逐字不变**（本地基线保留，模型不用重抄）."""
    _uid = _first_beat_uid()
    _v, _p, _rk = _apply(_delta(upsert={"beats": [
        dict(_VIEW["beats"][0], text=_VIEW["beats"][0]["text"])]}))
    assert _rk["kind"] == "no_change", _rk          # 内容一字未改 ⇒ 同一份视图
    # 真改一处正文 ⇒ applied，且**别的数组逐字相同**
    _new_beat = dict(_VIEW["beats"][0], text=_VIEW["beats"][0]["text"] + "（补一笔）")
    _v2, _p2, _rk2 = _apply(_delta(upsert={"beats": [_new_beat]}))
    assert _rk2["kind"] == "applied", (_rk2, _codes(_p2))
    _back = _v2      # 候选视图本身（投影成计划是调用方 `complete_candidate` 的事）
    assert [_b["uid"] for _b in _back["beats"]] == [_b["uid"] for _b in _VIEW["beats"]]
    assert _back["dialogue_units"] == _VIEW["dialogue_units"], "未提到的数组被改动了"
    assert _back["narration_units"] == _VIEW["narration_units"]
    assert _back["frozen_facts"] == _VIEW["frozen_facts"]


# ---------------------------------------------------------------------------
# ② 结构违约：整笔拒收、基线不动
# ---------------------------------------------------------------------------
def test_upsert_must_carry_complete_objects_not_indices():
    """模型退化的典型形态：用序号顶替对象（`[0,1,2]`）——必须拒收，不按位置猜目标."""
    _v, _p, _rk = _apply(_delta(upsert={"beats": [0, 1, 2]}))
    assert _rk["kind"] == "illegal"
    assert _codes(_p) == ["delta_upsert_item_type"] * 3, _codes(_p)


def test_upsert_items_must_carry_a_uid():
    _v, _p, _rk = _apply(_delta(upsert={"beats": [{"text": "无 uid"}]}))
    assert "delta_upsert_uid_missing" in _codes(_p)


def test_deleting_an_unknown_uid_is_rejected():
    """删除只认**已有**稳定 uid；删不存在的 ⇒ 拒收（不按相似度猜）."""
    _v, _p, _rk = _apply(_delta(delete={"beats": ["b99"]}))
    assert _rk["kind"] == "illegal" and "delta_delete_unknown_uid" in _codes(_p)


def test_the_same_uid_cannot_be_deleted_and_upserted():
    _uid = _first_beat_uid()
    _v, _p, _rk = _apply(_delta(delete={"beats": [_uid]},
                                upsert={"beats": [dict(_VIEW["beats"][0])]}))
    assert _rk["kind"] == "illegal"
    assert "delta_delete_and_upsert_same_uid" in _codes(_p)


def test_order_must_be_a_permutation_of_the_merged_set():
    """顺序块只许重排，不许增删——增删要走 upsert / delete（避免两处同时改集合）."""
    _v, _p, _rk = _apply(_delta(order={"beats": [_first_beat_uid()]}))
    assert _rk["kind"] == "illegal" and "delta_order_not_a_permutation" in _codes(_p)


def test_forbidden_keys_are_rejected():
    """冻结内容与结论字段不许出现在增量响应里——不要求重抄，出现即越界."""
    for _k in ("view", "frozen_facts", "frozen_events", "plan_basis",
               "mode", "event_binding", "passed", "executable", "budget"):
        _v, _p, _rk = _apply(_delta(**{_k: {}}))
        assert "delta_forbidden_key" in _codes(_p), ("" + _k, _codes(_p))


def test_the_base_hash_must_match():
    _d = _delta()
    _d["base_plan_hash"] = "0" * 64
    _v, _p, _rk = _apply(_d)
    assert _rk["kind"] == "illegal" and "delta_base_hash_mismatch" in _codes(_p)
    _d2 = _delta()
    _d2["fact_basis_hash"] = "0" * 64
    assert "delta_fact_basis_hash_mismatch" in _codes(_apply(_d2)[1])


def test_an_unknown_delta_schema_is_rejected():
    _d = _delta()
    _d["schema"] = "plan_revision.v2"
    assert "delta_response_schema_unknown" in _codes(_apply(_d)[1])


def test_an_unknown_array_name_is_rejected():
    _v, _p, _rk = _apply(_delta(upsert={"shot_beats": [{"uid": "b01"}]}))
    assert "delta_block_unknown_key" in _codes(_p)


# ---------------------------------------------------------------------------
# ③ 语义：增改 / 删除 / 重排 / 只补出处
# ---------------------------------------------------------------------------
def test_upsert_replaces_by_uid_and_appends_new_ones():
    _uid = _first_beat_uid()
    _base_uids = [str(b["uid"]) for b in _VIEW["beats"]]
    _edited = dict(_VIEW["beats"][0], text=_VIEW["beats"][0]["text"] + "，补一笔")
    _added = {"uid": "new:1", "event_uids": list(_VIEW["beats"][0].get("event_uids") or []),
              "text": "新加的一条节拍", "purpose": "补足信息来源",
              "evidence": _VIEW["beats"][0].get("evidence") or "",
              "source": {}, "voice_ref": None}
    _v, _p, _rk = _apply(_delta(upsert={"beats": [_edited, _added]},
                                order={"beats": _base_uids + ["new:1"]}))
    assert _rk["kind"] == "applied", (_rk, _codes(_p))
    assert _rk["mapping"], "新增实体必须由代码分配正式 uid（映射不许为空）"
    _uids = [str(b["uid"]) for b in _v["beats"]]
    assert len(_uids) == len(_VIEW["beats"]) + 1, _uids
    assert _uids[-1] == _rk["mapping"]["new:1"] != "new:1", "临时 uid 必须被换成正式 uid"
    assert _uids[0] == _uid, "已有实体的 uid 必须原样保留"


def test_order_is_required_when_the_set_changes_and_is_respected_when_given():
    """复核规格：「新增、删除或重排集合 ⇒ 给出最终完整 UID 顺序」；只改正文可省略."""
    _base_uids = [str(b["uid"]) for b in _VIEW["beats"]]
    _a = {"uid": "new:1", "event_uids": [], "text": "甲", "purpose": "p",
          "evidence": "", "source": {}, "voice_ref": None}
    # ① 集合变了却不给 order ⇒ 拒收（新条目排第几不作隐式约定）
    _v0, _p0, _r0 = _apply(_delta(upsert={"beats": [_a]}))
    assert _r0["kind"] == "illegal" and "delta_order_required" in _codes(_p0), _codes(_p0)
    # ② 给了 order ⇒ 按它排，临时 uid 换成正式 uid（断言经**映射**比，不假定同名）
    _v, _p, _rk = _apply(_delta(upsert={"beats": [_a]},
                                order={"beats": ["new:1"] + _base_uids}))
    assert _rk["kind"] == "applied", (_rk, _codes(_p))
    assert [str(b["uid"]) for b in _v["beats"]] == [_rk["mapping"]["new:1"]] + _base_uids
    # ③ 只改正文、不动集合 ⇒ 顺序可省略，原顺序保留
    _v2, _p2, _r2 = _apply(_delta(upsert={"beats": [
        dict(_VIEW["beats"][0], text=_VIEW["beats"][0]["text"] + "，只改正文")]}))
    assert _r2["kind"] == "applied", (_r2, _codes(_p2))
    assert [str(b["uid"]) for b in _v2["beats"]] == _base_uids


def test_an_unknown_formal_uid_is_rejected_not_treated_as_new():
    """复核规格：「不存在的正式 UID ⇒ 拒收，不能当新增」.

    打错的 uid 若静默变新增，会把「改这一条」变成「多出一条」——那是**内容级事故**，
    不是形状问题，所以必须在这一层堵住，而不是等验收去数条数。
    """
    _v, _p, _rk = _apply(_delta(upsert={"beats": [dict(_VIEW["beats"][0], uid="b09")]},
                                order={"beats": ["b09"]}))
    assert _rk["kind"] == "illegal", _rk
    assert "delta_upsert_uid_unknown" in _codes(_p), _codes(_p)


def test_new_entries_must_use_the_declared_temporary_uid_prefix():
    """临时 uid 必须是**显式**的（`new:` 开头），不是「凡不在基线里就算新」."""
    _v, _p, _rk = _apply(_delta(upsert={"beats": [
        {"uid": "随便起的名字", "event_uids": [], "text": "甲", "purpose": "p",
         "evidence": "", "source": {}, "voice_ref": None}]}))
    assert _rk["kind"] == "illegal"
    assert "delta_upsert_uid_unknown" in _codes(_p), _codes(_p)


def test_fact_binding_order_carries_no_meaning():
    """`fact_bindings` 的**排列**不携带含义（R1 的规范不变式）：换个书写顺序 ⇒ 同一份结果.

    ⚠️ 边界：`upsert` 里**新增条目**的书写顺序**是**有含义的——它决定呈现顺序（B/D/N
    编号），那是模型要做的真实选择；要改顺序请用 `order` 块。所以这里只钉绑定表的排列。
    """
    _f = [{"fact_uid": str(b["fact_uid"]),
           "carriers": [{"kind": "visual", "uid": _first_beat_uid()}]}
          for b in _VIEW["fact_bindings"]]
    _v1, _p1, _r1 = _apply(_delta(fact_bindings=_f))
    _v2, _p2, _r2 = _apply(_delta(fact_bindings=list(reversed(_f))))
    assert _r1["kind"] == _r2["kind"] == "applied", (_codes(_p1), _codes(_p2))
    assert pc.view_digest(_v1) == pc.view_digest(_v2), "换了书写顺序却算出不同的视图"


def test_fact_bindings_only_replace_the_listed_ones():
    _target = str(_VIEW["fact_bindings"][0]["fact_uid"])
    _rest = {str(b["fact_uid"]): b["carriers"]
             for b in _VIEW["fact_bindings"] if str(b["fact_uid"]) != _target}
    _v, _p, _rk = _apply(_delta(fact_bindings=[
        {"fact_uid": _target, "carriers": [{"kind": "visual", "uid": _first_beat_uid()}]}]))
    assert _rk["kind"] == "applied", (_rk, _codes(_p))
    _back = _v          # `apply_delta_view` 与 `apply_editable_view` 一样返回**候选视图**
    _now = {str(b["fact_uid"]): b["carriers"] for b in _back["fact_bindings"]}
    assert _now[_target] == [{"kind": "visual", "uid": _first_beat_uid()}]
    for _fu, _car in _rest.items():
        assert _now[_fu] == _car, "没列出来的事实绑定被改动了：%s" % _fu


def test_evidence_updates_alone_are_a_legal_change():
    """只补出处也是**有效改动**：断言**具体结果**与**保留内容**，不是"三种结果都算过".

    复核 2026-10-06：旧版把 `applied` / `no_change` / `illegal` 全放行，等于什么都没证明。
    这里钉住：改了出处 ⇒ `applied`；改后的记录在结果里；**未提及的修正不丢**。
    """
    _fu0 = str(_VIEW["frozen_facts"][0]["uid"])
    _fu1 = str(_VIEW["frozen_facts"][1]["uid"])
    _src0 = _VIEW["frozen_facts"][0].get("evidence") or ""
    _src1 = _VIEW["frozen_facts"][1].get("evidence") or ""
    # 先给 f0 建一条修正 ⇒ 得到一份**带出处修正**的新基线
    _v1, _p1, _r1 = _apply(_delta(evidence_updates=[{"fact_uid": _fu0, "evidence": _src0}]))
    assert _r1["kind"] == "applied", (_r1, _codes(_p1))
    _base1 = _v1
    assert [str(e["fact_uid"]) for e in _base1["evidence_updates"]] == [_fu0]
    # 再只补 f1 的修正 ⇒ **f0 那条必须还在**（按事实身份增改，不是整表替换）
    _v2, _p2, _r2 = _apply(_delta_on(_base1, evidence_updates=[
        {"fact_uid": _fu1, "evidence": _src1}]), base=_base1)
    assert _r2["kind"] == "applied", (_r2, _codes(_p2))
    _got = {str(e["fact_uid"]): e["evidence"] for e in _v2["evidence_updates"]}
    assert set(_got) == {_fu0, _fu1}, "未提及的出处修正被丢掉了：%s" % sorted(_got)
    assert _got[_fu0] == _src0 and _got[_fu1] == _src1


def test_a_partial_improvement_is_applied_with_residuals():
    """增量应用器须透传验收器残余；本例显式注入诊断，不模拟语义判定。

    旧例把声画共镜当非法；2026-10-18 的契约已允许该组合。
    """
    residual = {"code": "semantic_review_pending", "entity_type": "beat",
                "entity_id": _first_beat_uid(), "field_path": "beats[0].text"}
    changed = dict(_VIEW["beats"][0], text=_VIEW["beats"][0]["text"] + "（补一笔）")
    _v, _p, _rk = pc.apply_delta_view(
        _VIEW, _delta(upsert={"beats": [changed]}), mode="legacy_audio",
        accept=lambda view: [dict(residual)])
    assert not _p and _v is not None and _rk["kind"] == "applied"
    assert _rk["residuals"] == [residual]


def test_the_legacy_multi_carrier_rule_is_shared_not_reimplemented():
    """增量层**不自带**判据：旧模式多载体是 §46 那条共用判据拦下的."""
    _v, _p, _rk = _apply(_delta(fact_bindings=[
        {"fact_uid": str(_VIEW["fact_bindings"][0]["fact_uid"]),
         "carriers": [{"kind": "visual", "uid": _first_beat_uid()},
                      {"kind": "narration",
                       "uid": str(_VIEW["narration_units"][0]["uid"])}]}]))
    assert _rk["kind"] == "illegal"
    assert "view_binding_multi_carrier_legacy" in _codes(_p), _codes(_p)


# ---------------------------------------------------------------------------
# ④ 换的只是传输表示：增量与 v2 完整视图必须**同结果**
# ---------------------------------------------------------------------------
def _full_view_response(view):
    return {"schema": pc.REVISION_SCHEMA, "rule_version": pc.DELTA_RULE_VERSION,
            "base_plan_hash": _REQ["base_plan_hash"],
            "fact_basis_hash": _REQ["fact_basis_hash"], "view": view}


def test_the_delta_path_and_the_full_view_path_agree():
    """同一处改动，增量提交与 v2 完整视图提交必须**逐字同结果**（统一验收不许分叉）."""
    _edited = dict(_VIEW["beats"][0], text=_VIEW["beats"][0]["text"] + "，补一笔")
    _f1 = {"fact_uid": str(_VIEW["fact_bindings"][0]["fact_uid"]),
           "carriers": [{"kind": "visual", "uid": _first_beat_uid()}]}

    _via_delta, _pd, _rd = _apply(_delta(upsert={"beats": [_edited]},
                                         fact_bindings=[_f1]))
    # 同改动写成"完整视图"：基线视图 + 同一处编辑
    _full = json.loads(json.dumps(_VIEW))
    _full["beats"][0] = _edited
    for _b in _full["fact_bindings"]:
        if str(_b["fact_uid"]) == _f1["fact_uid"]:
            _b["carriers"] = _f1["carriers"]
    _via_full, _pf, _rf = pc.apply_editable_view(
        _VIEW, _full_view_response(_full), mode="legacy_audio",
        accept=lambda v: o._accept_view_candidate(v, _SRC, None))

    assert _rd["kind"] == _rf["kind"] == "applied", (_codes(_pd), _codes(_pf))
    assert pc.view_digest(_via_delta) == pc.view_digest(_via_full), \
        "同一处改动经两条传输表示算出了不同的计划"
    assert len(_rd["residuals"]) == len(_rf["residuals"])


# ---------------------------------------------------------------------------
# ⑤ 复核 2026-10-06（第六轮）的三类 P1
# ---------------------------------------------------------------------------
def _delta_on(base, **over):
    """按**给定基线**算摘要的增量信封（比"永远对 `_VIEW`"更能钉住基线不动）."""
    _req = pc.delta_request(base)
    _d = {"schema": pc.DELTA_SCHEMA, "rule_version": pc.DELTA_RULE_VERSION,
          "base_plan_hash": _req["base_plan_hash"],
          "fact_basis_hash": _req["fact_basis_hash"]}
    _d.update(over)
    return _d


def _hybrid_base():
    """一份**合法**的 hybrid 视图（无旁白、事件已声明、带一张卡片）——用来钉身份键."""
    _h = json.loads(json.dumps(_VIEW))
    _h.update(mode=pc.MODE_HYBRID, event_binding="declared", narration_units=[])
    _ev = [str(e) for e in _h["frozen_events"]]
    for _i, _b in enumerate(_h["beats"]):
        _b["event_uids"] = list(_ev) if _i == 0 else list(_b["event_uids"] or [_ev[-1]])
        if (_b.get("voice_ref") or {}).get("kind") == pc.KIND_NARRATION:
            _b["voice_ref"] = None
    _h["screen_cards"] = [{"card_id": "c01", "beat_uid": str(_h["beats"][2]["uid"]),
                           "template": "background", "text": "北岸仓库有三十七个货位",
                           "placement": "info_panel"}]
    for _fb in _h["fact_bindings"]:
        _fb["carriers"] = [{"kind": "visual", "uid": str(_h["beats"][2]["uid"])}]
    assert pc.view_problems(_h, base_view=_h) == [], pc.view_problems(_h, base_view=_h)
    return _h


def test_all_four_collections_use_their_own_identity_key():
    """复核 P1①：卡片读 `card_id`，其余读 `uid`——统一读 `uid` 会把合法卡片判成缺 uid."""
    _h = _hybrid_base()
    _card = dict(_h["screen_cards"][0], text="改了卡片正文")
    for _tag, _blk in (("card_id", {"screen_cards": [_card]}),
                       ("uid 别名", {"screen_cards": [dict(_card, uid="c01")]})):
        _v, _p, _rk = pc.apply_delta_view(_h, _delta_on(_h, upsert=_blk), mode=pc.MODE_HYBRID)
        assert _rk["kind"] == "applied", (_tag, _codes(_p))
    _v, _p, _rk = pc.apply_delta_view(
        _h, _delta_on(_h, upsert={"screen_cards": [{"text": "没身份"}]}), mode=pc.MODE_HYBRID)
    assert _rk["kind"] == "illegal" and "delta_upsert_uid_missing" in _codes(_p)
    _v, _p, _rk = pc.apply_delta_view(
        _h, _delta_on(_h, delete={"screen_cards": ["c01"]}, order={"screen_cards": []}),
        mode=pc.MODE_HYBRID)
    assert _rk["kind"] == "applied", _codes(_p)


def test_upsert_is_a_whole_replacement_so_required_keys_are_checked_first():
    """复核 P1①：`{"uid":"b01"}` 会把原节拍正文**整体替换掉**——必须在替换前拒收."""
    _v, _p, _rk = _apply(_delta(upsert={"beats": [{"uid": _first_beat_uid()}]}))
    assert _rk["kind"] == "illegal", _rk
    # 缺必填字段由**共用实体契约**判（不再是"基线键交集"），code 具体到字段
    assert "entity_field_missing" in _codes(_p), _codes(_p)
    assert any(str(x.get("path") or "").startswith("upsert.beats[0].") for x in _p), _codes(_p)
    _v2, _p2, _r2 = _apply(_delta(upsert={"beats": [
        dict(_VIEW["beats"][0], text=_VIEW["beats"][0]["text"] + "，改正文")]}))
    assert _r2["kind"] == "applied", (_r2, _codes(_p2))


def test_duplicate_fact_uid_is_rejected_before_any_merge():
    """复核 P1②：同一 fact_uid 两项**先前是「后者覆盖前者」**——关系信息在验证前就被消灭."""
    _fu = str(_VIEW["fact_bindings"][0]["fact_uid"])
    _v, _p, _rk = _apply(_delta(fact_bindings=[
        {"fact_uid": _fu, "carriers": [{"kind": "visual", "uid": _first_beat_uid()}]},
        {"fact_uid": _fu, "carriers": [{"kind": "visual",
                                        "uid": str(_VIEW["beats"][2]["uid"])}]}]))
    assert _rk["kind"] == "illegal", _rk
    assert "delta_binding_duplicate" in _codes(_p), _codes(_p)


def test_evidence_delete_is_explicit_and_only_removes_existing_records():
    """复核 P1②：撤销修正要**显式**；只删已有修正记录，不删主张/基线出处；未知撤销拒收."""
    _fu = str(_VIEW["frozen_facts"][0]["uid"])
    _v, _p, _rk = _apply(_delta(evidence_delete=[_fu]))
    assert _rk["kind"] == "illegal"
    assert "delta_evidence_delete_unknown" in _codes(_p), _codes(_p)


def test_hook_bindings_must_be_a_complete_relation():
    """复核 P1②：`hook_bindings={}` 不能当清空——清空要显式给完整空关系."""
    _v, _p, _rk = _apply(_delta(hook_bindings={}))
    assert _rk["kind"] == "illegal", _rk
    assert "delta_hook_bindings_incomplete" in _codes(_p), _codes(_p)
    _v2, _p2, _r2 = _apply(_delta(hook_bindings={"spoken_uids": [], "carriers": []}))
    assert _r2["kind"] in ("applied", "no_change"), (_r2, _codes(_p2))


def test_carriers_items_are_type_checked_instead_of_crashing():
    """复核 P1③：`carriers=[1]` 此前抛 `AttributeError`——形状违约必须**结构化拒收**."""
    _fu = str(_VIEW["fact_bindings"][0]["fact_uid"])
    _v, _p, _rk = _apply(_delta(fact_bindings=[{"fact_uid": _fu, "carriers": [1]}]))
    assert _rk["kind"] == "illegal"
    assert "delta_binding_carrier_item_type" in _codes(_p), _codes(_p)


def test_rule_version_is_checked_against_the_request():
    """复核 P1③：规则版本要与**本次请求的期望值**核对，缺了也不许放过."""
    _d = _delta()
    _d["rule_version"] = "别的版本"
    _v, _p, _rk = _apply(_d)
    assert _rk["kind"] == "illegal" and "delta_rule_version_mismatch" in _codes(_p)
    _d2 = _delta()
    _d2.pop("rule_version")
    assert "delta_rule_version_missing" in _codes(_apply(_d2)[1])


def test_unknown_top_level_keys_are_rejected():
    """复核 P1③：拼错的顶层键（如 `fact_binding`）不许静默忽略——那等于改动被丢掉."""
    _v, _p, _rk = _apply(_delta(fact_binding=[]))
    assert _rk["kind"] == "illegal"
    assert "delta_unknown_top_key" in _codes(_p), _codes(_p)


# ---------------------------------------------------------------------------
# ⑥ 复核 2026-10-06（第七轮）：共用实体契约 / 身份规范化 / 摘要规范化
# ---------------------------------------------------------------------------
def _hybrid_with_card(card_id="c05", extra=None):
    """合法 hybrid 视图 + 一张**指定 id** 的卡片（用来钉身份不与 `ref_uid` 分叉）."""
    _h = json.loads(json.dumps(_VIEW))
    _h.update(mode=pc.MODE_HYBRID, event_binding="declared", narration_units=[])
    _ev = [str(e) for e in _h["frozen_events"]]
    for _i, _b in enumerate(_h["beats"]):
        _b["event_uids"] = list(_ev) if _i == 0 else list(_b["event_uids"] or [_ev[-1]])
        if (_b.get("voice_ref") or {}).get("kind") == pc.KIND_NARRATION:
            _b["voice_ref"] = None
    _card = {"card_id": card_id, "beat_uid": str(_h["beats"][2]["uid"]),
             "template": "background", "text": "北岸仓库有三十七个货位",
             "placement": "info_panel"}
    _card.update(extra or {})
    _h["screen_cards"] = [_card]
    for _fb in _h["fact_bindings"]:
        _fb["carriers"] = [{"kind": "visual", "uid": str(_h["beats"][2]["uid"])}]
    return _h


def test_entity_field_types_are_checked_for_every_collection():
    """复核 P1①：`text` 给 `null` / 数字 / 数组 / 对象**此前全部被接受**.

    空字符串不算违约（内容裁决交既有验收），但**类型**违约必须在契约层拒收。
    """
    for _coll in ("beats", "dialogue_units", "narration_units"):
        for _bad in (None, 7, ["x"], {"k": 1}):
            _it = dict(_VIEW[_coll][0])
            _it["text"] = _bad
            _v, _p, _rk = _apply(_delta(upsert={_coll: [_it]}))
            assert _rk["kind"] == "illegal", (_coll, _bad, _rk)
            assert "entity_field_type" in _codes(_p), (_coll, _bad, _codes(_p))
        # 合法对照：空字符串**不是**类型违约（内容裁决另算）
        _ok = dict(_VIEW[_coll][0], text="")
        _v, _p, _rk = _apply(_delta(upsert={_coll: [_ok]}))
        assert "entity_field_type" not in _codes(_p), _codes(_p)


def test_nested_reference_lists_are_type_checked():
    """复核 P1①：`event_uids=[{}]` 此前被接受——嵌套引用的**元素类型**也要核."""
    _it = dict(_VIEW["beats"][0], event_uids=[{}])
    _v, _p, _rk = _apply(_delta(upsert={"beats": [_it]}))
    assert _rk["kind"] == "illegal" and "entity_field_type" in _codes(_p), _codes(_p)


def test_card_identity_alias_must_agree_or_be_rejected():
    """复核 P1②：`entity_identity` 优先 `card_id` 而 `ref_uid` 优先 `uid`，两条读法分叉 ⇒
    已有卡片 `c05` 同时给 `card_id`+`uid` 会被**重新编号**成 `c01`。同值别名才接受。"""
    _h = _hybrid_with_card("c05")
    _conflict = dict(_h["screen_cards"][0], uid="c99")
    _v, _p, _rk = pc.apply_delta_view(
        _h, _delta_on(_h, upsert={"screen_cards": [_conflict]}), mode=pc.MODE_HYBRID)
    assert _rk["kind"] == "illegal", _rk
    assert "entity_identity_alias_conflict" in _codes(_p), _codes(_p)
    # 合法对照：**同值**别名 ⇒ 接受，且身份保持 c05（不重新编号）
    _same = dict(_h["screen_cards"][0], uid="c05", text="改了卡片正文")
    _v2, _p2, _r2 = pc.apply_delta_view(
        _h, _delta_on(_h, upsert={"screen_cards": [_same]}), mode=pc.MODE_HYBRID)
    assert _r2["kind"] == "applied", (_r2, _codes(_p2))
    _ids = [str(c.get("card_id")) for c in _v2["screen_cards"]]
    assert _ids == ["c05"], "已有卡片的身份被重新分配了：%s" % _ids


def test_evidence_update_order_is_not_a_content_change():
    """复核 P2③：同两条出处修正**交换顺序**此前摘要不同、判 `applied` ⇒ 循环/无变化判定
    会把排列变化当成改善。出处按**冻结事实顺序**输出后，换序不改变摘要。"""
    _fu = [str(f["uid"]) for f in _VIEW["frozen_facts"][:2]]
    _mk = lambda u: {"fact_uid": u, "evidence": "X"}
    _a = _apply(_delta(evidence_updates=[_mk(_fu[0]), _mk(_fu[1])]))
    _b = _apply(_delta(evidence_updates=[_mk(_fu[1]), _mk(_fu[0])]))
    assert _a[2]["kind"] == _b[2]["kind"] == "applied", (_a[2], _b[2])
    assert pc.view_digest(_a[0]) == pc.view_digest(_b[0]), \
        "换序仍被当成内容改变——循环与 no_change 判定会被排列变化骗过"


def test_explicit_null_is_not_the_same_as_omitted():
    """复核 P2④：`hook_bindings: null` 此前被跳过检查、合并后记 `no_change`.

    省略 = 保留；完整空关系 = 清空；`null` 不是任何一种 ⇒ 结构化拒收。
    """
    _v, _p, _rk = _apply(_delta(hook_bindings=None))
    assert _rk["kind"] == "illegal", _rk
    assert "delta_hook_bindings_null" in _codes(_p), _codes(_p)
    # 合法对照：**省略**该键 = 保留（不是错误）
    assert _apply(_delta())[2]["kind"] == "no_change"


def test_the_full_view_entry_rejects_bad_evidence_records_structurally():
    """复核 P1①：完整视图的 `evidence_updates=[1]` 此前抛 `AttributeError`.

    两个入口共用同一份契约 ⇒ 都要**结构化拒收**，都不许抛异常。
    """
    _bad = json.loads(json.dumps(_VIEW))
    _bad["evidence_updates"] = [1]
    _resp = {"schema": pc.REVISION_SCHEMA, "rule_version": "x",
             "base_plan_hash": _REQ["base_plan_hash"],
             "fact_basis_hash": _REQ["fact_basis_hash"], "view": _bad}
    _v, _p, _rk = pc.apply_editable_view(_VIEW, _resp, mode="legacy_audio")
    assert _rk["kind"] == "illegal", _rk
    assert "view_evidence_item_type" in _codes(_p), _codes(_p)


def test_the_same_entity_contract_guards_both_entry_points():
    """两个入口**共用**同一份实体契约：完整视图里的错类型同样被拒."""
    _bad = json.loads(json.dumps(_VIEW))
    _bad["beats"][0]["text"] = None
    _resp = {"schema": pc.REVISION_SCHEMA, "rule_version": "x",
             "base_plan_hash": _REQ["base_plan_hash"],
             "fact_basis_hash": _REQ["fact_basis_hash"], "view": _bad}
    _v, _p, _rk = pc.apply_editable_view(_VIEW, _resp, mode="legacy_audio")
    assert _rk["kind"] == "illegal", _rk
    assert "entity_field_type" in _codes(_p), _codes(_p)


def test_the_rejection_stage_is_recorded_so_a_replay_knows_its_entry():
    """**拒收阶段可辨**（2026-10-11 逐笔诊断）：同一条 `illegal` 来自校验器还是应用器.

    实测两轮真实拒收分属两个阶段（`delta_upsert_item_type` 来自响应校验器、
    `delta_delete_and_upsert_same_uid` 来自应用器）；旧写法只留 `detail`，回放时
    **用错入口就会得到空表**，看起来像"复现不出"。
    """
    # ① 响应校验器阶段：`upsert.beats[0]` 是序号占位（不是对象）
    _v, _p, _rk = _apply({"schema": pc.DELTA_SCHEMA, "rule_version": pc.DELTA_RULE_VERSION,
                          "base_plan_hash": _REQ["base_plan_hash"],
                          "fact_basis_hash": _REQ["fact_basis_hash"],
                          "upsert": {"beats": [1]}})
    assert _rk["kind"] == "illegal" and _rk["stage"] == "response", _rk
    assert "delta_upsert_item_type" in _codes(_p)

    # ② 应用器阶段：同 id 既删又改
    _uid = _first_beat_uid()
    _v2, _p2, _rk2 = _apply(_delta(delete={"beats": [_uid]},
                                   upsert={"beats": [dict(_VIEW["beats"][0])]}))
    assert _rk2["kind"] == "illegal" and _rk2["stage"] == "apply", _rk2
    assert "delta_delete_and_upsert_same_uid" in _codes(_p2)

    # ③ 合法轮不记阶段（不是什么都要有）
    _v3, _p3, _rk3 = _apply(_delta(order={"beats": [_first_beat_uid()]}))
    assert _rk3["kind"] == "illegal" and _rk3.get("stage") == "apply", _rk3
