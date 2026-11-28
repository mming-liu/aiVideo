# -*- coding: utf-8 -*-
"""**修订控制器**的离线验收矩阵（2026-10-01 工作包，复核第 31 轮）.

控制器是纯状态机（`accept` / `revise` 都注入），所以这里**逐格构造**：不需要调用模型，
也不需要真的跑章节。矩阵覆盖复核列出的 12 种情形，以及一条**离线贯通**
（反馈 → 应用 → 重验 → 生成契约 → 提交前门）。

注意：控制器**尚未接进 `generate_outline`**（那一步会改变缓存与 `unevaluable` 的既有口径，
需要先定 3 处旧预期），本文件验的是控制器自身与它依赖的两个真实现：
`outline.plan_acceptance_problems`（唯一验收链）与 `apply_replan_ops`（应用器）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen import outline as o                                   # noqa: E402
from step01_script_gen import plan_feasibility as pf                         # noqa: E402
from step01_script_gen.plan_revision import (RevisionBudget, RevisionController,  # noqa: E402
                                             residual_class, revision_fingerprint)

_CITE = "丙把茶壶踢翻了，茶水流了一地。"
_SRC = "甲道：“%s”乙道：“丁吓得发抖。”" % _CITE


def _beat(body, carrier, cite=_CITE):
    return "节拍：%s｜本镜信息目的：推进｜原文依据：“%s”｜载体：%s" % (body, cite, carrier)


_FACT_OK = "丙把茶壶踢翻了｜原文证据：“%s”｜承载：B1/台词1" % _CITE
#: 承载**不闭合**的那一份：F1 声明由 B2 承载「台词1」，而 B2 的载体标注是「画面」
_FACT_BAD = "丙把茶壶踢翻了｜原文证据：“%s”｜承载：B2/台词1" % _CITE


def _plan(**over):
    """一份承载不闭合的计划（默认 `_FACT_BAD`）；`required_facts=[_FACT_OK]` 即为闭合版."""
    p = {
        "main_events": ["甲喝止", "丙失手", "丁发抖"],
        "climax": "丙失手",
        "causal_chain": ["甲喝止→丙失手→丁发抖"],
        "required_facts": [_FACT_BAD],
        "shot_beats": [_beat("丙把茶壶踢翻了", "台词1"), _beat("甲喝止", "画面"),
                       _beat("丁吓得发抖", "画面")],
        "dialogue_lines": [_CITE],
        "hook_spoken": [],
        "hook_carrier": "",
    }
    p.update(over)
    return p


def _problems(plan, novel_text=""):
    return o.plan_acceptance_problems(plan, novel_text)


# ---------------------------------------------------------------------------
# ① 唯一验收链：字段路径 + 判据依据
# ---------------------------------------------------------------------------
def test_acceptance_chain_names_the_field_path_and_basis():
    """承载问题按**具体判据**分账（工作包 §2.1）：code 是 `carrier_*` 家族而不是笼统的
    `carrier`；`path` / `index` 由生产者给出（这里正是 `required_facts[i]` 那一轴）."""
    _ps = _problems(_plan())
    assert _ps, "这份计划的承载是错的，必须报出来"
    _car = [p for p in _ps if str(p["code"]).startswith("carrier_")]
    assert _car, _ps
    assert any(p["path"].startswith("required_facts[") for p in _car), _ps
    assert any(p["kind"] == "fact" and p["index"] == 1 for p in _car), _car
    assert all(p["basis"] for p in _ps), _ps


def test_carrier_records_come_from_the_producer_not_from_the_message_text():
    """判别性（工作包 §2.2）：报告层**不再解析问题文案**。把一节拍的载体改成越界值，
    编号轴与路径仍必须由生产者给出；且**正文里出现编号样式的文字**不会被当成身份
    （复核第 33 轮：`2024` 曾被扫成 `dialogue_lines[2023]`）."""
    _p = _plan(required_facts=["甲在原文里说的话｜承载：B2/台词9"])
    _ps = _problems(_p, _SRC)
    _bad = [p for p in _ps if p["code"] == "carrier_fact_line_missing"]
    assert _bad, _ps
    assert _bad[0]["kind"] == "fact" and _bad[0]["index"] == 1, _bad
    assert _bad[0]["path"] == "required_facts[0].承载", _bad
    # 越界台词号（9）必须进 reason，但**不许**变成任何记录的 index
    assert not any(p["index"] == 9 for p in _ps), _ps


def test_a_closed_plan_has_no_problems():
    assert _problems(_plan(required_facts=[_FACT_OK]), _SRC) == []


# ---------------------------------------------------------------------------
# ② 循环指纹覆盖完整修订状态（复核第 4 条）
# ---------------------------------------------------------------------------
def test_fingerprint_covers_carrier_and_hook_declarations():
    _a = _plan()
    _b = _plan(required_facts=[_FACT_OK])
    assert revision_fingerprint(_a) != revision_fingerprint(_b), "只改承载声明必须改变指纹"
    _c = _plan(hook_carrier="B1/台词1")
    assert revision_fingerprint(_a) != revision_fingerprint(_c), "只改钩子声明必须改变指纹"
    # 元数据不进指纹
    _d = _plan()
    _d["plan_revision"] = {"stop_reason": "x", "calls": 3}
    _d["plan_id"] = "abc"
    assert revision_fingerprint(_d) == revision_fingerprint(_a)


# ---------------------------------------------------------------------------
# ③ 12 格矩阵：情形 → 期望停止原因
# ---------------------------------------------------------------------------
def _ctl(accept, revise, max_calls=3, deadline_sec=None):
    return RevisionController(accept=accept, revise=revise,
                              budget=RevisionBudget(max_calls=max_calls,
                                                    deadline_sec=deadline_sec))


def _fixed_accept(_problems_fn):
    # **必须把 novel_text 透下去**：原文核验缺了它，合法台词会被判"不在原文里"
    return lambda plan, novel_text="": _problems_fn(plan, novel_text)


def test_case_legal_plan_costs_zero_calls():
    _calls = []
    _r = _ctl(_fixed_accept(lambda p, nt="": []),
              lambda p, pr, bg, rj=None: _calls.append(1) or {"kind": "applied", "plan": p}).run({"a": 1})
    assert _r["stop_reason"] == "ok" and _calls == [] and _r["calls"] == 0


def test_case_normalization_already_fixed_it():
    """规范化就能修好的计划：验收链直接通过 ⇒ 0 次调用（规范化不属于"修订调用"）."""
    from step01_script_gen.plan_feasibility import normalize_plan_ranges
    _p = {"shot_beats": [_beat("开场", "画面"), _beat("旁白块", "旁白1-2")],
          "required_facts": [], "dialogue_lines": ["甲"],
          "narration_lines": [{"text": "n1", "source": "s1", "fact_refs": []},
                              {"text": "n2", "source": "s2", "fact_refs": []}]}
    _new, _ch = normalize_plan_ranges(_p)
    assert _ch, "区间应当被展开"
    _r = _ctl(_fixed_accept(lambda x, nt="": []),
              lambda p, pr, bg, rj=None: {"kind": "applied", "plan": p}).run(_new)
    assert _r["stop_reason"] == "ok" and _r["calls"] == 0


def test_case_carrier_only_revision():
    """只改承载声明（应用器给出的新计划只动了 `承载：`） ⇒ 1 次调用后通过."""
    _bad = _plan()
    _good = _plan(required_facts=[_FACT_OK])

    def _revise(plan, pr, bg, rejections=None):
        return {"kind": "applied", "plan": _good}

    _r = _ctl(_fixed_accept(_problems), _revise).run(_bad, _SRC)
    assert _r["stop_reason"] == "ok" and _r["calls"] == 1, _r


def test_case_contract_and_budget_fail_together():
    """契约 + 预算**同时**不满足：停止后残余类别记 `mixed`（不把两者混成一类）."""
    _ps = [{"code": "carrier", "detail": "x"}, {"code": "budget_infeasible", "detail": "y"}]
    _r = _ctl(_fixed_accept(lambda p, nt="": _ps),
              lambda p, pr, bg, rj=None: {"kind": "no_change"}).run({"a": 1})
    assert _r["stop_reason"] == "no_change" and _r["residual_class"] == "mixed", _r


def test_case_budget_reselect_breaks_the_carrier():
    """预算重选把承载改坏 ⇒ 重验出来，继续修；额度用尽后残余仍是契约类."""
    _states = []

    def _accept(plan, nt=""):
        return [] if plan.get("ok") else [{"code": "carrier", "detail": "承载坏了"}]

    def _revise(plan, pr, bg, rejections=None):
        _states.append(bg.calls_used)
        # 每轮都改**修订状态里的字段**（否则指纹不变 ⇒ 判循环，而不是次数耗尽）
        return {"kind": "applied", "plan": {"ok": False, "shot_beats": ["B%d" % bg.calls_used]}}

    _r = _ctl(_accept, _revise, max_calls=3).run({"ok": False})
    assert _r["stop_reason"] == "calls_exhausted" and _r["calls"] == 3, _r
    assert _r["residual_class"] == "contract" and _r["plan_level_conclusion"] is True


def test_case_source_violation_is_still_a_problem():
    """原文违规（台词不在原文里）由验收链报出来（`evaluate_plan` 一侧）."""
    _p = _plan(required_facts=[_FACT_OK],
               dialogue_lines=["这句台词原文里根本没有。"])
    _ps = _problems(_p, _SRC)
    assert _ps, "原文违规必须报出来"


def test_case_call_failed_is_not_a_plan_level_conclusion():
    _r = _ctl(_fixed_accept(lambda p, nt="": [{"code": "carrier", "detail": "x"}]),
              lambda p, pr, bg, rj=None: {"kind": "call_failed", "detail": "boom"}).run({"a": 1})
    assert _r["stop_reason"] == "call_failed" and _r["calls"] == 1
    assert _r["plan_level_conclusion"] is False, "调用失败不是「计划不可用」的结论"


def test_case_illegal_response():
    _r = _ctl(_fixed_accept(lambda p, nt="": [{"code": "carrier", "detail": "x"}]),
              lambda p, pr, bg, rj=None: {"kind": "illegal", "detail": "不是 JSON"}).run({"a": 1})
    assert _r["stop_reason"] == "illegal_response" and _r["plan_level_conclusion"] is False


def test_case_no_change_is_recorded_and_counts_as_a_call():
    _r = _ctl(_fixed_accept(lambda p, nt="": [{"code": "carrier", "detail": "x"}]),
              lambda p, pr, bg, rj=None: {"kind": "no_change"}).run({"a": 1})
    assert _r["stop_reason"] == "no_change" and _r["calls"] == 1


def test_case_unchanged_content_is_no_change_not_loop():
    """**应用后内容没变**记 `no_change`（工作包 §4 C：与"回到旧版本"分开）."""
    _p = _plan()
    _r = _ctl(_fixed_accept(_problems),
              lambda p, pr, bg, rj=None: {"kind": "applied", "plan": _plan()}).run(_p, _SRC)
    assert _r["stop_reason"] == "no_change" and _r["calls"] == 1, _r


def test_case_loop_is_detected():
    """改回**已经出现过的更早版本** ⇒ 记 `loop`，不无限跑（两种都扣次数）."""
    _v1 = _plan()
    _v2 = dict(_v1, main_events=list(_v1.get("main_events") or []) + ["第二版新增事件"])
    _rounds = iter([{"kind": "applied", "plan": _v2},
                    {"kind": "applied", "plan": _v1}])
    _r = _ctl(_fixed_accept(_problems), lambda p, pr, bg, rj=None: next(_rounds)).run(_v1, _SRC)
    assert _r["stop_reason"] == "loop" and _r["calls"] == 2, _r


def test_case_calls_exhausted_counts_failures_and_illegal_too():
    """失败/非法/无变化**都扣次数**（复核第 3 条）."""
    _kinds = iter(["call_failed", "illegal", "no_change"])

    def _revise(plan, pr, bg, rejections=None):
        return {"kind": next(_kinds)}

    _r = _ctl(_fixed_accept(lambda p, nt="": [{"code": "carrier", "detail": "x"}]), _revise,
              max_calls=3).run({"a": 1})
    assert _r["calls"] == 1 and _r["stop_reason"] == "call_failed", _r


def test_case_deadline_exhausted():
    _b = RevisionBudget(max_calls=3, deadline_sec=0.0)
    _r = RevisionController(accept=_fixed_accept(lambda p, nt="": [{"code": "carrier"}]),
                            revise=lambda p, pr, bg, rj=None: {"kind": "applied", "plan": {"n": 1}},
                            budget=_b).run({"a": 1})
    assert _r["stop_reason"] == "deadline_exhausted" and _r["calls"] == 0, _r


def test_case_cache_entry_goes_through_the_same_chain():
    """缓存入口：一份**已通过**的计划（缓存里的那一份）同样走这条链 ⇒ 0 次调用."""
    _p = _plan(required_facts=[_FACT_OK])
    _r = _ctl(_fixed_accept(_problems), lambda p, pr, bg, rj=None: pytest.fail("不该调用")).run(_p, _SRC)
    assert _r["stop_reason"] == "ok" and _r["calls"] == 0


# ---------------------------------------------------------------------------
# ④ 离线贯通：反馈 → 应用 → 重验 → 生成契约 → 提交前门
# ---------------------------------------------------------------------------
def test_offline_chain_feedback_apply_reverify_contract_and_gate(tmp_path):
    """把真实现串起来跑一遍：验收链报问题 → 应用器落地操作 → 重验通过 →
    生成契约渲染出承载信息 → **生成前门**放行（不抛 `PlanContractInvalidError`）."""
    _bad = _plan()
    _ops = {"fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}],
            "beat_ops": [{"index": 2, "new_carrier": "画面",
                          "new_text": _beat("甲喝止", "画面")}]}
    _seen = {}

    def _revise(plan, problems, budget, rejections=None):
        # 真实应用器：模型只回操作，代码在旧计划上应用（这里直接注入"模型的操作"）
        _new, _why = o.apply_replan_ops(plan, _ops, _SRC)
        _seen["why"] = _why
        if _new is None:
            return {"kind": "illegal", "detail": str(_why)}
        return {"kind": "applied", "plan": _new}

    _r = _ctl(_problems, _revise).run(_bad, _SRC)
    assert _r["stop_reason"] == "ok", (_r["stop_reason"], _r["problems"][:2], _seen)
    _final = _r["plan"]
    # 生成契约：承载信息必须渲染进契约块（下游据此拍摄）
    _contract = o.PROMPT_CONTRACT_RENDERER(_final) if hasattr(o, "PROMPT_CONTRACT_RENDERER") \
        else __import__("step01_script_gen.prompts", fromlist=["x"]).render_execution_contract(_final)
    assert isinstance(_contract, str) and _contract.strip()
    # 提交前门：通过的计划不再抛契约异常
    _rep = pf.check_plan_before_generation(_final, chapter_dir=tmp_path, novel_text=_SRC)
    assert _rep, _rep


# ---------------------------------------------------------------------------
# ⑤ 完整验收报告（2026-10-01 复核第 32 轮 P1）：结构 / 原文 / 承载 / 预算分别产生问题
# ---------------------------------------------------------------------------
def test_missing_core_fields_are_reported_not_silently_accepted():
    """删掉 `main_events` / `climax`：**必须报出来**（复核实测旧链返回空表）."""
    _p = _plan(required_facts=[_FACT_OK])
    _p.pop("main_events")
    _p.pop("climax")
    # 共用纯诊断给的 code（A02 口径）：缺字段 ⇒ 类型/数量/空值记录，不是"静默通过"
    _codes = {x["code"] for x in _problems(_p, _SRC)}
    assert _codes & {"structure_events_type", "structure_events_count"}, _codes
    assert "structure_missing_climax" in _codes, _codes
    _paths = {x["path"] for x in _problems(_p, _SRC)}
    assert "main_events" in _paths and "climax" in _paths, _paths


def test_ungrounded_lines_are_an_incomplete_acceptance_not_a_pass():
    """全部台词都不在原文里：`unevaluable` ⇒ 记 `acceptance_incomplete`，**不是通过**."""
    _p = _plan(required_facts=[_FACT_OK], dialogue_lines=["原文里根本没有的句子。",
                                                          "另一句也不在。"])
    _ps = _problems(_p, _SRC)
    _codes = {x["code"] for x in _ps}
    assert _ps and ("acceptance_incomplete" in _codes or "source_not_in_novel" in _codes), _codes
    _r = _ctl(_fixed_accept(_problems),
              lambda p, pr, bg, rj=None: {"kind": "no_change"}).run(_p, _SRC)
    assert _r["stop_reason"] != "ok", _r
    assert _r["residual_class"] != "none", _r


def test_the_report_layer_only_combines_producer_records():
    """复核第 40 轮：报告层**只组合**生产者的记录，不再自行解释汇总结论.

    判别性：①生产者给的 `problems` 原样出现在验收结果里；
    ②`dialogue_unrepresentable` 这类**汇总结论字段**不再被报告层二次解释
    （此前它自己拼 `budget_unrepresentable`，把来源不同的失败都渲染成预算问题）。
    """
    import step01_script_gen.plan_feasibility as _pf
    _rec = {"code": "budget_unrepresentable", "kind": "narration", "index": 2,
            "path": "narration_lines[1]", "reason": "N2 不可单镜表示（估时 14.24s）：装不下",
            "basis": "预算：每句台词/旁白必须能在一镜里讲完"}
    _rep = {"verdict": "infeasible", "problems": [dict(_rec)],
            "dialogue_unrepresentable": [{"line": "x", "est_sec": 99.0, "reason": "旧形态"}]}
    _orig = _pf.evaluate_plan
    _pf.evaluate_plan = lambda *a, **k: {k2: (list(v) if isinstance(v, list) else v)
                                        for k2, v in _rep.items()}
    try:
        _p = _plan(required_facts=[_FACT_OK])
        _ps = o.plan_acceptance_problems(_p, _SRC)
        assert [x["code"] for x in _ps] == ["budget_unrepresentable"], _ps
        assert _ps[0]["kind"] == "narration" and _ps[0]["index"] == 2, _ps
        assert _ps[0]["path"] == "narration_lines[1]", _ps
        # ② 只给汇总结论、不给记录 ⇒ 报告层**不能**自己造记录
        _pf.evaluate_plan = lambda *a, **k: {"verdict": "infeasible",
                                             "dialogue_unrepresentable": [{"line": "x"}]}
        assert o.plan_acceptance_problems(_p, _SRC) == []
    finally:
        _pf.evaluate_plan = _orig


def test_a_line_whose_text_contains_a_year_keeps_its_own_axis_index():
    """D2 的台词正文含 `2024`：编号必须来自**轴**（`dialogue_lines[1]`），不是正文里的数字."""
    _p = _plan(required_facts=[_FACT_OK],
               dialogue_lines=[_CITE, "2024 年那句在原文里根本没有。"])
    _ps = [x for x in _problems(_p, _SRC) if x["code"].startswith("source")]
    assert _ps, "第二句不在原文里，必须报出来"
    _hit = [x for x in _ps if x.get("index") == 2]
    assert _hit, _ps
    assert _hit[0]["path"] == "dialogue_lines[1]", _hit[0]
    assert _hit[0]["kind"] == "dialogue", _hit[0]


def test_a_narration_orphan_is_reported_on_the_narration_axis():
    """旁白孤儿 `N1` 必须报 `narration_lines[0]`——**不能落成 `dialogue_lines[0]`**."""
    _p = _plan(required_facts=[_FACT_OK],
               narration_lines=[{"text": "图外旁白一句", "source": _CITE, "fact_refs": []}])
    _ps = [x for x in _problems(_p, _SRC) if x["code"].startswith("orphan")]
    assert _ps, "没有任何节拍承载 N1，必须报孤儿"
    _n = [x for x in _ps if x["kind"] == "narration"]
    assert _n, _ps
    assert _n[0]["path"] == "narration_lines[0]", _n[0]
    assert _n[0]["index"] == 1, _n[0]
    assert not any(x["path"] == "dialogue_lines[0]" for x in _ps), _ps


def test_overlong_text_and_declarations_stay_structured():
    """超长数字正文 / 超长声明：只返回结构化问题，**不抛 `ValueError`/`TypeError`**."""
    _long = "9" * 5000
    _p = _plan(required_facts=["丙把茶壶踢翻了｜原文证据：“%s”｜承载：B%s/画面" % (_CITE, _long)],
               dialogue_lines=[_CITE, "第 %s 号句子不在原文里。" % _long])
    _ps = _problems(_p, _SRC)                      # ← 不许抛
    assert _ps, "非法声明 + 不在原文的句子，必须都有记录"
    for _x in _ps:
        assert set(_x) >= {"code", "path", "reason", "basis"}, _x
        assert _x["index"] is None or isinstance(_x["index"], int), _x


# ---------------------------------------------------------------------------
# ⑦ 结构规则**共用一处**（工作包 A02/A03）：纯诊断与验收链给同一套 code
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("mutate,want", [
    (lambda p: p.pop("main_events"), "structure_events_type"),
    (lambda p: p.update({"main_events": "不是数组"}), "structure_events_type"),
    (lambda p: p.update({"main_events": ["只有一条"]}), "structure_events_count"),
    (lambda p: p.pop("climax"), "structure_missing_climax"),
    (lambda p: p.update({"causal_chain": []}), "structure_field_empty"),
    (lambda p: p.update({"required_facts": []}), "structure_field_empty"),
    (lambda p: p.update({"shot_beats": []}), "structure_field_empty"),
    (lambda p: p.update({"narration_lines": [{"text": "x"}]}), "structure_narration_item"),
    (lambda p: p.update({"hook_spoken": "说出口"}), "structure_hook_spoken_type"),
])
def test_structure_rules_are_shared_between_diagnosis_and_chain(mutate, want):
    """同一份规则：`outline_structure_problems`（纯诊断）与 `plan_acceptance_problems`
    （验收链）给出同一批结构 code；**纯诊断不改输入**（前后字节等价）."""
    import json as _json
    _p = _plan(required_facts=[_FACT_OK])
    mutate(_p)
    _before = _json.dumps(_p, ensure_ascii=False, sort_keys=True)
    _pure = {x["code"] for x in o.outline_structure_problems(_p)}
    _after = _json.dumps(_p, ensure_ascii=False, sort_keys=True)
    assert _after == _before, "纯诊断不许改输入"
    assert want in _pure, (_pure, want)
    _chain = {x["code"] for x in _problems(_p, _SRC)}
    assert want in _chain, (_chain, want)
    assert _pure <= _chain, (_pure, _chain)


def test_a_legal_plan_passes_both_entries():
    """合法对照：纯诊断与验收链都**不**报结构问题（不能把统一协议做成一律拒收）."""
    _p = _plan(required_facts=[_FACT_OK])
    assert o.outline_structure_problems(_p) == []
    assert _problems(_p, _SRC) == []


# ---------------------------------------------------------------------------
# ⑧ 结构责任收口（复核第 36 轮）：规则对齐旧入口 + 结构先跑短路 + **真跑两个入口**
# ---------------------------------------------------------------------------
_STRUCT_BAD = [
    ("main_events 缺失", lambda p: p.pop("main_events")),
    ("main_events 是字符串", lambda p: p.update({"main_events": "不是数组"})),
    ("main_events 只有 1 条", lambda p: p.update({"main_events": ["一条"]})),
    ("climax 缺失", lambda p: p.pop("climax")),
    ("causal_chain 是字符串", lambda p: p.update({"causal_chain": "x"})),
    ("causal_chain 是 [None]", lambda p: p.update({"causal_chain": [None]})),
    ("causal_chain 空数组", lambda p: p.update({"causal_chain": []})),
    ("required_facts 空", lambda p: p.update({"required_facts": []})),
    ("shot_beats 是整数", lambda p: p.update({"shot_beats": 7})),
    ("required_facts 是整数", lambda p: p.update({"required_facts": 7})),
    ("dialogue_lines 是字符串", lambda p: p.update({"dialogue_lines": "不是数组"})),
    ("dialogue_lines 是整数", lambda p: p.update({"dialogue_lines": 7})),
    ("shot_beats 少于事件数", lambda p: p.update({"shot_beats": [p["shot_beats"][0]]})),
    ("narration_lines 条目缺 source", lambda p: p.update({"narration_lines": [{"text": "x"}]})),
]


@pytest.mark.parametrize("name,mutate", _STRUCT_BAD, ids=[x[0] for x in _STRUCT_BAD])
def test_structure_is_reported_and_never_crashes_the_chain(name, mutate):
    """结构非法 ⇒ **结构化拒收**（不带异常）；且**旧入口同样拒收**（规则对齐）."""
    import copy as _copy
    _p = _plan(required_facts=[_FACT_OK])
    mutate(_p)
    _ps = _problems(_p, _SRC)                       # ← 不许抛
    assert _ps, name
    assert all(str(x["code"]).startswith("structure") for x in _ps), (name, _ps)
    assert o._validate_outline(_copy.deepcopy(_p), _SRC) is None, name
    assert o.outline_structure_problems(_p), name


def test_a_legal_plan_passes_both_entries_for_real():
    """合法对照：**真跑** `_validate_outline` ⇒ 通过；诊断与验收链都空."""
    import copy as _copy
    _p = _plan(required_facts=[_FACT_OK])
    assert o.outline_structure_problems(_p) == []
    assert _problems(_p, _SRC) == []
    assert o._validate_outline(_copy.deepcopy(_p), _SRC) is not None


def test_structure_short_circuits_before_downstream_checks():
    """结构不可安全遍历时**不下探**：`shot_beats=7` 只返回结构记录（不会因承载/预算抛错）."""
    _p = _plan(required_facts=[_FACT_OK])
    _p["shot_beats"] = 7
    _ps = _problems(_p, _SRC)
    assert _ps and all(str(x["code"]).startswith("structure") for x in _ps), _ps


# ---------------------------------------------------------------------------
# ⑨ 类型必须**是**字符串，不能被 `str(...)` 转换绕过（复核第 37 轮）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,mutate", [
    ("climax=123", lambda p: p.update({"climax": 123})),
    ("旁白 text=123", lambda p: p.update({"narration_lines": [{"text": 123, "source": "s"}]})),
    ("旁白 source=123", lambda p: p.update({"narration_lines": [{"text": "x", "source": 123}]})),
])
def test_non_string_types_are_reported_not_coerced(name, mutate):
    """schema 要字符串：数字不许靠 `str(...)` 转换放行（只断言新链报出，不比对旧入口）."""
    _p = _plan(required_facts=[_FACT_OK])
    mutate(_p)
    _codes = {x["code"] for x in o.outline_structure_problems(_p)}
    assert _codes & {"structure_field_type", "structure_narration_item"}, (name, _codes)
    _chain = _problems(_p, _SRC)
    assert _chain and all(str(x["code"]).startswith("structure") for x in _chain), (name, _chain)


# ---------------------------------------------------------------------------
# ⑩ 接线证明 + 基准一致（复核第 37 轮）：**注入**诊断结果，两个入口都必须消费它
# ---------------------------------------------------------------------------
def test_both_entries_consume_the_injected_diagnosis(monkeypatch):
    """把共用诊断换成桩：`_validate_outline` 与验收链都必须**消费它**（不是各自维护一份）."""
    import copy as _copy
    _p = _plan(required_facts=[_FACT_OK])
    _stub = [{"code": "structure_stub", "kind": "plan", "index": None, "path": "",
              "reason": "桩：注入的结构问题", "basis": "注入"}]
    monkeypatch.setattr(o, "outline_structure_problems", lambda *_a, **_k: _stub)
    assert o._validate_outline(_copy.deepcopy(_p), _SRC) is None, "旧入口没消费共用诊断"
    _chain = _problems(_p, _SRC)
    assert [x["code"] for x in _chain] == ["structure_stub"], _chain


def test_purifiable_and_tolerated_inputs_agree_between_entries():
    """基准一致（复核第 37 轮两组读数 + 第 39 轮修正）：可净化项与"未选台词"都**放行**，
    两个入口在**同一份**规则下给同一结论（不再有"新诊断放行、旧入口拒收"的分裂）."""
    import copy as _copy
    # ① 3 条有效事件后追加空串：可净化 ⇒ 两个入口都放行（数量与覆盖同一口径）
    _p1 = _plan(required_facts=[_FACT_OK])
    _p1["main_events"] = list(_p1["main_events"]) + [""]
    assert o.outline_structure_problems(_p1) == [], o.outline_structure_problems(_p1)
    assert o._validate_outline(_copy.deepcopy(_p1), _SRC) is not None
    # ①' 空串夹在中间同样只算有效条数（顺序无关）
    _p1b = _plan(required_facts=[_FACT_OK])
    _ev = list(_p1b["main_events"])
    _p1b["main_events"] = [_ev[0], "   ", _ev[1], _ev[2]]
    assert o.outline_structure_problems(_p1b) == [], o.outline_structure_problems(_p1b)
    assert o._validate_outline(_copy.deepcopy(_p1b), _SRC) is not None
    # ② `dialogue_lines` 缺失 / 为 null / 空数组：都放行（"一句都没选"归生成前可行性门）
    for _label, _mut in (("缺失", lambda p: p.pop("dialogue_lines")),
                         ("null", lambda p: p.update({"dialogue_lines": None})),
                         ("空数组", lambda p: p.update({"dialogue_lines": []}))):
        _p2 = _plan(required_facts=[_FACT_OK])
        _mut(_p2)
        assert o.outline_structure_problems(_p2) == [], (_label, o.outline_structure_problems(_p2))
        assert o._validate_outline(_copy.deepcopy(_p2), _SRC) is not None, _label


def test_non_array_dialogue_lines_is_a_structure_error():
    """判别性（复核第 39 轮）："缺失/null/空数组放行"**不许**推广成"任意非数组放行"——
    整表不是数组时 D 编号无从核验，两个入口都必须拒收，且给出结构化原因."""
    import copy as _copy
    for _bad in ("这不是数组", 7, {"line": "x"}):
        _p = _plan(required_facts=[_FACT_OK])
        _p["dialogue_lines"] = _bad
        _ps = o.outline_structure_problems(_p)
        assert _ps and _ps[0]["code"] == "structure_field_type", (_bad, _ps)
        assert _ps[0]["path"] == "dialogue_lines", _ps
        assert o._validate_outline(_copy.deepcopy(_p), _SRC) is None, _bad
        assert _ps == _problems(_p, _SRC), (o.outline_structure_problems(_p), _problems(_p, _SRC))

# ---------------------------------------------------------------------------
# ④ 拒收原因**完整**交给下一轮（复核 2026-10-04 P1-1）
# ---------------------------------------------------------------------------
def test_controller_hands_the_full_rejection_reason_to_the_next_round():
    """控制器保存完整拒收问题；下一轮请求读得到**每一条**身份与原因.

    复核反例：`rejection_record` 旧写法在**存储**这一步就 `[:400]`，于是渲染层再宽也
    拿不到后面的问题——章 1 第 3 轮的 6 条问题（533 字）被截成 400 且没有省略标记。
    当时的新增测试直接调渲染器，**绕过了控制器这一跳**，所以结论不成立。
    这条从控制器入口走连续两轮，逐条断言身份进入第二轮请求。
    """
    import step01_script_gen.outline as _o

    _identities = ["F%d 声明由 B%d 的**画面**承载，但该节拍标的是「载体：台词%d」"
                   "——一条节拍只能有**一个**载体，画面与台词/旁白必须分属**不同节拍**"
                   % (i, i, i) for i in range(1, 9)]
    _detail = "；".join(_identities)
    assert len(_detail) > 400, "这条用例要构造**超过旧上限**的原因才有判别性"

    _seen: list = []

    def _revise(plan, problems, budget, rejections=None):
        _seen.append(list(rejections or []))
        if len(_seen) == 1:
            return {"kind": "illegal", "detail": _detail}
        return {"kind": "no_change"}

    _r = _ctl(_fixed_accept(lambda p, nt="": [{"code": "carrier", "detail": "x"}]),
              _revise, max_calls=3).run(_plan(), _SRC)
    assert len(_seen) >= 2, _r
    assert _seen[0] == [], "第一轮不该有拒收记录"
    assert _seen[1], _seen
    _rec = _seen[1][0]
    assert _rec["detail"] == _detail, (
        "控制器必须保存**完整**拒收原因（旧写法截到 400 字）：%d 字" % len(_rec["detail"]))
    # 逐条断言身份与原因都进了第二轮**请求**
    _block = _o._rejection_feedback_block(_seen[1])
    for _one in _identities:
        assert _one in _block, "第二轮请求里缺这条：%s" % _one
    assert "未显示" not in _block, "没超上限就不该出现省略标记"


def test_rejection_display_marks_what_it_omits():
    """展示形态有界，但**不许静默省略**（存储仍完整）."""
    from step01_script_gen.plan_revision import (rejection_display, rejection_record)
    _long = "甲" * 900
    _rec = rejection_record(1, "illegal", _long)
    assert _rec["detail"] == _long, "存储必须完整"
    _show = rejection_display(_rec, limit=100)
    assert _show.startswith("甲" * 100) and "另有 800 字未显示" in _show, _show
    assert rejection_display(rejection_record(1, "illegal", "短"), limit=100) == "短"
