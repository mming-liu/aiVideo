# -*- coding: utf-8 -*-
"""**K 阶段验收矩阵**（复核之十一 P1/P2）：逐字基线 + 能力盘点表 + 失败出口.

三条独立复现的缺口，本矩阵按**最终行为**验收：

1. **要求逐字保留却没给原值**：完整计划修订要求保留 `main_events`/`causal_chain`…
   但请求里只有 B/D/N/F 编号清单——两份只有 `causal_chain` 不同的计划发出**完全相同**
   的请求，同一份响应必然对其中一份报 `core_field_changed`。
   ⇒ 现在请求附**逐字基线**，且"必须保留"的字段清单与守卫**同源**。
2. **能力判据仍是否定式**：`time_budget` 带「载体缺失：无合适承载」的合法计划被判
   `expressible=True`、送进操作式接口，而该接口**拒收** `time_budget`。
   ⇒ 分流改成按**盘点表**的正面声明；表外/未声明的 code 一律 fail-closed。
3. **预算调用失败出口抛裸异常**：`_call_id` 在 `result.success` 之前没赋值 ⇒
   `UnboundLocalError`，统一协议变成裸异常。⇒ 所有失败出口都带原因与调用身份。

另外覆盖：守围范围（规则说"必须原样"的字段**逐项**比对）、盘点表不许腐烂（扫源码）、
`REPLAN_OP_KEYS` ↔ 能力表的字段单一来源。
"""
from __future__ import annotations

import copy
import io
import json
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:                                                # 复用 G 的夹具
    sys.path.insert(0, str(_HERE))

from shared import call_ledger as cl                                          # noqa: E402
from step01_script_gen import outline as o                                    # noqa: E402
from step01_script_gen.plan_revision import RevisionBudget, RevisionController  # noqa: E402
from test_plan_phase_g_rejection_feedback import (                            # noqa: E402
    _LINE, _SRC, _Svc, _legal_plan,
)


@pytest.fixture()
def ledger(tmp_path):
    cl.set_ledger_path(tmp_path / "step01_calls.jsonl")
    yield
    cl.set_ledger_path(None)


def _svc_for(monkeypatch, payloads, **kw):
    _svc = _Svc(payloads, **kw)
    monkeypatch.setattr(o, "create_service", lambda *a, **k: _svc)
    # 2026-10（核验协议迁移）：本文件的 `payloads` 只按**规划**调用配量。核验协议演化后
    # 核验也要发请求，若它也走 `create_service` 就会把顺序回放列表耗光 ⇒
    # `核验调用失败：stub: 没有更多响应` → `executable=False` → 缓存不写，成片误红。
    # 经 `generate_outline_result` 的 `verify=` 覆盖缝注入**合法**核验记录
    # （`verification_helpers.legal_record_for`，四轴齐全、身份随 `digest` 绑定）。
    from tests import verification_helpers as _vh

    def _novel_text_for_verify():
        # 各文件的原文常量名不同（`_SRC` / `_SRC2` / `_SRC_LONG` / `_WIDE_NOVEL`），
        # 按运行时可用性择一，避免写死某个文件名下的常量名。
        for _n in ("_SRC", "_SRC2", "_SRC_LONG", "_WIDE_NOVEL", "_NOVEL"):
            _v = globals().get(_n)
            if isinstance(_v, str) and _v:
                return _v
        return ""

    _gor_orig = o.generate_outline_result

    def _gor_with_verify(*a, **k):
        # 2026-10：原文取**本次调用**传进去的那一份——用模块常量猜会在
        # "本文件传 `_WIDE_NOVEL`、常量里却先命中 `_SRC`"这类情形下把记录
        # 绑到**另一份原文**上 ⇒ `核验记录违约` ⇒ "核验记录无法复核"。
        _novel = (a[0] if a else None) or k.get("novel_text") or _novel_text_for_verify()
        k.setdefault("verify",
                     lambda view, digest: _vh.legal_record_for(
                         view, _novel, plan_digest=digest))
        return _gor_orig(*a, **k)

    monkeypatch.setattr(o, "generate_outline_result", _gor_with_verify)
    return _svc


def _revise(monkeypatch, plan, problems, payloads, **kw):
    _svc = _svc_for(monkeypatch, payloads, **kw)
    _r = o.revise_plan_once(plan, problems, RevisionBudget(deadline_sec=60),
                            novel_text=_SRC)
    return _r, _svc


