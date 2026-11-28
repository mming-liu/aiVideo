# -*- coding: utf-8 -*-
"""**L 阶段验收矩阵**（复核之十二 P1）：接口只裁决一次，schema / 反馈 / 应用器同源.

三条独立复现的缺口，本矩阵按**最终行为**验收：

1. **能力表判"表达不了"，请求却发操作协议**：预算分支优先于能力裁决，内部又按"有没有
   不可表示条目"**另判一次**接口 ⇒ 31 句总量超限 × 一句不可单镜表示时发出 `replan_ops`，
   而该协议**删不掉条目**——同一份 31→1、同步重绑、核心不变的响应，走完整计划接口
   能验收归零，走操作协议连门都进不去（模型照办也完不成这个合法修法）。
   ⇒ 接口选择收进 `_replan_route` **一处**；`schema` / 反馈块 / 应用器都消费它。
2. **反馈承诺了守卫禁止的事**：「无合适替代」反馈写着「`required_facts` 可以少条目」，
   而核心内容守卫要求事实本体逐项不变——模型照反馈减少一条事实，随后被判
   `core_field_changed: required_facts`（实测复现）。⇒ **修反馈、保持已定的守卫**：
   合法出路只到"选哪几句台词 / 怎么登记承载"为止，内容取舍交还用户。
3. **两个"回一整份计划"的入口判据不同**：预算重选的整份重写分支没有完整性/结构前置门，
   只回操作的响应会被记成 `core_field_changed`（另一个入口早就改成如实报"缺 N 个字段"）。
   ⇒ 两个入口共用同一份 `_full_plan_response_problem`。

覆盖：预算超限 × 单镜不可表示（对白 / 旁白**两轴**）、两条 `time_budget` 停止标记，
以及每条的**合法成功对照**（只证明"错误进了请求"或"响应被拒收"都只说明一半）。
"""
from __future__ import annotations

import copy
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

from step01_script_gen import outline as o                                    # noqa: E402
from step01_script_gen.plan_revision import RevisionBudget                    # noqa: E402
from test_plan_phase_g_rejection_feedback import (                            # noqa: E402
    _LINE, _SRC, _Svc, _legal_plan,
)

#: 一句**装不进单镜**的原文（整句一份、句末收尾）
_LONG = "海风吹动桅杆上的帆布，" + "海浪拍打甲板而帆布不断翻动，" * 12 + "船仍向前行驶。"
#: 原文：把这两句放进**同一段引语**里，`representable_replacements` 才有可列出的
#: **已验算替代**（它按"引语里句末收尾的前缀"取候选——出处通篇一个句末标点都没有时
#: 返回空表，反馈块据此改说"按原文顺序重排载体"）
_SRC_LONG = "开头。“甲板上有风，帆布猎猎作响。" + _LONG + "”事件丙。"


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
    _novel = kw.pop("novel_text", _SRC)
    _svc = _svc_for(monkeypatch, payloads, **kw)
    _r = o.revise_plan_once(plan, problems, RevisionBudget(deadline_sec=60),
                            novel_text=_novel)
    return _r, _svc


def _schema_name(req) -> str:
    return str((req.json_schema or {}).get("name") or "")


# ---------------------------------------------------------------------------
# 夹具：预算超限 × 单镜不可表示（对白轴 / 旁白轴）
# ---------------------------------------------------------------------------
def _mixed_dialogue_plan():
    """31 句（前 30 句可表示、第 31 句装不进单镜），每句都有承载 ⇒ 总量超限 + 不可表示."""
    _p = _legal_plan(dialogue_lines=[_LINE] * 30 + [_LONG])
    _p["shot_beats"] = [
        "节拍：海面%d｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词%d" % (_i, _LINE, _i)
        for _i in range(1, 32)]
    return o._validate_outline(_p, _SRC_LONG)


def _mixed_dialogue_fix(plan):
    """**合法修法**（复核者给的那一份）：31→1、同步重绑载体、核心内容一字不动."""
    _q = copy.deepcopy(plan)
    _q["dialogue_lines"] = [_LINE]
    _q["shot_beats"] = [
        _b if _i == 0 else _b.rsplit("载体：", 1)[0] + "载体：画面"
        for _i, _b in enumerate(_q["shot_beats"])]
    return _q


def _mixed_narration_plan():
    """旁白轴：一条**装不进单镜**的旁白 + 总量超限（旁白同样计入发声下界）.

    旁白是**整表声明**（`narration_lines`），与对白轴走同一条能力裁决——这条正是
    "落点只写 `dialogue_lines`" 会漏判的一侧。
    """
    _p = _legal_plan(dialogue_lines=[_LINE] * 30)
    _p["shot_beats"] = (
        ["节拍：海面%d｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词%d" % (_i, _LINE, _i)
         for _i in range(1, 31)]
        + ["节拍：长旁白｜本镜信息目的：推进｜原文依据：“%s”｜载体：旁白1" % _LONG])
    _p["required_facts"] = [
        "甲板上有风，帆布猎猎作响｜原文证据：“%s”｜承载：B1/台词1" % _LINE]
    _p["narration_lines"] = [{"text": _LONG, "source": _LONG, "fact_refs": []}]
    return o._validate_outline(_p, _SRC_LONG)


