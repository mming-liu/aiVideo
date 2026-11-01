# -*- coding: utf-8 -*-
"""R2：完整关系事务应用器（`apply_editable_view`）+ `plan_revision.v2` 请求/响应 codec.

规格：`docs/aiVideo-step01-planning-closure-2026-10-04.md` **R2**。

钉住的判据（R2 明文）：

1. **应用顺序固定**：严格解析 → 核基线 → 构造候选关系 → 分配 ID/派生映射 → 核冻结事实
   与事件 → 核来源及结构 → 收集残余 → 控制器决定。任一步失败都返回**结构化问题**。
2. **`illegal` 只表示形状、基线、引用或保护边界违约**；合法候选即使仍有预算/内容残余
   也记 `applied` + 残余——不能因为"改了一项却还有独立基线问题"丢掉有效的局部修正，
   也不能接收破坏冻结事实的"改善"。
3. **已有实体保留 uid；新增临时 uid 由代码一次分配**并统一改写全部引用。
4. **同摘要 = `no_change`**（代码比较，不接受自报）。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared import presentation_contract as pc                               # noqa: E402

_SAMPLE = json.loads(
    (_ROOT / "tests" / "step01_script_gen" / "fixtures"
     / "plan_revision_mixed_legal_sample_v1.json").read_text(encoding="utf-8"))
from step01_script_gen import outline as o                                   # noqa: E402

_LEGAL, _ = o.apply_replan_ops(_SAMPLE["base_plan"], _SAMPLE["response"], _SAMPLE["source"])
_BASE, _BP = pc.view_from_plan(_LEGAL)


def _resp(view, base=None):
    """按协议给一份"回带同一份视图"的合法信封."""
    _b = base if base is not None else _BASE
    return {"schema": pc.REVISION_SCHEMA, "rule_version": "plan_revision.v2.1",
            "base_plan_hash": pc.view_digest(_b),
            "fact_basis_hash": pc.fact_basis_digest(_b), "view": view}


def _codes(probs):
    return [p["code"] for p in probs]


def _apply(view, *, accept=None, base=None):
    return pc.apply_editable_view(base or _BASE, _resp(view, base),
                                  accept=accept if accept is not None else (lambda v: []))


# ---------------------------------------------------------------------------
# ① 三态：no_change / applied(+残余) / illegal
# ---------------------------------------------------------------------------
def test_identical_view_is_no_change_not_applied():
    _v, _p, _r = _apply(copy.deepcopy(_BASE))
    assert _v is None and _r["kind"] == "no_change", _r
    assert "摘要一致" in _r["detail"]


def test_legal_candidate_with_residuals_is_applied_and_reports_them():
    """合法候选仍有残余 ⇒ `applied` + 残余，**不是** `illegal`、也不是丢弃（R2 明文）."""
    _v = copy.deepcopy(_BASE)
    _v["beats"][0]["text"] = "周明把辞呈摁在桌面上"
    _resid = [{"code": "budget_infeasible", "kind": "plan", "index": None, "path": "",
               "reason": "发声下界 90s 超过上限", "basis": "预算", "capability": "content"}]
    _cv, _p, _r = _apply(_v, accept=lambda _x: _resid)
    assert _r["kind"] == "applied", _r
    assert _cv is not None and _r["residuals"] == _resid
    assert _r["detail"] == ""      # 应用器自己不下"通过/不通过"的结论


def test_illegal_shapes_are_structured_and_carry_producer_reason():
    _cases = {
        "view_response_schema_unknown": {"schema": "nope"},
        "view_response_field_missing": {"base_plan_hash": "x"},
        "view_response_self_verdict": {"passed": True},
    }
    for _want, _drop, _over in (
            ("view_response_schema_unknown", None, {"schema": "nope"}),
            ("view_response_field_missing", "fact_basis_hash", {}),
            ("view_response_self_verdict", None, {"passed": True}),
    ):
        _r = copy.deepcopy(_resp(copy.deepcopy(_BASE)))
        if _drop:
            # **真的删掉**这个键才是"缺字段"；只改它的值是"值不对"，那是另一条 code
            _r.pop(_drop, None)
        _r.update(_over)
        _cv, _p, _res = pc.apply_editable_view(_BASE, _r)
        assert _cv is None and _res["kind"] == "illegal", _want
        assert _res["detail"], "拒收必须给出生产者原话"
        assert _want in _codes(_p), "%s -> %s" % (_want, _codes(_p))
    # 对照：`base_plan_hash` **值**不对（不是缺字段）⇒ base_hash_mismatch
    _r = _resp(copy.deepcopy(_BASE))
    _r["base_plan_hash"] = "0" * 64
    _cv, _p, _res = pc.apply_editable_view(_BASE, _r)
    assert "view_base_hash_mismatch" in _codes(_p), _codes(_p)


def test_base_hash_mismatch_is_rejected():
    """响应带的基线摘要与本地不符 ⇒ 拒收（改的不是同一份基线）."""
    _r = _resp(copy.deepcopy(_BASE))
    _r["base_plan_hash"] = "0" * 64
    _cv, _p, _res = pc.apply_editable_view(_BASE, _r)
    assert _cv is None and "view_base_hash_mismatch" in _codes(_p), _codes(_p)


def test_frozen_fact_claims_cannot_be_rewritten():
    """**正文主张**冻结：响应里改 `claim` ⇒ 拒收（证据更正走 `evidence_updates`）."""
    _v = copy.deepcopy(_BASE)
    _v["frozen_facts"][0]["claim"] = "被改过的主张"
    _cv, _p, _res = _apply(_v)
    assert _cv is None and "view_frozen_facts_changed" in _codes(_p), _codes(_p)
    assert "正文主张" in _res["detail"]


def test_frozen_event_set_cannot_be_dropped_or_reordered():
    """事件集合与顺序冻结."""
    _v = copy.deepcopy(_BASE)
    _v["frozen_events"] = _v["frozen_events"][:1]
    _cv, _p, _res = _apply(_v)
    assert _cv is None and "view_fact_basis_changed" in _codes(_p), _codes(_p)


def test_non_relation_fields_cannot_be_rewritten():
    """视图只开放**关系层**：`plan_basis`（climax/causal_chain/time_budget…）也不许改."""
    _v = copy.deepcopy(_BASE)
    _v["plan_basis"] = dict(_v.get("plan_basis") or {}, climax="换掉高潮")
    _cv, _p, _res = _apply(_v)
    assert _cv is None and "view_plan_basis_changed" in _codes(_p), _codes(_p)


# ---------------------------------------------------------------------------
# ② ID 分配与引用改写
# ---------------------------------------------------------------------------
def test_new_entities_get_formal_uids_and_every_reference_is_rewritten():
    """新增的节拍/对白用**响应内临时 uid**；代码一次分配正式 uid 并**统一改写**
    `voice_ref` / `fact_bindings` / `hook_bindings`——漏一处就留下悬空引用.

    ⚠️ 复核 2026-10-05 指出本用例里 `_got_b` 与下一行引用的 `got_b` 拼写不一致
    （**NameError**，测的判据根本没跑到）。这里统一变量名。
    """
    _v = copy.deepcopy(_BASE)
    _uid = "b_new"
    _v["beats"].append({"uid": _uid, "event_uids": [], "text": "收尾镜头",
                         "purpose": "收束", "evidence": "", "source": {},
                         "voice_ref": {"kind": pc.KIND_DIALOGUE, "uid": "d_new"}})
    _v["dialogue_units"].append({"uid": "d_new", "text": "新的台词", "source": {}})
    _cv, _p, _r = _apply(_v)
    assert _cv is not None, _p
    _map = _r["mapping"]
    _new_b = _map[_uid]
    _new_d = _map["d_new"]
    assert _new_b != _uid and _new_d != "d_new", "临时 uid 没有被换成正式 uid"
    _got_b = [b for b in _cv["beats"] if b["text"] == "收尾镜头"][0]
    assert _got_b["uid"] == _new_b
    assert _got_b["voice_ref"]["uid"] == _new_d, "voice_ref 没被改写 ⇒ 悬空引用"
    assert pc.parse_view(_cv)[0] is not None, "改写后的视图必须自洽"


def test_existing_uids_are_preserved():
    _v = copy.deepcopy(_BASE)
    _v["beats"][0]["text"] = "改过的正文"
    _cv, _p, _r = _apply(_v)
    assert _cv is not None
    for _old, _new in _r["mapping"].items():
        # 基线里的**所有**实体（含 d01/n01）都必须原样保留；这条只改节拍正文
        assert _new == _old, "已有实体 uid 被重编号了：%s -> %s" % (_old, _new)


def test_duplicate_uid_in_response_is_rejected_before_mapping():
    _v = copy.deepcopy(_BASE)
    _v["beats"].append(dict(_v["beats"][0]))
    _cv, _p, _res = _apply(_v)
    assert _cv is None and "view_uid_duplicate" in _codes(_p), _codes(_p)


# ---------------------------------------------------------------------------
# ③ 请求装配与「不改」的表达
# ---------------------------------------------------------------------------
def test_revision_request_carries_everything_the_spec_requires():
    """R1「请求必备」逐项：`base_plan_hash` / `fact_basis_hash` / 模式与规则版本 /
    冻结事实与事件 / 可编辑视图 / 全部问题 / 最近拒收候选 / 剩余额度与截止."""
    _req = pc.revision_request(_BASE, problems=[{"code": "carrier_unlabeled"}],
                              rejections=[{"round": 1, "kind": "illegal",
                                           "detail": "载体编号越界"}],
                              rule_version="plan_revision.v2.1",
                              remaining_calls=2, remaining_sec=120.0)
    for _k in ("schema", "rule_version", "mode", "base_plan_hash", "fact_basis_hash",
               "frozen_facts", "frozen_events", "view", "problems", "rejections",
               "budget", "limits"):
        assert _k in _req, "请求缺 %s" % _k
    assert _req["schema"] == pc.REVISION_SCHEMA
    assert _req["base_plan_hash"] == pc.view_digest(_BASE)
    assert _req["fact_basis_hash"] == pc.fact_basis_digest(_BASE)
    assert _req["budget"]["remaining_calls"] == 2
    assert _req["budget"]["remaining_sec"] == 120.0
    assert _req["rejections"][0]["detail"] == "载体编号越界", "拒收原因必须原样带"


def test_empty_view_is_illegal_and_identical_view_is_no_change():
    """R1：「空视图不等于不改；不改须完整返回相同视图」.

    空的 `view` 字段（缺字段）⇒ 结构拒收；回一份**逐字相同**的视图 ⇒ `no_change`
    （由代码比摘要判定，不接受自报）。
    """
    _r = _resp(copy.deepcopy(_BASE))
    del _r["view"]
    _cv, _p, _res = pc.apply_editable_view(_BASE, _r)
    assert _cv is None and "view_response_field_missing" in _codes(_p), _codes(_p)
    _cv2, _p2, _res2 = _apply(copy.deepcopy(_BASE))
    assert _res2["kind"] == "no_change", _res2


def test_evidence_only_update_is_applied_and_projection_shows_it():
    """只更正**来源片段**：主张不变、事实条目里的证据变了."""
    _v = copy.deepcopy(_BASE)
    _v["evidence_updates"] = [{"fact_uid": _BASE["frozen_facts"][0]["uid"],
                               "evidence": "仓库管理员周明去年递了辞呈。"}]
    _cv, _p, _r = _apply(_v)
    assert _cv is not None and _r["kind"] == "applied", _r
    _plan = pc.plan_from_view(_cv)
    assert "另一句" not in _plan["required_facts"][0]
    assert _plan["required_facts"][0].startswith(
        _LEGAL["required_facts"][0].split("｜")[0]), "正文主张被证据更正带走了"


# ---------------------------------------------------------------------------
# ④ 完整关系事务走**真实验收**（不注入假 accept）
# ---------------------------------------------------------------------------
def test_full_chain_through_the_real_acceptance_entry():
    """v2 候选走**真实验收链**（`plan_acceptance_problems`），不注入假 accept."""
    _v = copy.deepcopy(_BASE)
    _v["beats"][0]["text"] = "周明把辞呈摁在桌面上"     # 一处合法的呈现改动
    _probs = o.plan_acceptance_problems(pc.plan_from_view(_v), _SAMPLE["source"])
    _cv, _p, _r = _apply(_v, accept=lambda x: _probs)
    assert _cv is not None and _r["kind"] == "applied"
    assert _r["residuals"] == _probs
    # 合法对照：把关系改坏（删掉一条绑定）⇒ 结构拒收
    _bad = copy.deepcopy(_BASE)
    _bad["fact_bindings"] = _bad["fact_bindings"][:-1]
    _cv2, _p2, _r2 = _apply(_bad)
    assert _cv2 is None and "view_binding_missing" in _codes(_p2), _codes(_p2)


def test_cycle_detection_uses_the_content_digest_including_evidence():
    """循环/无变化判定用的摘要**含证据变化**——否则"补齐出处"会被读成"没改"."""
    _a, _b = copy.deepcopy(_BASE), copy.deepcopy(_BASE)
    _b["evidence_updates"] = [{"fact_uid": _BASE["frozen_facts"][0]["uid"],
                               "evidence": "新的出处。"}]
    assert pc.view_digest(_a) != pc.view_digest(_b)
    assert pc.fact_basis_digest(_a) == pc.fact_basis_digest(_b)


def test_pure_applier_calls_no_model_and_touches_no_files():
    """纯应用器**不调模型、不写盘**（R2：语义核验在外层控制器）."""
    import inspect
    _src = inspect.getsource(pc.apply_editable_view)
    for _forbidden in ("create_service", "generate(", "open(", "write_text",
                       "write_bytes", "Path("):
        assert _forbidden not in _src, "应用器不该碰 %s" % _forbidden


# ---------------------------------------------------------------------------
# ⑤ 增删拆并重绑：派生编号正确、稳定身份不串位
# ---------------------------------------------------------------------------
def test_add_delete_split_and_rebind_keep_derived_numbers_right():
    """新增/删除/拆分后：`B{n}`/`D{n}`/`N{n}` **按数组位置重算**，uid 不串位."""
    _v = copy.deepcopy(_BASE)
    # ① 在中间插入一条对白 ⇒ 后面所有 D 编号后移
    _v["dialogue_units"].insert(1, {"uid": "d_new", "text": "新插入的台词", "source": {}})
    # 旧模式（legacy_audio）下每条声音单元都必须有发声节拍，否则投影成 B0 静默丢失
    _v["beats"].append({"uid": "b_voice_new", "event_uids": [],
                        "text": "新插入的台词镜头", "purpose": "推进",
                        "evidence": "", "source": {},
                        "voice_ref": {"kind": pc.KIND_DIALOGUE, "uid": "d_new"}})
    _cv, _p, _r = _apply(_v)
    assert _cv is not None, _p
    _plan = pc.plan_from_view(_cv)
    assert _plan["dialogue_lines"][1] == "新插入的台词"
    # 派生编号按位置：事实若由旁白承载，`｜承载：Bn/旁白k` 的 k 必须是**新位置**
    assert any("旁白1" in f for f in _plan["required_facts"]), _plan["required_facts"]
    # ② 删除一条对白：删了却不重绑那条事实 ⇒ **悬空引用必须拒收**（不是"编号回收"）
    _v2 = copy.deepcopy(_BASE)
    _v2["dialogue_units"] = []
    for _b in _v2["beats"]:
        if (_b.get("voice_ref") or {}).get("kind") == pc.KIND_DIALOGUE:
            _b["voice_ref"] = {"kind": pc.KIND_VISUAL, "uid": _b["uid"]}
    _cv2, _p2, _r2 = _apply(_v2)
    assert _cv2 is None, "删掉对白却留着指向它的绑定，应当被拒"
    assert "view_binding_dangling" in _codes(_p2), _codes(_p2)
    # 合法对照：删除后**同步重绑** ⇒ 通过，且派生编号里不再有那条对白
    _v3 = copy.deepcopy(_BASE)
    _v3["dialogue_units"] = []
    for _b in _v3["beats"]:
        if (_b.get("voice_ref") or {}).get("kind") == pc.KIND_DIALOGUE:
            _b["voice_ref"] = {"kind": pc.KIND_VISUAL, "uid": _b["uid"]}
    for _fb in _v3["fact_bindings"]:
        _fb["carriers"] = [{"kind": pc.KIND_VISUAL, "uid": _v3["beats"][0]["uid"]}]
    _cv3, _p3, _r3 = _apply(_v3)
    assert _cv3 is not None, _p3
    assert pc.plan_from_view(_cv3)["dialogue_lines"] == []
    # ③ 拆分节拍：稳定 uid 保留，新节拍拿新 uid
    _v3 = copy.deepcopy(_BASE)
    _keep = _v3["beats"][0]["uid"]
    _v3["beats"].insert(1, {"uid": "b_split", "event_uids": [],
                             "text": "拆出来的第二个镜头", "purpose": "推进",
                             "evidence": "", "source": {},
                             "voice_ref": {"kind": pc.KIND_VISUAL, "uid": "b_split"}})
    _cv3, _p3, _r3 = _apply(_v3)
    assert _cv3 is not None, _p3
    assert _cv3["beats"][0]["uid"] == _keep, "已有节拍的 uid 被改了"
    assert _r3["mapping"]["b_split"] != "b_split"
    assert pc.parse_view(_cv3)[0] is not None


def test_rebinding_one_fact_does_not_move_another():
    """改一条事实的承载**不许**牵动另一条（uid 不串位）."""
    _v = copy.deepcopy(_BASE)
    _f0 = _v["fact_bindings"][0]["fact_uid"]
    _b0 = _v["fact_bindings"][1]["carriers"][0]
    _v["fact_bindings"][0]["carriers"] = [{"kind": pc.KIND_VISUAL,
                                           "uid": _v["beats"][-1]["uid"]}]
    _cv, _p, _r = _apply(_v)
    assert _cv is not None, _p
    _after = {b["fact_uid"]: b["carriers"] for b in _cv["fact_bindings"]}
    assert _after[_f0] != [_b0]
    assert _after[_v["fact_bindings"][1]["fact_uid"]] == [_b0], \
        "改了一条事实的承载，另一条跟着动了"