def _baseline(**over):
    """一份**合法**的计划（与 G/J 夹具同形），基线即可通过统一验收."""
    return o._validate_outline(_legal_plan(**over), _SRC)


# ---------------------------------------------------------------------------
# K01 不同核心值 ⇒ 不同请求（原值必须看得见）
# ---------------------------------------------------------------------------
def test_k01_different_core_values_produce_different_requests(monkeypatch):
    """**复核复现**：只有 `causal_chain` 不同的两份计划，请求不许完全相同."""
    _pa = _baseline(dialogue_lines=[], causal_chain=["甲→乙→丙"])
    _pb = copy.deepcopy(_pa)
    _pb["causal_chain"] = ["甲→丙→乙"]
    _sa = _svc_for(monkeypatch, [copy.deepcopy(_pa)])
    o.revise_plan_full(_pa, o.plan_acceptance_problems(_pa, _SRC), _SRC)
    _sb = _svc_for(monkeypatch, [copy.deepcopy(_pb)])
    o.revise_plan_full(_pb, o.plan_acceptance_problems(_pb, _SRC), _SRC)
    _qa, _qb = _sa.reqs[0].user_prompt, _sb.reqs[0].user_prompt
    assert _qa != _qb, "请求必须带**自己那份**的基线"
    assert "甲→乙→丙" in _qa and "甲→丙→乙" not in _qa, _qa[-800:]
    assert "甲→丙→乙" in _qb and "甲→乙→丙" not in _qb, _qb[-800:]
    # 未带基线时"照抄"都做不到——两份请求同形正是这条缺口的形态
    assert _qa.count("```json") >= 1 and _qb.count("```json") >= 1


# ---------------------------------------------------------------------------
# K02 照抄基线 ⇒ 守卫放行（两种"没变"仍可分辨）
# ---------------------------------------------------------------------------
def test_k02_echoing_the_baseline_passes_the_guard_and_is_a_no_change(monkeypatch):
    """把**请求里给的基线**原样回一份 ⇒ 不许再报 `core_field_changed`（那是"没得抄"的假拒收）."""
    _p = _baseline(dialogue_lines=[])
    _ps = o.plan_acceptance_problems(_p, _SRC)
    # 空台词表会同时触发承载登记与"验收没做完"的标记——本条只关心其中**可修**的那条
    _codes = {x["code"] for x in _ps}
    assert "plan_no_dialogue_lines" in _codes, _codes
    assert o._fix_capability(_ps)["expressible"] is False, o._fix_capability(_ps)
    _r, _svc = _revise(monkeypatch, _p, _ps, [copy.deepcopy(_p), copy.deepcopy(_p)])
    assert _r["kind"] == "applied" and _r["detail"] == "", _r
    assert o._plan_core_preserved(_p, _r["plan"]) == "", "照抄基线不许被核心守卫拦下"
    # 请求里确实给了要照抄的那份（而不是只有编号清单）
    _q = _svc.reqs[0].user_prompt
    for _f in o.replan_protected_fields():
        assert json.dumps(_f, ensure_ascii=False) in _q, "基线 JSON 必须带 %s" % _f
    # 控制器按指纹把"回抄"记成 no_change（与"响应违约"分账）：轮次 kind 仍是 applied
    # （候选确实通过了守卫），**停止原因**才是 no_change——两者的差别写在证据里：
    # 回抄的 `changed_fields` 是空的（回了，但一个字段都没动）
    _ctl = RevisionController(
        accept=lambda _p, _nt="": o.plan_acceptance_problems(_p, _nt),
        revise=lambda plan, problems, budget, rejections=None:
            o.revise_plan_once(plan, problems, budget, rejections, novel_text=_SRC),
        budget=RevisionBudget(max_calls=2, deadline_sec=60))
    _out = _ctl.run(_p, novel_text=_SRC)
    assert _out["stop_reason"] == "no_change", _out["stop_reason"]
    assert _out["history"][0]["kind"] != "illegal", ("回抄原值不是响应违约", _out["history"][0])
    _ev = _out["history"][0]["evidence"]
    assert _ev["route"] == "full_plan" and _ev["applied"] is True, _ev
    assert _ev["response"]["shape"] == "full_plan", _ev
    assert _ev["response"]["missing_fields"] == [], _ev
    assert _ev["response"]["changed_fields"] == [], ("回抄基线：一个字段都不该算改过", _ev)