def _only_narration_unrep_plan():
    """**只有**旁白不可单镜表示（预算内）⇒ 操作协议表达得了（替换旁白整表）."""
    _p = _legal_plan(dialogue_lines=[_LINE])
    _p["shot_beats"] = [
        "节拍：甲板上有风｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % _LINE,
        "节拍：长旁白｜本镜信息目的：推进｜原文依据：“%s”｜载体：旁白1" % _LONG,
        "节拍：收尾｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _LINE]
    _p["narration_lines"] = [{"text": _LONG, "source": _LONG, "fact_refs": []}]
    return o._validate_outline(_p, _SRC_LONG)


# ---------------------------------------------------------------------------
# L01 预算超限 × 单镜不可表示（对白轴）：走**完整计划**接口并走到成功出口
# ---------------------------------------------------------------------------
def test_l01_budget_over_x_unrepresentable_uses_the_full_plan_interface(monkeypatch):
    """**复核复现**：能力表判不可表达，请求就必须是完整计划（旧：发 `replan_ops`）."""
    _p = _mixed_dialogue_plan()
    _ps = o.plan_acceptance_problems(_p, _SRC_LONG)
    assert {x["code"] for x in _ps} == {"budget_infeasible", "budget_unrepresentable"}, _ps
    _cap = o._fix_capability(_ps)
    assert _cap["expressible"] is False and _cap["undeclared"] == [], _cap
    assert "budget_infeasible" in _cap["inexpressible"], _cap
    # 操作协议**确实**表达不了它（分流的依据）：整份计划会被当未知操作键拒收
    _empty, _why = o.apply_replan_ops(copy.deepcopy(_p), {"dialogue_lines": [_LINE]}, _SRC_LONG)
    assert _empty is None and "未知操作键" in str(_why), _why
    # 真入口：请求是**完整计划**（不是操作协议），反馈是"逐字基线"版
    _q = _mixed_dialogue_fix(_p)
    _r, _svc = _revise(monkeypatch, _p, _ps, [_q], novel_text=_SRC_LONG)
    assert _schema_name(_svc.reqs[0]) == o.OUTLINE_SCHEMA["name"], _schema_name(_svc.reqs[0])
    assert _r["evidence"]["route"] == "full_plan", _r["evidence"]
    assert _r["evidence"]["budget_context"] is True, _r["evidence"]
    _q_text = _svc.reqs[0].user_prompt
    assert "逐字基线" in _q_text and "只读上下文" not in _q_text, "完整计划轮不许标成只读上下文"
    # 合法修法（31→1、同步重绑、核心不变）被采纳，且**统一验收归零**
    assert _r["kind"] == "applied", _r
    assert o._plan_core_preserved(_p, _q) == ""
    assert o.plan_acceptance_problems(_r["plan"], _SRC_LONG) == [], _r["plan"]
    assert _r["evidence"]["applied"] is True, _r["evidence"]


# ---------------------------------------------------------------------------
# L02 同一份读数、只有不可表示（预算内）：仍走操作协议（对照，不许一刀切）
# ---------------------------------------------------------------------------
def test_l02_unrepresentable_alone_still_uses_the_ops_protocol(monkeypatch):
    """只有不可表示 ⇒ `expressible=True` ⇒ 操作协议 + **操作式措辞**（不许整份重写）."""
    _p = _legal_plan(dialogue_lines=[_LINE, _LONG])
    _p["shot_beats"] = [
        "节拍：甲板上有风｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % _LINE,
        "节拍：海面｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词2" % _LONG,
        "节拍：收尾｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _LINE]
    _p = o._validate_outline(_p, _SRC_LONG)
    _ps = o.plan_acceptance_problems(_p, _SRC_LONG)
    assert [x["code"] for x in _ps] == ["budget_unrepresentable"], _ps
    _cap = o._fix_capability(_ps)
    assert _cap["expressible"] is True and _cap["undeclared"] == [], _cap
    # 合法操作：换成**已验算的可表示替代**（菜单给的那一句），代码在旧计划上应用
    from step01_script_gen.plan_feasibility import representable_replacements
    _cands = representable_replacements(_LONG, _SRC_LONG)
    assert _cands, "夹具出处里必须有**已验算的可表示替代**（否则本条判据无从验证）"
    # 反馈里必须把这份**已验算的菜单**摆出来（不是让模型自由重抽）
    _svc0 = _svc_for(monkeypatch, [])
    o._budget_feedback_block(
        {"dialogue_unrepresentable": [{"line": _LONG, "index": 2, "est_sec": 20.0,
                                       "reason": "句内无句末边界"}],
         "problems": [{"code": "budget_unrepresentable", "kind": "dialogue", "index": 2}]},
        _p, _SRC_LONG)
    # 换过台词就必须**逐项声明事实承载**（既有裁决：替换台词后事实/钩子都要有明确承载）
    _payload = {"dialogue_ops": [{"index": 2, "replace_with": _cands[0]}],
                "beat_ops": [],
                "fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}],
                "hook_carrier": ""}
    _r, _svc = _revise(monkeypatch, _p, _ps, [_payload], novel_text=_SRC_LONG)
    assert _schema_name(_svc.reqs[0]) == "replan_ops", _schema_name(_svc.reqs[0])
    assert _r["evidence"]["route"] == "ops", _r["evidence"]
    _q_text = _svc.reqs[0].user_prompt
    # 两轮的基线**都在**（`render_plan_baseline` 的标题固定），分野在"怎么用它"：
    # 操作轮 = 只读上下文；完整计划轮 = 逐字基线、原样返回
    assert "只读上下文，不要重写它" in _q_text, "操作轮必须把计划标成只读上下文"
    assert "其余字段请**原样返回**" not in _q_text, "操作轮不许要求整份重写"
    assert "beat_ops" in _q_text, "操作轮的措辞必须按操作协议写"
    assert "允许改动 `shot_beats` 的正文" in _q_text, "操作轮的边界按操作协议写"
    assert _r["kind"] == "applied", _r
    assert o.plan_acceptance_problems(_r["plan"], _SRC_LONG) == [], _r["plan"]


# ---------------------------------------------------------------------------
# L03 旁白轴：同一条裁决，两个落点都认
# ---------------------------------------------------------------------------
def test_l03_the_narration_axis_is_decided_by_the_same_verdict(monkeypatch):
    """旁白不可单镜表示 + 总量超限 ⇒ 完整计划；单独出现 ⇒ 操作协议（落点含旁白表）."""
    _cap_field = o.PLAN_FIX_CAPABILITY["budget_unrepresentable"][1]
    assert "narration_lines" in _cap_field, _cap_field
    # ① 混合（旁白轴）⇒ 完整计划接口
    _p = _mixed_narration_plan()
    _ps = o.plan_acceptance_problems(_p, _SRC_LONG)
    _codes = {x["code"] for x in _ps}
    assert "budget_unrepresentable" in _codes and "budget_infeasible" in _codes, _codes
    assert any(x.get("kind") == "narration" for x in _ps
               if x["code"] == "budget_unrepresentable"), _ps
    assert o._fix_capability(_ps)["expressible"] is False, o._fix_capability(_ps)
    # 合法修法：把那条长旁白换成**可表示**的原文短句（整表声明；事实不靠它承载）
    _q = copy.deepcopy(_p)
    _q["dialogue_lines"] = [_LINE]
    _q["shot_beats"] = [
        _b if (_i == 0 or "载体：旁白" in _b) else _b.rsplit("载体：", 1)[0] + "载体：画面"
        for _i, _b in enumerate(_q["shot_beats"])]
    _q["narration_lines"] = [{"text": _LINE, "source": _LINE, "fact_refs": []}]
    _r, _svc = _revise(monkeypatch, _p, _ps, [_q], novel_text=_SRC_LONG)
    assert _schema_name(_svc.reqs[0]) == o.OUTLINE_SCHEMA["name"], _schema_name(_svc.reqs[0])
    assert _r["kind"] == "applied" and o.plan_acceptance_problems(_r["plan"], _SRC_LONG) == [], _r
    # ② 只有旁白不可表示 ⇒ 操作协议（对照）
    _p2 = _only_narration_unrep_plan()
    _ps2 = o.plan_acceptance_problems(_p2, _SRC_LONG)
    assert [x["code"] for x in _ps2] == ["budget_unrepresentable"], _ps2
    assert o._fix_capability(_ps2)["expressible"] is True, o._fix_capability(_ps2)
    _payload = {"dialogue_ops": [], "beat_ops": [],
                "narration_lines": [{"text": _LINE, "source": _LINE, "fact_refs": []}],
                "hook_carrier": ""}
    _r2, _svc2 = _revise(monkeypatch, _p2, _ps2, [_payload], novel_text=_SRC_LONG)
    assert _schema_name(_svc2.reqs[0]) == "replan_ops", _schema_name(_svc2.reqs[0])
    assert _r2["kind"] == "applied", _r2
    assert _r2["plan"]["narration_lines"] == [
        {"text": _LINE, "source": _LINE, "fact_refs": []}], _r2["plan"]["narration_lines"]


# ---------------------------------------------------------------------------
# L04 停止标记「无合适替代」：反馈不许承诺守卫禁止的事
# ---------------------------------------------------------------------------
#: 与**核心守卫**矛盾的承诺（`_plan_core_preserved`：事实本体、事件、节拍正文与条数
#: 一律逐项不变）。"`dialogue_lines` 可以少条目"是**合法**的（它是可改字段），
#: 判据必须精确到"删**事实**"这件事上。
_FORBIDDEN_PROMISES = (
    "`required_facts` 可以少条目",
    "不列进 `required_facts`",
    "取舍掉它们承载的内容",
    "把与它绑定的节拍、必保留事实一并取舍掉",
    "删掉装不下的那段情节",
)


def _two_fact_plan():
    """**验收干净**的两条必保留事实计划（一条台词承载、一条画面承载）+ 规划层停止标记.

    两条事实是"删掉一条"这类反例的前提（只有一条时 `required_facts: []` 会先撞结构门，
    判成 `structure_field_empty`，验不到核心守卫这一层）。
    """
    _p = _legal_plan(time_budget="无合适替代——本集装不下")
    _p["required_facts"] = [
        "甲板上有风，帆布猎猎作响｜原文证据：“%s”｜承载：B1/台词1" % _LINE,
        "船仍向前行驶｜原文证据：“%s”｜承载：B2/画面" % _LONG]
    _p["shot_beats"] = [
        "节拍：甲板上有风，帆布猎猎作响｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % _LINE,
        "节拍：船仍向前行驶｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _LONG,
        "节拍：收尾｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _LINE]
    return _p


def test_l04_the_no_alternative_feedback_never_promises_what_the_guard_forbids(monkeypatch):
    """反馈与守卫必须一致：**按反馈做**就能过守卫，否则这一轮白费（复核之十二 P1）."""
    _p = o._validate_outline(_two_fact_plan(), _SRC_LONG)
    assert _p is not None
    _ps = o.plan_acceptance_problems(_p, _SRC_LONG)
    assert [x["code"] for x in _ps] == ["budget_no_alternative"], _ps
    # ① 渲染出来的反馈里**不许**出现与核心守卫矛盾的承诺
    _q = copy.deepcopy(_p)
    _q["time_budget"] = "对白约3秒，画面约5秒"
    _r, _svc = _revise(monkeypatch, _p, _ps, [_q], novel_text=_SRC_LONG)
    _text = _svc.reqs[0].user_prompt
    _bad = [x for x in _FORBIDDEN_PROMISES if x in _text]
    assert not _bad, "反馈承诺了守卫禁止的事：%s" % _bad
    assert "一条都不许删" in _text, "必须说清必保留事实的硬边界"
    assert _r["evidence"]["route"] == "full_plan", _r["evidence"]
    # ② 照**旧反馈**的承诺做（删掉一条必保留事实）⇒ 守卫拒收（这条守卫是已定裁决，不放宽）
    _del = copy.deepcopy(_q)
    _del["required_facts"] = list(_del["required_facts"][:1])   # 照旧反馈的承诺：少一条
    _r2, _ = _revise(monkeypatch, _p, _ps, [_del], novel_text=_SRC_LONG)
    assert _r2["kind"] == "illegal", _r2
    assert _r2["detail"] == "core_field_changed: required_facts", _r2
    # ③ **合法成功对照**：按反馈的**合法**出路（事实一条不动，只改承载登记与标记）⇒
    #    采纳且**统一验收归零**
    # （本夹具的承载本来就是一致的——唯一的问题是规划层那句自报标记；把标记改回正常
    #   时间倾向、**事实一条不动**，是反馈里明说的合法出路）
    _r3, _ = _revise(monkeypatch, _p, _ps, [copy.deepcopy(_q)], novel_text=_SRC_LONG)
    assert _r3["kind"] == "applied", _r3
    assert o.plan_acceptance_problems(_r3["plan"], _SRC_LONG) == [], _r3["plan"]
    assert _r3["plan"]["required_facts"] == _p["required_facts"], "事实必须逐字不动"


# ---------------------------------------------------------------------------
# L05 停止标记「载体缺失、无合适承载」：同一口径
# ---------------------------------------------------------------------------
def test_l05_the_no_carrier_marker_feedback_matches_the_guard(monkeypatch):
    """`carrier_missing_annotation` ⇒；反馈说清"只许改载体登记段"，成功出口可达."""
    _p = _legal_plan(time_budget="载体缺失：无合适承载")
    _ps = o.plan_acceptance_problems(_p, _SRC)
    assert [x["code"] for x in _ps] == ["carrier_missing_annotation"], _ps
    _cap = o._fix_capability(_ps)
    assert _cap["expressible"] is False and _cap["undeclared"] == [], _cap
    _q = copy.deepcopy(_p)
    _q["time_budget"] = "对白约3秒，画面约5秒"
    _r, _svc = _revise(monkeypatch, _p, _ps, [_q])
    _text = _svc.reqs[0].user_prompt
    assert not [x for x in _FORBIDDEN_PROMISES if x in _text], _text[-500:]
    assert _r["evidence"]["route"] == "full_plan", _r["evidence"]
    assert _r["kind"] == "applied" and o.plan_acceptance_problems(_r["plan"], _SRC) == [], _r
    # 反例：连**节拍正文**一起改（守卫同样禁止）⇒ 拒收，且原因点到字段
    _bad = copy.deepcopy(_q)
    _bad["shot_beats"] = [str(x).replace("节拍：甲板上有风", "节拍：甲板无人") for x in _bad["shot_beats"]]
    _r2, _ = _revise(monkeypatch, _p, _ps, [_bad])
    assert _r2["kind"] == "illegal" and _r2["detail"] == "core_field_changed: shot_beats", _r2


# ---------------------------------------------------------------------------
# L06 接口是**唯一开关**：同一份读数，两个 route ⇒ schema 与措辞同时翻转
# ---------------------------------------------------------------------------
def test_l06_the_route_is_the_single_switch_for_schema_and_wording(monkeypatch):
    """同一份 `rep` + 不同 `route` ⇒ schema 与反馈措辞**一起**变（不是各判各的）.

    判别性：把 `route` 参数去掉、回到"按 rep 读数自选"的旧写法，本用例立刻转红——
    那时两次调用的 schema 与措辞都由 `rep` 决定，传什么都不变。
    """
    _rep = {"budget": {"min": 60, "max": 90}, "dialogue_lines": 2, "dialogue_sec": 100.0,
            "lower_bound_sec": 100.0, "dialogue_unrepresentable":
                [{"line": _LONG, "index": 2, "est_sec": 20.0, "reason": "句内无句末边界"}],
            "beat_carrier_duplicate": [], "dialogue_orphan": [],
            "beat_carrier_unlabeled": [], "beat_carrier_dangling": [],
            "problems": [{"code": "budget_infeasible", "fix": "reselect_dialogue"},
                         {"code": "budget_unrepresentable", "kind": "dialogue", "index": 2}]}
    _plan = _legal_plan(dialogue_lines=[_LINE, _LONG])
    _p_full = copy.deepcopy(_plan)
    _p_full["shot_beats"] = _mixed_dialogue_fix(_p_full)["shot_beats"]
    _out = {}
    for _route, _payload in (("ops", {"dialogue_ops": [], "beat_ops": [],
                                      "fact_carriers": [], "hook_carrier": ""}),
                             ("full_plan", _plan)):
        _svc = _svc_for(monkeypatch, [copy.deepcopy(_payload)])
        o.replan_dialogue_within_budget(copy.deepcopy(_plan), _rep, _SRC_LONG,
                                        route=_route)
        _out[_route] = (_schema_name(_svc.reqs[0]), _svc.reqs[0].user_prompt)
    assert _out["ops"][0] == "replan_ops", _out["ops"][0]
    assert _out["full_plan"][0] == o.OUTLINE_SCHEMA["name"], _out["full_plan"][0]
    assert "只读上下文" in _out["ops"][1] and "只读上下文" not in _out["full_plan"][1]
    assert "逐字基线" in _out["full_plan"][1]
    assert "只读上下文——这一轮只回操作" in _out["ops"][1], "基线那句话也要按接口说"
    assert "只读上下文——这一轮只回操作" not in _out["full_plan"][1]
    # 接口绑定的两条建议也跟着翻（ops 才允许改节拍正文）
    assert "允许改动 `shot_beats` 的正文" in _out["ops"][1]
    assert "允许改动 `shot_beats` 的正文" not in _out["full_plan"][1]
    assert "正文与「原文依据」一字不许改" in _out["full_plan"][1]


# ---------------------------------------------------------------------------
# L07 两个"回一整份计划"的入口共用同一份前置门
# ---------------------------------------------------------------------------
def test_l07_both_full_plan_entries_share_one_precheck(monkeypatch):
    """只回操作的响应：两个入口都如实报"缺 N 个顶层字段"（不许报成 `core_field_changed`）."""
    _partial = {"dialogue_ops": [], "beat_ops": []}
    # 桩服务必须先装好（不许真调模型）；两条入口各消费一份响应
    _svc = _svc_for(monkeypatch, [copy.deepcopy(_partial), copy.deepcopy(_partial)])
    # ① 完整计划修订入口
    _p = _legal_plan(dialogue_lines=[])
    _w1 = o.revise_plan_full(_p, o.plan_acceptance_problems(_p, _SRC), _SRC)[1]
    # ② 预算重选的整份重写分支（复核之十二前：这一路只说一句 invalid_result）
    _rep = {"budget": {"min": 60, "max": 90}, "dialogue_lines": 1, "dialogue_sec": 100.0,
            "lower_bound_sec": 100.0, "dialogue_unrepresentable": [],
            "beat_carrier_duplicate": [], "dialogue_orphan": [],
            "beat_carrier_unlabeled": [], "beat_carrier_dangling": [],
            "problems": [{"code": "budget_infeasible", "fix": "reselect_dialogue"}]}
    _w2 = o.replan_dialogue_within_budget(_legal_plan(), _rep, _SRC, route="full_plan")[1]
    for _w in (_w1, _w2):
        assert _w.startswith("invalid_result: 这轮要的是**完整计划**"), _w
        assert "顶层字段" in _w and "不是只回操作" in _w, _w
        assert "core_field_changed" not in _w, _w
    assert _schema_name(_svc.reqs[0]) == o.OUTLINE_SCHEMA["name"]


# ---------------------------------------------------------------------------
# L08 机械：所有渲染出来的反馈块都不许出现与守卫矛盾的承诺
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", [
    "_PLAN_BUDGET_FEEDBACK", "_PLAN_REPRESENTABILITY_FEEDBACK", "_PLAN_CARRIER_FEEDBACK",
    "_PLAN_CARRIER_RELATION_FEEDBACK", "_PLAN_NO_ALTERNATIVE_FEEDBACK",
    "_PLAN_NO_CARRIER_FEEDBACK", "_PLAN_OPS_INSTRUCTION", "_FULL_PLAN_REVISION_RULE",
])
def test_l08_no_template_promises_what_the_core_guard_forbids(name):
    """模板层面钉住"建议必须符合核心守卫"：删事实/改事件顺序的措辞一律不许出现."""
    _t = getattr(o, name)
    assert isinstance(_t, str) and _t.strip(), name
    _bad = [x for x in _FORBIDDEN_PROMISES if x in _t]
    assert not _bad, "%s 里出现了与核心守卫矛盾的承诺：%s" % (name, _bad)
    # 「必保留事实」只允许以"不许删"的形态出现
    for _ln in _t.split(chr(10)):
        if "required_facts" in _ln and "删" in _ln:
            assert "不许删" in _ln or "不得删" in _ln, _ln.strip()


# ---------------------------------------------------------------------------
# L09 归因证据：接口与反馈 flavor 分账
# ---------------------------------------------------------------------------
def test_l09_evidence_records_the_interface_and_the_feedback_flavour(monkeypatch):
    """预算读数是**反馈 flavor**、不是接口：两条路各记一次，事后能分辨."""
    # ① 预算读数 + 不可表达 ⇒ route=full_plan，budget_context=True
    _p = _mixed_dialogue_plan()
    _ps = o.plan_acceptance_problems(_p, _SRC_LONG)
    _r, _ = _revise(monkeypatch, _p, _ps, [_mixed_dialogue_fix(_p)], novel_text=_SRC_LONG)
    _ev = _r["evidence"]
    assert _ev["route"] == "full_plan" and _ev["budget_context"] is True, _ev
    assert _ev["fix_capability"]["inexpressible"] == ["budget_infeasible"], _ev
    # ② 契约类问题（非预算）⇒ route=ops，budget_context=False
    _p2 = _legal_plan()
    _p2["shot_beats"][1] = str(_p2["shot_beats"][1]).rsplit("载体：", 1)[0] + "载体：台词9"
    _p2 = o._validate_outline(_p2, _SRC)
    _ps2 = o.plan_acceptance_problems(_p2, _SRC)
    assert _ps2 and not {x["code"] for x in _ps2} & {"budget_infeasible"}, _ps2
    _r2, _ = _revise(monkeypatch, _p2, _ps2,
                     [{"dialogue_ops": [], "beat_ops": [],
                       "fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}],
                       "hook_carrier": ""}])
    assert _r2["evidence"]["budget_context"] is False, _r2["evidence"]


# ---------------------------------------------------------------------------
# L10 没有生产者记录 ⇒ fail-closed（走能力更强的接口）
# ---------------------------------------------------------------------------
def test_l10_a_rep_without_producer_records_is_fail_closed(monkeypatch):
    """**无从证明"表达得了"** 时不许默认操作式（"未声明"不等于"可修"）.

    `problems` 缺席（老入口/手工构造的 rep）⇒ `_replan_route` 判完整计划；空列表
    （确实没有发现）⇒ 操作式。真入口：同一份 rep 走 `replan_dialogue_within_budget`
    时 schema 随之而变。
    """
    assert o._replan_route(None) == "full_plan", "缺记录 ⇒ 不许默认操作式"
    assert o._replan_route([]) == "ops"
    _rep = {"budget": {"min": 60, "max": 90}, "dialogue_lines": 1, "dialogue_sec": 3.0,
            "lower_bound_sec": 3.0, "dialogue_unrepresentable": [],
            "beat_carrier_duplicate": [], "dialogue_orphan": [],
            "beat_carrier_unlabeled": [], "beat_carrier_dangling": []}
    _svc = _svc_for(monkeypatch, [_legal_plan()])
    o.replan_dialogue_within_budget(_legal_plan(), _rep, _SRC)
    assert _schema_name(_svc.reqs[0]) == o.OUTLINE_SCHEMA["name"], _schema_name(_svc.reqs[0])

    # **真入口**（复核之十二 P2）：入口必须调**同一个** `_replan_route`，而不是手抄
    # 它的表达式——抄写时"缺记录 ⇒ fail-closed"那条语义只留在助手函数里，
    # `revise_plan_once(..., problems=None, ...)` 会照样发操作协议（隔离复现）。
    _svc2 = _svc_for(monkeypatch, [_legal_plan()])
    _r = o.revise_plan_once(_legal_plan(), None, RevisionBudget(deadline_sec=60),
                            novel_text=_SRC)
    assert _schema_name(_svc2.reqs[0]) == o.OUTLINE_SCHEMA["name"], _schema_name(_svc2.reqs[0])
    assert _r["evidence"]["route"] == "full_plan", _r["evidence"]
    # 空表（确实没有发现）⇒ 操作协议
    _svc3 = _svc_for(monkeypatch, [{"dialogue_ops": [], "beat_ops": [],
                                    "fact_carriers": [], "hook_carrier": ""}])
    _r2 = o.revise_plan_once(_legal_plan(), [], RevisionBudget(deadline_sec=60),
                             novel_text=_SRC)
    assert _schema_name(_svc3.reqs[0]) == "replan_ops", _schema_name(_svc3.reqs[0])
    assert _r2["evidence"]["route"] == "ops", _r2["evidence"]


# ---------------------------------------------------------------------------
# L11 不许要求"本轮做不到的节拍操作"（建议必须符合接口能力）
# ---------------------------------------------------------------------------
def test_l11_no_round_asks_for_a_beat_edit_it_cannot_do(monkeypatch):
    """**复核之十二 P2 的实跑根因**：完整计划轮的规则块不许要求增删/拆分节拍.

    实测（`20261003T030007` ch2）：首版 14 条问题里带着「请把它**拆成两条**」这句**建议**，
    而完整计划轮的核心守卫禁止改 `shot_beats`（条数与正文逐项比对）⇒ 模型照建议拆了节拍，
    **连续 3 轮**都被 `core_field_changed: shot_beats` 拒收。生产者只说"要求"、接口层说
    "能用哪些操作"，两边不许互相矛盾。
    """
    _rule = o.render_full_plan_rule()
    assert "不许新增/删除/拆分/合并条目" in _rule, "守卫比的是整张表，规则就得说出来"
    assert "本轮不许改动节拍的条数与正文" in _rule, "禁令必须写在规则块里"
    for _bad in ("拆成两条", "补一条", "新增一条"):
        assert _bad not in _rule, "完整计划轮不许要求它做不到的节拍操作：%s" % _bad
    assert "可以增删" in _rule, "必须给出**合法**替代路径（改承载登记 / 改选材）"
    # 对照一（2026-10-18 契约变更）：**首版**也不再要求"拆成两条"——
    # 事实画面承载与声音归属可以落在**同一节拍**；不是把能力删掉，而是它本来就不必拆。
    _first = o.OUTLINE_PROMPT.format(novel_text="原文", beat_edit_rule=o._BEAT_EDIT_RULE["first_version"])
    assert "可以落在**同一节拍**" in _first
    assert "拆成两条（画面一条、台词一条）" not in _first
    _rev = o.OUTLINE_PROMPT.format(novel_text="原文", beat_edit_rule=o._BEAT_EDIT_RULE["revision"])
    assert "拆成两条" not in _rev and "不许增删/拆分/合并节拍" in _rev
    # 对照二：操作式轮**有**拆分能力（不是把能力删了，而是按接口说）
    assert "beat_ops" in o._PLAN_OPS_INSTRUCTION and "拆" in o._PLAN_OPS_INSTRUCTION
    # 生产者记录**中性**：只说要求，不说用哪个操作。
    # 2026-10-18：`画面` 落在承声节拍已被允许 ⇒ 改用"载体登记**非法**"触发同一族。
    _p = _legal_plan()
    _p["required_facts"] = [
        re.sub("｜承载：[^｜]*$", "｜承载：B1/画面", str(_p["required_facts"][0]))]
    _p["shot_beats"][0] = str(_p["shot_beats"][0]).rsplit("｜载体：", 1)[0] + "｜载体：乱写"
    _p = o._validate_outline(_p, _SRC)
    _rec = [x for x in o.plan_acceptance_problems(_p, _SRC)
            if x["code"] == "carrier_fact_visual_mismatch"]
    assert _rec, "夹具必须触发 `carrier_fact_visual_mismatch`（该节拍缺少合法载体登记）"
    assert "合法载体登记" in str(_rec[0]["reason"]), _rec[0]
    assert "分属**不同节拍**" not in str(_rec[0]["reason"]), "旧契约口径不得残留"
    assert "拆成两条" not in str(_rec[0]["reason"]), "生产者不许给操作建议"
    # 真入口：这类问题走**完整计划**轮时，请求里没有"拆/加节拍"的指令，但有合法替代路径
    _m = _mixed_dialogue_plan()
    _m["required_facts"] = [
        re.sub("｜承载：[^｜]*$", "｜承载：B1/画面", str(x)) for x in _m["required_facts"]]
    # 2026-10-18：`画面` 落在承声节拍已被允许 ⇒ 用"载体登记**非法**"触发同一族
    _m["shot_beats"][0] = str(_m["shot_beats"][0]).rsplit("｜载体：", 1)[0] + "｜载体：乱写"
    _ps = o.plan_acceptance_problems(_m, _SRC_LONG)
    assert "carrier_fact_visual_mismatch" in {x["code"] for x in _ps}, _ps
    assert o._fix_capability(_ps)["expressible"] is False, o._fix_capability(_ps)
    _r, _svc = _revise(monkeypatch, _m, _ps, [_mixed_dialogue_fix(_m)],
                       novel_text=_SRC_LONG)
    _q = _svc.reqs[0].user_prompt
    for _bad in ("拆成两条", "补一条承载"):
        assert _bad not in _q, "请求里出现了本轮做不到的节拍操作：%s" % _bad
    assert "本轮不许改动节拍的条数与正文" in _q and "可以增删" in _q,         "必须明说做不到 + 给合法替代"
    assert _r["evidence"]["route"] == "full_plan", _r["evidence"]


# ---------------------------------------------------------------------------
# L12 "做不到时怎么表达"也按接口渲染（复核之十三 P1）
# ---------------------------------------------------------------------------
def _only_unrep_plan():
    """只有一句不可单镜表示（预算内）⇒ 操作协议表达得了."""
    _p = _legal_plan(dialogue_lines=[_LINE, _LONG])
    _p["shot_beats"] = [
        "节拍：甲板上有风｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % _LINE,
        "节拍：海面｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词2" % _LONG,
        "节拍：收尾｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _LINE]
    return o._validate_outline(_p, _SRC_LONG)


def test_l12_the_give_up_expression_is_rendered_per_interface(monkeypatch):
    """**复核之十三 P1**：操作协议里没有 `time_budget`，就不许让模型去写它.

    真实修订入口抓到的形态：请求 schema 是 `replan_ops`，提示却写着「请在 `time_budget`
    里明确写明…」——模型照做 ⇒ `未知操作键 time_budget` ⇒ 整轮白费。
    "做不到时的表达方式"与"成功修法"一样，都必须按接口渲染。
    """
    _p = _only_unrep_plan()
    _ps = o.plan_acceptance_problems(_p, _SRC_LONG)
    assert [x["code"] for x in _ps] == ["budget_unrepresentable"], _ps
    assert o._fix_capability(_ps)["expressible"] is True, o._fix_capability(_ps)
    _empty_ops = {"dialogue_ops": [], "beat_ops": [], "fact_carriers": []}
    _r, _svc = _revise(monkeypatch, _p, _ps, [dict(_empty_ops)], novel_text=_SRC_LONG)
    _q = _svc.reqs[0].user_prompt
    assert _schema_name(_svc.reqs[0]) == "replan_ops", _schema_name(_svc.reqs[0])
    assert "回空操作" in _q, "操作式轮的'改不动'必须给**操作协议里存在**的表达方式"
    assert "请在 `time_budget`" not in _q, "操作式轮不许要求写操作协议碰不到的字段"
    # 建议不是空头支票：空操作**真的**被记成 no_change（与应用器行为一致）
    assert _r["kind"] == "no_change" and _r["evidence"]["applied"] is False, _r
    # 对照：**完整计划**轮同一条建议渲染成"写停止标记"（那边 `time_budget` 可改）
    _m = _mixed_dialogue_plan()
    _mps = o.plan_acceptance_problems(_m, _SRC_LONG)
    assert o._fix_capability(_mps)["expressible"] is False, o._fix_capability(_mps)
    _r2, _svc2 = _revise(monkeypatch, _m, _mps, [_mixed_dialogue_fix(_m)], novel_text=_SRC_LONG)
    _q2 = _svc2.reqs[0].user_prompt
    assert _schema_name(_svc2.reqs[0]) == o.OUTLINE_SCHEMA["name"]
    assert "请在 `time_budget` 里" in _q2, "完整计划轮该给这条合法出路"


# ---------------------------------------------------------------------------
# L13 两个接口的**全部**建议：不许出现"操作协议碰不到的字段"的指令
# ---------------------------------------------------------------------------
#: "让模型去写某个字段"的措辞（探针；"不要写 `X`"这种**禁令**不算——见下面的回看窗口）
_DIRECTIVE = re.compile("(?:请|要|需|去|改|写|更新|给)[^。；\n]{0,14}`([a-z_]+)`")
_NEGATIONS = ("不要", "不许", "不得", "别", "禁止", "碰不到")


#: 操作协议**能改到的计划字段**（操作键 → 落点；与 K05b 同一张映射，逐项核过）
_OP_TARGET_FIELDS = ("dialogue_lines", "shot_beats", "required_facts",
                     "hook_carrier", "hook_spoken", "narration_lines")


def _non_op_directives(text: str) -> list:
    """提示里"让模型去写**操作协议碰不到**的字段"的地方（禁令除外）.

    判据是**落点字段**而不是操作键名：操作协议用 `dialogue_ops`/`beat_ops`/`fact_carriers`
    /`hook_carrier`/`hook_spoken`/`narration_lines` 六个键，改到的字段正是 `_OP_TARGET_FIELDS`
    那六个——所以"改 `shot_beats` 的正文"（经 `beat_ops`）是**合法**的，而"写 `omittable`"
    不是（没有任何操作键碰得到它）。
    """
    from step01_script_gen.plan_feasibility import REPLAN_OP_KEYS
    from _text_gen.deepseek_schema import OUTLINE_SCHEMA as _OS
    assert set(_OP_TARGET_FIELDS) == {"dialogue_lines", "shot_beats", "required_facts",
                                      "hook_carrier", "hook_spoken", "narration_lines"}
    assert set(REPLAN_OP_KEYS) == {"dialogue_ops", "beat_ops", "fact_carriers",
                                   "hook_carrier", "hook_spoken", "narration_lines"}
    _fields = set(((_OS.get("schema") or {}).get("required") or []))
    _out = []
    for _m in _DIRECTIVE.finditer(text):
        _key = _m.group(1)
        if _key not in _fields or _key in _OP_TARGET_FIELDS:
            continue
        # 禁令不算指令："**不要**去写 `X`" 里正则从"要"起match，回看窗口要**多吃两字**
        _head = text[max(0, _m.start() - 8):_m.start() + 2]
        if any(_n in _head for _n in _NEGATIONS):
            continue
        _out.append((_key, _m.group(0)))
    return _out


def test_l13_the_probe_actually_catches_the_old_wording():
    """先证明探针有效：**旧文案**必须被抓出来（否则下面的"没有"是空条件）."""
    _old = ("7. 若原文确实没有能承载这些事实的合法台词与节拍，请在 `time_budget` 里"
            "**明确写明**「台词不可表示：无合适替代」——代码会据此显式停止。")
    assert _non_op_directives(_old), "探针必须能抓到旧文案"
    assert _non_op_directives("本轮不要写 `time_budget`（操作协议碰不到）") == [], "禁令不算指令"


def test_l13_no_ops_round_asks_for_a_field_the_protocol_lacks(monkeypatch):
    """操作式轮的**每一个**渲染块都不许有"去写非操作字段"的指令（含无人触发的分支）."""
    _plan = _legal_plan(dialogue_lines=[_LINE, _LONG])
    _rep = {
        "budget": {"min": 60, "max": 90}, "dialogue_lines": 3, "dialogue_sec": 100.0,
        "narration_lines": 2, "narration_sec": 10.0, "lower_bound_sec": 110.0,
        "beats_floor_basis": {"beats": 6},
        "dialogue_unrepresentable": [{"line": _LONG, "index": 2, "est_sec": 20.0,
                                      "reason": "句内无句末边界"}],
        "beat_carrier_duplicate": ["B2"], "dialogue_orphan": ["N3", "台词2"],
        "beat_carrier_unlabeled": ["B5"], "beat_carrier_dangling": ["B6 载体「台词9」不在表内"],
        "problems": [{"code": "budget_unrepresentable", "kind": "dialogue", "index": 2},
                     {"code": "budget_no_alternative"}, {"code": "carrier_missing_annotation"}],
    }
    # 一整份"什么分支都亮"的反馈（含预算块/可表示性/承载关系/悬空/两条停止标记）
    _ops = o._budget_feedback_block(_rep, _plan, _SRC_LONG, route="ops")
    assert "回空操作" in _ops and "只读上下文" in _ops, "块要真的渲染出来（不是空串）"
    assert _non_op_directives(_ops) == [], _non_op_directives(_ops)
    # 对照：同一份 rep 走**完整计划**轮时，写 `time_budget` 是**合法**出路（不是漏网）
    _full = o._budget_feedback_block(_rep, _plan, _SRC_LONG, route="full_plan")
    assert "请在 `time_budget` 里" in _full
    # 真入口再扫一遍：操作式轮的实际提示
    _p = _only_unrep_plan()
    _ps = o.plan_acceptance_problems(_p, _SRC_LONG)
    _r, _svc = _revise(monkeypatch, _p, _ps, [{"dialogue_ops": [], "beat_ops": [],
                                               "fact_carriers": []}], novel_text=_SRC_LONG)
    assert _non_op_directives(_svc.reqs[0].user_prompt) == [], \
        _non_op_directives(_svc.reqs[0].user_prompt)

# ---------------------------------------------------------------------------
# L14 闭合拒收的**身份清单**不许静默截断（复核之十五后第一轮实跑暴露）
# ---------------------------------------------------------------------------
def _orphan_dialogue_plan(n: int = 5):
    """`n` 条台词**没有任何节拍承载**（全是画面节拍）——本批只要动节拍就走闭合检查."""
    _p = _legal_plan(dialogue_lines=[_LINE] * n)
    _p["required_facts"] = ["甲板上有风｜原文证据：“%s”｜承载：B1/画面" % _LINE]
    _p["shot_beats"] = [
        "节拍：海面%d｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % (_i, _LINE)
        for _i in range(1, n + 1)]
    return o._validate_outline(_p, _SRC)


def test_l14_the_closure_rejection_names_every_orphan():
    """**实跑暴露**（批 20261003T041720 ch1 r3）：拒收消息原为 `[:4]` 静默省略。

    为什么是缺陷而不是措辞问题：这条字符串进 `replan_rejections`，**随下一轮请求**发给
    模型——只点名前 4 条会让模型以为剩下的不存在（与复核之六 P2「反馈保留完整问题身份」
    同一条口径，只是落点从验收反馈换到了拒收记录）。
    """
    _ops = {"beat_ops": [{"op": "insert_after", "index": 5,
                          "new_text": "节拍：补一条｜本镜信息目的：推进｜原文依据：“%s”" % _LINE,
                          "new_carrier": "画面"}]}
    # ① 5 条孤儿：全部点名（实跑那一例正好是 5 条，旧写法只给 4 条）
    _new, _why = o.apply_replan_ops(copy.deepcopy(_orphan_dialogue_plan(5)), _ops, _SRC)
    assert _new is None and "无任何节拍承载" in _why, _why
    for _i in range(1, 6):
        assert "D%d" % _i in _why, "拒收消息漏了 D%d：%s" % (_i, _why)
    # ② 超上限：显式给总数，仍然不静默省略
    _many = _orphan_dialogue_plan(14)
    _new2, _why2 = o.apply_replan_ops(copy.deepcopy(_many), _ops, _SRC)
    assert _new2 is None and "无任何节拍承载" in _why2, _why2
    assert "共 14 条" in _why2, _why2
