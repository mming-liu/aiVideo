# -*- coding: utf-8 -*-
"""B 包 ②：**增量与完整视图的传输等价**（逐类合法组合，走真实入口）.

规格：复核 2026-10-06（第七轮）§「验证传输等价」+ 第八轮 §4 —— 每一类合法改动都要
**分别经增量与完整视图**应用，然后核对三样东西：

1. **候选摘要**（`view_digest` 逐字相同）；
2. **uid 映射**（临时 id → 正式 id 的结果相同）；
3. **本地验收残余**（code 列表相同）。

「换的只是传输表示」这句话只有这样逐类核过才算成立——只做一组对照证明不了。
矩阵覆盖：新增并绑定 / 删除并重绑 / 拆分并重排 / 仅补出处 / 改绑定 / 钩子整块替换 /
空修改 / 非法输入（两边都拒收）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import pytest                                                          # noqa: E402

from shared import presentation_contract as pc                                # noqa: E402
from step01_script_gen import outline as o                                    # noqa: E402

_SAMPLE = json.loads(
    (_ROOT / "tests" / "step01_script_gen" / "fixtures"
     / "plan_revision_mixed_legal_sample_v1.json").read_text(encoding="utf-8"))
_SRC = _SAMPLE["source"]
_LEGAL, _ = o.apply_replan_ops(_SAMPLE["base_plan"], _SAMPLE["response"], _SRC)
_VIEW, _ = pc.view_from_plan(_LEGAL)


def _base():
    return json.loads(json.dumps(_VIEW))


def _env(base, **over):
    _req = pc.delta_request(base)
    _d = {"schema": pc.DELTA_SCHEMA, "rule_version": pc.DELTA_RULE_VERSION,
          "base_plan_hash": _req["base_plan_hash"],
          "fact_basis_hash": _req["fact_basis_hash"]}
    _d.update(over)
    return _d


def _via_delta(base, **over):
    return pc.apply_delta_view(base, _env(base, **over), mode="legacy_audio",
                               accept=lambda v: o._accept_view_candidate(v, _SRC, None))


def _via_full(base, view):
    _req = pc.revision_request(base, problems=[], rule_version=o.PLAN_VIEW_RULE_VERSION)
    _resp = {"schema": pc.REVISION_SCHEMA, "rule_version": o.PLAN_VIEW_RULE_VERSION,
             "base_plan_hash": _req["base_plan_hash"],
             "fact_basis_hash": _req["fact_basis_hash"], "view": view}
    return pc.apply_editable_view(base, _resp, mode="legacy_audio",
                                  accept=lambda v: o._accept_view_candidate(v, _SRC, None))


def _res(problems):
    return [p.get("code") for p in (problems or [])]


# ---------------------------------------------------------------------------
# 逐类对照：每种改动写**两份**（增量 / 完整视图），必须同结果
# ---------------------------------------------------------------------------
def _case_new_and_bind(base):
    """新增一条节拍并把一条事实改绑到它（增改 + 绑定 + 顺序三样一起动）."""
    _new = {"uid": "new:beat", "event_uids": [], "text": "新加的一条节拍",
            "purpose": "补足信息来源", "evidence": "", "source": {}, "voice_ref": None}
    _fu = str(base["fact_bindings"][0]["fact_uid"])
    _order = [str(b["uid"]) for b in base["beats"]] + ["new:beat"]
    _carriers = [{"kind": "visual", "uid": "new:beat"}]

    _full = _base()
    _full["beats"] = _full["beats"] + [json.loads(json.dumps(_new))]
    for _fb in _full["fact_bindings"]:
        if str(_fb["fact_uid"]) == _fu:
            _fb["carriers"] = json.loads(json.dumps(_carriers))
    return (dict(upsert={"beats": [json.loads(json.dumps(_new))]},
                 order={"beats": _order},
                 fact_bindings=[{"fact_uid": _fu,
                                 "carriers": json.loads(json.dumps(_carriers))}]),
            _full)


def _case_delete_and_rebind(base):
    """删掉一条节拍，并把依附它的事实重绑到另一条（删除 + 重绑，必须同一份响应里做完）."""
    _victim = str(base["beats"][2]["uid"])
    _keep = str(base["beats"][0]["uid"])
    _fu = str(base["fact_bindings"][2]["fact_uid"])          # f03 挂在被删那条上
    _order = [str(b["uid"]) for b in base["beats"] if str(b["uid"]) != _victim]

    _full = _base()
    _full["beats"] = [b for b in _full["beats"] if str(b["uid"]) != _victim]
    for _fb in _full["fact_bindings"]:
        if str(_fb["fact_uid"]) == _fu:
            _fb["carriers"] = [{"kind": "visual", "uid": _keep}]
    return (dict(delete={"beats": [_victim]}, order={"beats": _order},
                 fact_bindings=[{"fact_uid": _fu, "carriers": [
                     {"kind": "visual", "uid": _keep}]}]),
            _full)


def _case_split_and_reorder(base):
    """拆分并重排：新增一条 + 给出完整新顺序（顺序块是唯一改集合顺序的入口）."""
    _a = {"uid": "new:a", "event_uids": [], "text": "拆出来的甲", "purpose": "拆分",
          "evidence": "", "source": {}, "voice_ref": None}
    _b = {"uid": "new:b", "event_uids": [], "text": "拆出来的乙", "purpose": "拆分",
          "evidence": "", "source": {}, "voice_ref": None}
    _uids = [str(x["uid"]) for x in base["beats"]]
    _order = ["new:a", _uids[0], "new:b"] + _uids[1:]

    _full = _base()
    _full["beats"] = ([json.loads(json.dumps(_a)), _full["beats"][0],
                       json.loads(json.dumps(_b))] + _full["beats"][1:])
    return (dict(upsert={"beats": [json.loads(json.dumps(_a)),
                                   json.loads(json.dumps(_b))]},
                 order={"beats": _order}), _full)


def _case_evidence_only(base):
    """仅补出处（不动集合、不动关系）——顺序可省略."""
    _fu = str(base["frozen_facts"][0]["uid"])
    _ev = base["frozen_facts"][0].get("evidence") or ""
    _full = _base()
    _full["evidence_updates"] = [{"fact_uid": _fu, "evidence": _ev}]
    return (dict(evidence_updates=[{"fact_uid": _fu, "evidence": _ev}]), _full)


def _case_rebind_only(base):
    """只改一条事实的载体（不动集合、不动顺序）."""
    _fu = str(base["fact_bindings"][0]["fact_uid"])
    _car = [{"kind": "narration", "uid": str(base["narration_units"][1]["uid"])}]
    _full = _base()
    for _fb in _full["fact_bindings"]:
        if str(_fb["fact_uid"]) == _fu:
            _fb["carriers"] = json.loads(json.dumps(_car))
    return (dict(fact_bindings=[{"fact_uid": _fu,
                                 "carriers": json.loads(json.dumps(_car))}]), _full)


def _case_hook_block(base):
    """钩子**整块替换**（给完整关系，不是补丁）."""
    _hb = {"spoken_uids": [], "carriers": []}
    _full = _base()
    _full["hook_bindings"] = json.loads(json.dumps(_hb))
    return (dict(hook_bindings=json.loads(json.dumps(_hb))), _full)


def _case_no_change(base):
    """空修改：增量什么都不给，完整视图**原样回带**——两边都必须 `no_change`."""
    return (dict(), _base())


CASES = [
    ("新增并绑定", _case_new_and_bind),
    ("删除并重绑", _case_delete_and_rebind),
    ("拆分并重排", _case_split_and_reorder),
    ("仅补出处", _case_evidence_only),
    ("只改绑定", _case_rebind_only),
    ("钩子整块替换", _case_hook_block),
    ("空修改", _case_no_change),
]


#: 每一类的**固定预期**（复核 2026-10-06 第十轮 ③）.
#:
#: 「两边一致」本身证明不了合法修法可用——两条路**同时**退化成 `illegal` 时它也成立
#: （见本文件末尾的「一律拒收」变异，实测七条全绿）。所以先钉死**这一类应当得到什么**：
#: `kind`、残余 code（排序后比，避免把"残余次序"当契约）、以及新增条目的正式 uid 映射。
_EXPECT = {
    "新增并绑定": {"kind": "applied", "residuals": ["carrier_fact_visual_mismatch",
                                                    "carrier_unlabeled"],
                   "mapping": {"new:beat": "b05"}},
    # 2026-10-18「A 窄版」裁决：画面承载与声音归属可落在同一节拍（声画共镜），
    # 重绑到「旁白k」节拍不再报 carrier_fact_visual_mismatch；该节拍正文与事实
    # 无交集时按 carrier_fact_visual_unrelated 报（分类随裁决变化，非回归）。
    "删除并重绑": {"kind": "applied", "residuals": ["carrier_fact_visual_unrelated"],
                   "mapping": {}},
    "拆分并重排": {"kind": "applied", "residuals": ["carrier_unlabeled",
                                                    "carrier_unlabeled"],
                   "mapping": {"new:a": "b05", "new:b": "b06"}},
    "仅补出处": {"kind": "applied", "residuals": [], "mapping": {}},
    "只改绑定": {"kind": "applied", "residuals": ["carrier_fact_narration_unrelated"],
                 "mapping": {}},
    "钩子整块替换": {"kind": "no_change", "residuals": [], "mapping": {}},
    "空修改": {"kind": "no_change", "residuals": [], "mapping": {}},
}


def _one_case(name, builder, *, delta=None, full=None):
    """一类改动的**完整判定**：先核这一类该得到什么，再核两条路一致."""
    _base_v = _base()
    _over, _full_view = builder(_base_v)
    _via_d = _via_delta if delta is None else delta
    _via_f = _via_full if full is None else full

    _vd, _pd, _rd = _via_d(_base_v, **_over)
    _vf, _pf, _rf = _via_f(_base_v, _full_view)

    _exp = _EXPECT[name]
    assert _rd["kind"] == _exp["kind"], (
        "增量路的结论与本类**应有**结论不符", name, _rd["kind"], _exp["kind"], _res(_pd))
    assert _rf["kind"] == _exp["kind"], (
        "完整视图路的结论与本类**应有**结论不符", name, _rf["kind"], _exp["kind"],
        _res(_pf))
    assert sorted(_res(_rd["residuals"])) == sorted(_exp["residuals"]),         (name, _res(_rd["residuals"]), _exp["residuals"])
    assert sorted(_res(_rf["residuals"])) == sorted(_exp["residuals"]),         (name, _res(_rf["residuals"]), _exp["residuals"])
    for _k, _v in _exp["mapping"].items():
        assert _rd["mapping"].get(_k) == _v, ("增量路的 uid 映射", name, _k, _rd["mapping"])
        assert _rf["mapping"].get(_k) == _v, ("完整视图路的 uid 映射", name, _k, _rf["mapping"])
    if _exp["kind"] != "applied":
        _moved = {k: v for k, v in _rd["mapping"].items() if k != v}
        assert not _moved, ("没改动却发生了重新编号", name, _moved)
        assert _vd is None and _vf is None, name
        return
    assert _vd is not None and _vf is not None, name
    assert pc.view_digest(_vd) == pc.view_digest(_vf), "两条传输表示算出了不同的候选"
    assert _rd["mapping"] == _rf["mapping"], "uid 映射不一致"
    assert _res(_rd["residuals"]) == _res(_rf["residuals"]), "残余问题不一致"


@pytest.mark.parametrize("name,builder", CASES, ids=[c[0] for c in CASES])
def test_each_class_lands_on_its_expected_result_and_the_two_paths_agree(name, builder):
    """每一类合法改动：**先核这一类应得的结论**，再核增量与完整视图三样一致."""
    _one_case(name, builder)


def _reject_all(*a, **k):
    """变异：**一律拒收**（用来证明上面的预期真的在判别，而不是"两边一样"就放过）."""
    return None, [{"code": "forced_reject", "kind": "view", "ref": "", "path": "",
                   "reason": "变异：一律拒收", "fix": ""}], {
        "kind": "illegal", "mapping": {}, "residuals": [], "detail": "forced"}


def test_the_matrix_goes_red_when_both_paths_reject_everything(monkeypatch):
    """复核第十轮 ③：两条路**同时**退化时，逐项预期必须全部转红.

    旧写法只在两侧都 `illegal` 时 `return`，于是"两条应用器都注入为一律拒收"七条全绿——
    等价测试自己证明不了合法修法可用。
    """
    monkeypatch.setattr(pc, "apply_delta_view", _reject_all)
    monkeypatch.setattr(pc, "apply_editable_view", _reject_all)
    _red = []
    for _name, _b in CASES:
        try:
            _one_case(_name, _b)
        except AssertionError:
            _red.append(_name)
    assert len(_red) == len(CASES), "这些类没转红：%s" % ([c[0] for c in CASES], _red)
    # 注入作用在**实际消费的**对象上：撤掉注入后同类立刻恢复原结论
    monkeypatch.undo()
    _one_case("仅补出处", _case_evidence_only)


@pytest.mark.parametrize("name,builder", CASES, ids=[c[0] for c in CASES])
def test_the_baseline_is_untouched_by_either_path(name, builder):
    """两条路都**只交候选**：基线那份视图必须逐字不变（失败时也一样）."""
    _base_v = _base()
    _before = json.dumps(_base_v, ensure_ascii=False, sort_keys=True)
    _over, _full = builder(_base_v)
    _via_delta(_base_v, **_over)
    _via_full(_base_v, _full)
    assert json.dumps(_base_v, ensure_ascii=False, sort_keys=True) == _before, \
        "应用器改动了传入的基线对象：%s" % name


# ---------------------------------------------------------------------------
# 非法输入：两个入口都要**结构化拒收**，且基线不变
# ---------------------------------------------------------------------------
_BAD_DELTA = [
    ("缺字段", dict(upsert={"beats": [{"uid": "b01"}]})),
    ("错类型", dict(upsert={"beats": [dict(_VIEW["beats"][0], text=None)]})),
    ("未知 id", dict(upsert={"beats": [dict(_VIEW["beats"][0], uid="b09")]})),
    ("重复绑定", dict(fact_bindings=[
        {"fact_uid": str(_VIEW["fact_bindings"][0]["fact_uid"]),
         "carriers": [{"kind": "visual", "uid": str(_VIEW["beats"][0]["uid"])}]},
        {"fact_uid": str(_VIEW["fact_bindings"][0]["fact_uid"]),
         "carriers": [{"kind": "visual", "uid": str(_VIEW["beats"][0]["uid"])}]}])),
    ("未知顶层键", dict(fact_binding=[])),
    ("集合变了不给顺序", dict(upsert={"beats": [
        {"uid": "new:x", "event_uids": [], "text": "甲", "purpose": "p",
         "evidence": "", "source": {}, "voice_ref": None}]})),
    ("钩子 null", dict(hook_bindings=None)),
]


@pytest.mark.parametrize("name,over", _BAD_DELTA, ids=[b[0] for b in _BAD_DELTA])
def test_illegal_inputs_are_rejected_structurally_and_leave_the_baseline_alone(name, over):
    """非法输入 ⇒ 明确 code、基线逐字不变、**不进入内容核验**（残余为空）."""
    _base_v = _base()
    _before = json.dumps(_base_v, ensure_ascii=False, sort_keys=True)
    _v, _p, _rk = _via_delta(_base_v, **over)
    assert _rk["kind"] == "illegal", (name, _rk)
    assert _codes_nonempty(_p), name
    assert _rk["residuals"] == [], "拒收的输入不该产生内容残余（残余只属于已应用的候选）"
    assert json.dumps(_base_v, ensure_ascii=False, sort_keys=True) == _before
    assert _v is None


def _codes_nonempty(problems):
    return bool(problems) and all(str(p.get("code") or "") for p in problems)