# ---------------------------------------------------------------------------
# K03 守卫范围 = 规则里"必须逐字保持不变"的那一份（逐项比对）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("field,value", [
    ("main_events", ["甲", "乙", "丁"]),
    ("climax", "丁"),
    ("causal_chain", ["甲→丙"]),
    ("characters", ["甲"]),
    ("emotional_arc", "平"),
    ("key_scenes", []),
    ("character_motives", []),
    ("end_hook", ""),
])
def test_k03_every_protected_field_is_compared(field, value):
    """规则块说"一字不许改"的字段，守卫就得**逐项**拦下（旧写法只比对 5 个键）."""
    _p = _baseline()
    assert field in o.replan_protected_fields(), (field, o.replan_protected_fields())
    _q = copy.deepcopy(_p)
    _q[field] = value
    assert o._plan_core_preserved(_p, _q) == field, (field, o._plan_core_preserved(_p, _q))


@pytest.mark.parametrize("field,value", [
    ("narration_lines", [{"text": _LINE, "source": _LINE, "fact_refs": []}]),
    ("time_budget", "对白约3秒"),
    ("omittable", ["乙"]),
    ("hook_spoken", [_LINE]),
    ("hook_carrier", "B1/画面"),
])
def test_k03b_the_declared_free_fields_are_allowed_to_change(field, value):
    """允许改的字段（含登记键）必须**放行**——守卫不许比规则更严."""
    _p = _baseline()
    assert field not in o.replan_protected_fields(), field
    _q = copy.deepcopy(_p)
    _q[field] = value
    assert o._plan_core_preserved(_p, _q) == "", (field, o._plan_core_preserved(_p, _q))


def test_k03c_carrier_registrations_stay_editable():
    """`shot_beats` / `required_facts` 只放开**末尾的承载登记**，正文与依据逐字不许动."""
    _p = _baseline()
    _q = copy.deepcopy(_p)
    _q["shot_beats"] = [str(x).split("｜载体：")[0] + "｜载体：画面" for x in _q["shot_beats"]]
    _q["required_facts"] = [str(x).split("｜承载：")[0] + "｜承载：B2/画面"
                            for x in _q["required_facts"]]
    assert o._plan_core_preserved(_p, _q) == "", o._plan_core_preserved(_p, _q)
    _r = copy.deepcopy(_p)
    # 改**正文**（载体段之前的那个字），不是往载体段尾巴上加字——加了也会被 `_strip_*` 当成登记
    _r["shot_beats"] = [str(x).replace("节拍：甲板上有风", "节拍：甲板上无人") for x in _r["shot_beats"]]
    assert o._plan_core_preserved(_p, _r) == "shot_beats"
    _s = copy.deepcopy(_p)
    _s["required_facts"] = [str(x).split("｜原文证据")[0] + "（改了本体）" for x in _s["required_facts"]]
    assert o._plan_core_preserved(_p, _s) == "required_facts"


def test_k03d_changing_a_protected_value_through_the_real_entry_is_rejected(monkeypatch):
    """真入口：改了 `characters` 的完整计划 ⇒ `core_field_changed: characters`（不是被静默采纳）."""
    _p = _baseline(dialogue_lines=[])                 # 有空台词表 ⇒ 本轮走完整计划修订
    _ps = o.plan_acceptance_problems(_p, _SRC)
    assert o._fix_capability(_ps)["expressible"] is False, o._fix_capability(_ps)
    _q = copy.deepcopy(_p)
    _q["characters"] = ["甲"]
    _r, _ = _revise(monkeypatch, _p, _ps, [_q])
    assert _r["kind"] == "illegal" and _r["detail"] == "core_field_changed: characters", _r
    assert _r["evidence"]["applied"] is False, _r["evidence"]


# ---------------------------------------------------------------------------
# K04 `time_budget` 标记类问题：表达不了 ⇒ 完整计划修订（可走到成功出口）
# ---------------------------------------------------------------------------
def test_k04_a_time_budget_marker_fix_is_not_sent_to_the_ops_protocol(monkeypatch):
    """**复核复现**：只在 `time_budget` 带「载体缺失：无合适承载」的合法计划.

    旧行为：`expressible=True` ⇒ 走操作式 ⇒ 模型给 `time_budget` ⇒
    `非法响应：未知操作键 time_budget`（合法修法走不通）。
    现行为：能力表判 `plan_field` ⇒ 完整计划修订 ⇒ 改 `time_budget` 即通过验收。
    """
    _p = _baseline(time_budget="载体缺失：无合适承载")
    _ps = o.plan_acceptance_problems(_p, _SRC)
    assert [x["code"] for x in _ps] == ["carrier_missing_annotation"], _ps
    _cap = o._fix_capability(_ps)
    assert _cap["expressible"] is False and _cap["undeclared"] == [], _cap
    assert _cap["inexpressible"] == ["carrier_missing_annotation"], _cap
    # 操作式接口**确实**表达不了它（这条是分流的依据，不是缺陷本身）
    _new, _why = o.apply_replan_ops(json.loads(json.dumps(_p)),
                                    {"time_budget": "对白较短，画面为主"}, _SRC)
    assert _new is None and "未知操作键" in str(_why), _why
    # 走真入口：合法响应（只改 `time_budget`）被采纳 + 验收归零 + 核心内容未动
    _fixed = copy.deepcopy(_p)
    _fixed["time_budget"] = "对白约3秒，画面约5秒"
    _r, _svc = _revise(monkeypatch, _p, _ps, [_fixed])
    assert _r["evidence"]["route"] == "full_plan", _r["evidence"]
    assert _r["kind"] == "applied", _r
    assert o.plan_acceptance_problems(_r["plan"], _SRC) == [], _r["plan"]
    assert o._plan_core_preserved(_p, _r["plan"]) == ""
    assert _r["evidence"]["applied"] is True, _r["evidence"]
    # 请求形态：完整计划契约 + 逐字基线 + 问题记录（path=time_budget）
    assert (_svc.reqs[0].json_schema or {}).get("name") == (o.OUTLINE_SCHEMA or {}).get("name")
    assert "time_budget" in _svc.reqs[0].user_prompt


# ---------------------------------------------------------------------------
# K05 能力盘点表：不许腐烂（扫源码）+ 与操作协议同源
# ---------------------------------------------------------------------------
_CODE_PATTERNS = (r'_problem_record\(\s*"([a-z_]+)"', r'_add\(\s*"([a-z_]+)"',
                  r'_rec\(\s*"([a-z_]+)"', r'"code":\s*"([a-z_]+)"')


def _codes_used_in(path) -> set:
    _t = io.open(path, encoding="utf-8-sig").read()
    _out: set = set()
    for _pat in _CODE_PATTERNS:
        _out |= set(re.findall(_pat, _t))
    return _out


def test_k05_every_producer_code_is_classified():
    """生产者用的**每一个** code 都要在盘点表或豁免表里（表外 ⇒ 悄悄退化成 fail-closed）."""
    _files = [_ROOT / "step01_script_gen" / "plan_feasibility.py",
              _ROOT / "step01_script_gen" / "outline.py"]
    _known = set(o.PLAN_FIX_CAPABILITY) | set(o._FIX_CAPABILITY_OUT_OF_SCOPE)
    _missing = []
    for _f in _files:
        _missing += ["%s：%s" % (_f.name, _c) for _c in sorted(_codes_used_in(_f) - _known)]
    assert not _missing, "这些 code 既没登记能力、也没说明为何不进分流：%s" % _missing
    # 豁免表里的每一条都要有**理由**
    assert all(str(v).strip() for v in o._FIX_CAPABILITY_OUT_OF_SCOPE.values())


def test_k05b_the_capability_table_speaks_the_protocol_s_own_surface():
    """`"ops"` 的落点必须是操作协议**真的能改**的字段（与 `REPLAN_OP_KEYS` 同源）."""
    from _text_gen.deepseek_schema import REPLAN_OPS_SCHEMA
    from step01_script_gen.plan_feasibility import REPLAN_OP_KEYS
    _props = set(((REPLAN_OPS_SCHEMA.get("schema") or {}).get("properties") or {}))
    assert _props == set(REPLAN_OP_KEYS), (_props, set(REPLAN_OP_KEYS))
    _field_of = {"dialogue_ops": "dialogue_lines", "beat_ops": "shot_beats",
                 "fact_carriers": "required_facts", "hook_carrier": "hook_carrier",
                 "hook_spoken": "hook_spoken", "narration_lines": "narration_lines"}
    assert set(_field_of) == set(REPLAN_OP_KEYS), "操作键与字段映射漂移"
    _editable = set(_field_of.values())
    # 一条 code 可以覆盖**两个轴**（落点是 tuple：`budget_unrepresentable` 的对白/旁白）——
    # 两个落点**逐个**核，"只核第一个"会放过旁白侧的漏判
    _bad = [(c, _f) for c, (a, _f) in o.PLAN_FIX_CAPABILITY.items() if a == "ops"
            for _f in ((_f,) if isinstance(_f, str) else tuple(_f))
            if _f not in _editable]
    assert not _bad, "标成 ops 却落在操作协议碰不到的字段：%s" % _bad
    assert isinstance(o.PLAN_FIX_CAPABILITY["budget_unrepresentable"][1], tuple),         "对白/旁白两轴都要登记（单轴会在另一侧漏判）"


def test_k05c_an_undeclared_problem_is_never_treated_as_repairable(monkeypatch):
    """**未声明 = 不可修**（正面声明）：表外的 code、未知的修法标记都 fail-closed."""
    _cap = o._fix_capability([{"code": "brand_new_code", "path": "x"}])
    assert _cap["expressible"] is False and _cap["undeclared"] == ["brand_new_code"], _cap
    _cap2 = o._fix_capability([{"code": "brand_new_code", "fix": "some_new_mark"}])
    assert _cap2["expressible"] is False, _cap2
    assert "some_new_mark" in _cap2["undeclared"][0], _cap2
    # 真入口：表外的 code 走完整计划修订（而不是硬塞进操作式接口）
    _p = _baseline()
    _r, _svc = _revise(monkeypatch, _p, [{"code": "brand_new_code", "path": "x",
                                          "reason": "新判据", "basis": "新判据"}],
                       [copy.deepcopy(_p)])
    assert _r["evidence"]["route"] == "full_plan", _r["evidence"]
    assert (_svc.reqs[0].json_schema or {}).get("name") == (o.OUTLINE_SCHEMA or {}).get("name")


# ---------------------------------------------------------------------------
# K06 混合问题与局部修改的对照（能力表下的既有结论不许退化）
# ---------------------------------------------------------------------------
def test_k06_a_mixed_budget_and_orphan_plan_is_fully_declared(monkeypatch):
    """预算超限 × 台词孤儿（31 句）——**混合问题**仍走完整计划修订.

    与 J09 的分工：J09 验"走到成功出口"，本条验"**没有任何一条靠 fail-closed 兜底**"
    （`undeclared` 为空）——否则"表达不了"这个结论可能只是表没登记，而不是真的不可表达。
    """
    from test_plan_phase_j_capability import _WIDE_NOVEL, _wide_plan
    _p = _wide_plan()
    _ps = o.plan_acceptance_problems(_p, _WIDE_NOVEL)
    _codes = {x["code"] for x in _ps}
    assert "budget_infeasible" in _codes and "orphan_uncarried" in _codes, _codes
    _cap = o._fix_capability(_ps)
    assert _cap["expressible"] is False and _cap["undeclared"] == [], _cap
    assert "budget_infeasible" in _cap["inexpressible"], _cap


# ---------------------------------------------------------------------------
# K07 预算重选的失败出口：统一协议 + 调用身份（不许裸异常）
# ---------------------------------------------------------------------------
def _budget_only_plan():
    """**只有**预算超限：31 句（逐字取自原文）且每句都有承载 ⇒ 走预算重选路."""
    _p = _legal_plan()
    _p["dialogue_lines"] = [_LINE] * 31
    _p["shot_beats"] = ["节拍：画面%d｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词%d"
                        % (i, _LINE, i) for i in range(1, 32)]
    return o._validate_outline(_p, _SRC)


def test_k07_a_failed_budget_call_returns_the_unified_protocol(monkeypatch):
    """**复核复现**：只有预算超限的计划 + 服务桩调用失败 ⇒ 统一协议，不许 `UnboundLocalError`."""
    _p = _budget_only_plan()
    _ps = o.plan_acceptance_problems(_p, _SRC)
    assert [x["code"] for x in _ps] == ["budget_infeasible"], _ps
    _r, _svc = _revise(monkeypatch, _p, _ps, [], error="transport denied 403")
    assert _r["kind"] == "call_failed", _r
    assert "transport denied 403" in _r["detail"], _r["detail"]
    # **接口裁决**（复核之十二 P1）：`budget_infeasible` 的登记修法是 `content`
    # （必须增删条目，操作协议表达不了）⇒ 接口是完整计划；"这一轮带的是预算读数"
    # 另记在 `budget_context`——接口与反馈 flavor 分账，不许把两者混成同一个值
    assert _r["evidence"]["route"] == "full_plan", _r["evidence"]
    assert _r["evidence"]["budget_context"] is True, _r["evidence"]


def test_k07b_an_exception_keeps_the_call_identity(monkeypatch):
    """通道抛异常时也要报出**这一次调用是哪一个**（台账里那一行就是按它落的）."""
    _p = _budget_only_plan()
    _ps = o.plan_acceptance_problems(_p, _SRC)

    class _Boom(Exception):
        pass

    _svc = _svc_for(monkeypatch, [])

    def _raise(_req):
        _e = _Boom("通道炸了")
        _e.ai_video_call_id = "call_review11"
        raise _e

    _svc.generate = _raise
    _r = o.revise_plan_once(_p, _ps, RevisionBudget(deadline_sec=60), novel_text=_SRC)
    assert _r["kind"] == "call_failed", _r
    assert "通道炸了" in _r["detail"], _r["detail"]
    assert _r["call_id"] == "call_review11", _r["call_id"]


def test_k07c_the_ledgered_proxy_hands_the_call_id_to_the_exception(tmp_path):
    """端到端：**真代理**抛出的异常带 `call_id`，且台账里有同一 id 的行（证据对得上）."""
    from _text_gen import service as svc_mod
    from _text_gen.models import TextGenerationRequest

    cl.set_ledger_path(tmp_path / "step01_calls.jsonl")
    try:
        class _P:
            def generate(self, req):
                raise RuntimeError("provider boom")

        _s = svc_mod._LedgeredService(_P(), "stub")
        try:
            _s.generate(TextGenerationRequest(system_prompt="s", user_prompt="u"))
        except RuntimeError as _e:
            _cid = cl.call_id_of(_e)
        else:                                                     # pragma: no cover
            raise AssertionError("异常必须照原样抛出")
        _rows = cl.read_calls()
        assert _cid and any(str(r.get("call_id") or "") == _cid for r in _rows), (_cid, _rows)
        assert _rows[-1].get("ok") is False, _rows[-1]
    finally:
        cl.set_ledger_path(None)


def test_k10_a_budget_round_records_the_response_and_the_application(monkeypatch):
    """预算重选路也要留**原始响应与应用结果**（复核之十一前：这一栏一直是空的）.

    与 ops/完整计划两路同一份口径——否则"预算轮到底回了什么、有没有被采纳"事后查不出来。
    """
    _p = _budget_only_plan()
    _ps = o.plan_acceptance_problems(_p, _SRC)
    _fixed = copy.deepcopy(_p)
    _fixed["dialogue_lines"] = [_LINE]
    _fixed["shot_beats"] = [str(_b).split("｜载体：")[0]
                            + ("｜载体：台词1" if _i == 1 else "｜载体：画面")
                            for _i, _b in enumerate(_fixed["shot_beats"], 1)]
    _r, _svc = _revise(monkeypatch, _p, _ps, [_fixed])
    assert _r["kind"] == "applied", _r
    _ev = _r["evidence"]
    assert _ev["route"] == "full_plan" and _ev["budget_context"] is True, _ev
    assert _ev["response"]["shape"] == "full_plan", _ev
    assert _ev["response"]["missing_fields"] == [], _ev
    assert _ev["applied"] is True, _ev
    assert "dialogue_lines" in _ev["response"]["changed_fields"], _ev
    assert o.plan_acceptance_problems(_r["plan"], _SRC) == [], _r["plan"]

    # 反例：完整计划里改了核心字段 ⇒ `applied=False` 且响应证据如实记下改了哪个字段
    _bad = copy.deepcopy(_fixed)
    _bad["causal_chain"] = ["改→过→的"]
    _r2, _ = _revise(monkeypatch, _p, _ps, [_bad])
    assert _r2["kind"] == "illegal" and _r2["detail"] == "core_field_changed: causal_chain", _r2
    assert _r2["evidence"]["applied"] is False, _r2["evidence"]
    assert "causal_chain" in _r2["evidence"]["response"]["changed_fields"], _r2["evidence"]


# ---------------------------------------------------------------------------
# K08 预算重选的整份重写请求：**无条件**带基线 + 带原因
# ---------------------------------------------------------------------------
def test_k08_a_no_alternative_plan_gets_a_reason_and_the_baseline(monkeypatch):
    """规划层自己报告「无合适替代」时，请求不许既没有原因、也没有旧计划（旧形态）."""
    _p = _baseline(time_budget="无合适替代——本集装不下")
    _ps = o.plan_acceptance_problems(_p, _SRC)
    assert [x["code"] for x in _ps] == ["budget_no_alternative"], _ps
    _fixed = copy.deepcopy(_p)
    _fixed["time_budget"] = "对白约3秒，画面约5秒"
    _r, _svc = _revise(monkeypatch, _p, _ps, [_fixed])
    _q = _svc.reqs[0].user_prompt
    # 原因**必须由反馈块给出**（不是靠基线 JSON 里那句标记文本凑巧出现）
    assert "规划层自己报告" in _q, "原因必须进请求"
    assert "逐字基线" in _q and '"causal_chain"' in _q, "旧计划必须附在请求里"
    assert _r["kind"] == "applied" and o.plan_acceptance_problems(_r["plan"], _SRC) == [], _r


def test_k08b_the_ops_context_is_still_read_only():
    """操作式分支不许被换成"整份重写"——它仍要把计划标成**只读上下文**."""
    _rep = {"budget": {"min": 60, "max": 90}, "dialogue_lines": 1, "dialogue_sec": 3.0,
            "lower_bound_sec": 3.0, "dialogue_unrepresentable": [],
            "beat_carrier_duplicate": [], "dialogue_orphan": ["台词2"],
            "beat_carrier_unlabeled": [], "beat_carrier_dangling": [],
            # 复核之十二起接口裁决读**生产者记录**（`rep["problems"]`）：手工夹具缺它
            # 就无从证明"表达得了"⇒ fail-closed 走完整计划接口。这里按真实生产者给。
            "problems": [{"code": "orphan_uncarried", "kind": "dialogue", "index": 2,
                          "path": "dialogue_lines[1]"}]}
    _block = o._budget_feedback_block(_rep, _legal_plan())
    assert "只读上下文" in _block, _block[-400:]
    assert "其余字段请**原样返回**" not in _block, "操作式分支不许要求整份重写"


# ---------------------------------------------------------------------------
# K09 提示与守卫**同一份来源**
# ---------------------------------------------------------------------------
def test_k09_the_rule_the_baseline_and_the_guard_share_one_source():
    """规则里"必须逐字保留"的字段渲染自 `replan_protected_fields()`；可改字段渲染自同一处."""
    _prot = o.replan_protected_fields()
    _rule = o.render_full_plan_rule()
    _head, _, _tail = _rule.partition("**可以改**")
    # "可改清单"= **那一行**（`**可以改**：…`）。规则块在它之后还会讲"本轮做不到什么"
    # （例如 `shot_beats` 的条数冻结）——那是**禁令**，不是可改清单，按整段尾巴比会误判。
    _tail = next((_l for _l in _tail.splitlines() if _l.strip()), _tail)
    for _f in _prot:
        assert ("`%s`" % _f) in _head, ("必须保留清单里缺 %s" % _f, _head)
    for _f in _prot:
        assert ("`%s`" % _f) not in _tail, ("可改清单里混进了保留字段 %s" % _f, _tail)
    for _f in o._REPLAN_FREE_FIELDS + o._REPLAN_DECL_FIELDS:
        assert ("`%s`" % _f) in _tail, ("可改清单里缺 %s" % _f, _tail)
    # 预算重选那一份清单同样来自这里（写两份必然漂移）
    _inline = o.render_protected_fields_inline()
    for _f in _prot:
        assert ("`%s`" % _f) in _inline, _inline


def test_k09b_the_guard_uses_the_same_editable_set():
    """守卫放行的字段集合 == 规则里"可以改"的字段集合（一处改、两处同时变）."""
    assert set(o._replan_editable_keys()) == (set(o._REPLAN_FREE_FIELDS)
                                              | set(o._REPLAN_DECL_FIELDS)
                                              | set(o._REPLAN_IGNORED_KEYS))
    _p = _baseline()
    _free = set(o.replan_protected_fields()) - {"shot_beats", "required_facts"}
    _q = copy.deepcopy(_p)
    for _f in _free:
        _q[_f] = "改过了" if not isinstance(_q.get(_f), list) else ["改过了"]
    assert o._plan_core_preserved(_p, _q) != "", "改了保留字段却没人拦"
