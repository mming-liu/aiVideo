# -*- coding: utf-8 -*-
"""单镜可表示性触发的规划重选：已验算替代集合 + 原文证据验收（2026-09-26 用户决定）.

## 为什么需要这条路径

章 2 的计划被判 `infeasible`（`after_unrepresentable=2`），正式流程在候选扇出前就停。
实测**自由重选不解决问题**：反馈块已经点名了不可表示的句子与原因，模型重抽后仍把
原句留下（`after_lines` 5→5、`rejected=0`）。

用户决定的修法：
- 反馈里给**已验算过**的可表示替代（只收**句末收尾**的片段——逗号截断的单镜装得下，
  但成片里是没说完的半句，明确不采用）；出处通篇没有句末标点时**要求按原文顺序重排载体**
  （画面动作 + 由别人在原文里的完整短句承载同一事实），**禁止把某人的台词改派给另一个角色**；
- 重排过的版本**必然改动核心字段**，`_plan_core_preserved` 会拒收——**不放宽它**，而是
  换一道更严的闸 `_plan_evidence_intact`：每一条节拍/必保留事实都必须带逐字可核的原文引文；
- 预算超限触发的重选**不享受**这个例外，仍只许改台词。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from step01_script_gen.outline import _plan_evidence_intact  # noqa: E402
from step01_script_gen.plan_feasibility import (  # noqa: E402
    representable_replacements,
)

# 出处通篇只有逗号（无句末标点）→ 不可表示且**没有**句末收尾替代
_Q_NO_END = ("杨旭让人宰了，消息一旦传到佥事大人耳中，我们会是什么下场可想而知，"
             "几位，罗大人的手段你们是晓得的，若不想落得个求生不得求死不能的下场，"
             "那就自我了断，寻个痛快吧。")
# 出处句内有叹号（句末标点）→ 整句不可表示，但有句末收尾替代
_Q_WITH_END = ("吃的灯草灰，放的轻巧屁！罗大人几时这般心慈面软过了，应天那边现在的情形"
               "你又不是不知道，我锦衣卫现在处境何等艰难，想要翻身，依赖的就是咱们了。")
# 长引文（归一化 17 字，走「≥8 字连续命中」档）
_LONG_CITE = "丙吓得发抖，连手里的茶盏都拿不稳了。"
# 短引文（归一化 7 字，走「整段逐字」档）；**单独作为一条引语**，给跨出处菜单用
_SHORT_CITE = "丁把茶壶踢翻了。"
_SRC = ('甲道：“%s”乙道：“%s”%s丙道：“%s”'
        % (_Q_NO_END, _Q_WITH_END, _LONG_CITE, _SHORT_CITE))


# ---------------------------------------------------------------------------
# ① representable_replacements：只给句末收尾、已验算可表示的替代
# ---------------------------------------------------------------------------

def test_replacements_only_sentence_end_terminated():
    got = representable_replacements(_Q_WITH_END, _SRC)
    assert got, "句内有句末标点的出处必须能给出替代"
    from step01_script_gen.plan_feasibility import _SENT_END_CHARS
    for c in got:
        assert c[-1] in _SENT_END_CHARS, "替代必须以句末标点收尾：%s" % c
        assert c in _Q_WITH_END, "替代必须逐字来自原文出处：%s" % c
    assert got[0] == "吃的灯草灰，放的轻巧屁！", "最长者优先（尽量少丢信息）"
    assert len(got) == len(set(got))


def test_replacements_empty_when_source_has_no_sentence_end():
    """出处通篇没有句末标点 → 空列表（反馈块据此要求重排载体，而不是逗号硬拆）."""
    assert representable_replacements(_Q_NO_END, _SRC) == []


def test_replacements_never_return_comma_terminated_fragments():
    got = representable_replacements(_Q_WITH_END, _SRC)
    assert not [c for c in got if c.endswith("，")], got


def test_replacements_empty_for_line_not_in_source():
    assert representable_replacements("这句原文里根本没有的话。", _SRC) == []


# ---------------------------------------------------------------------------
# ①b cross_quote_replacements：无出处候选时给**相邻引语**里的合法短句
# ---------------------------------------------------------------------------

def test_cross_quote_offers_a_neighbouring_short_line():
    """实测形态：出处通篇无句末标点时，把同场景相邻发言里的完整短句摆出来.

    章 2 冯检校那句的合法载体本来就在旁边（安员外「杨旭死了，咱们的差事算是办砸了，
    现在该怎么办？」）——只说「没有候选」时模型会去截断本句，把菜单给它就不必猜。
    """
    from step01_script_gen.plan_feasibility import _SENT_END_CHARS, cross_quote_replacements
    from step01_script_gen.plan_feasibility import _source_norm
    got = cross_quote_replacements(_Q_NO_END, _SRC)
    assert got, "相邻引语里有合法短句时必须列出来"
    texts = [c["text"] for c in got]
    assert _SHORT_CITE in texts, "同场景的完整短句应当在候选里：%s" % texts
    for c in got:
        assert c["text"][-1] in _SENT_END_CHARS, "候选必须句末收尾：%s" % c["text"]
        assert c["text"] in _SRC and _source_norm(c["text"]) != _source_norm(_Q_NO_END)
        assert c["from"].startswith("相邻第")


def test_cross_quote_candidates_fit_one_shot():
    """跨出处候选只收**单镜装得下**的——50-110 字那种换过去只是把长度问题搬个家."""
    from step01_script_gen.plan_feasibility import (check_dialogue_representable,
                                                    cross_quote_replacements)
    for c in cross_quote_replacements(_Q_NO_END, _SRC):
        assert check_dialogue_representable(c["text"]).get("split") is None, c["text"]


def test_cross_quote_empty_for_line_not_in_source():
    from step01_script_gen.plan_feasibility import cross_quote_replacements
    assert cross_quote_replacements("原文里没有这句。", _SRC) == []


# ---------------------------------------------------------------------------
# ② _plan_evidence_intact：重排版本必须有逐字可核的原文证据
# ---------------------------------------------------------------------------

def _plan(beats, facts=None):
    return {"shot_beats": list(beats),
            "required_facts": list(facts or ['丙失手｜原文证据："%s"' % _LONG_CITE]),
            "main_events": ["一", "二", "三"]}


def test_evidence_gate_accepts_a_source_anchored_plan():
    ok = _plan(['节拍：丙失手｜原文依据："%s"' % _LONG_CITE])
    assert _plan_evidence_intact(ok, _SRC) == ""


def test_evidence_gate_accepts_a_short_citation_that_matches_wholly():
    ok = _plan(['节拍：丁踢翻茶壶｜原文依据："%s"' % _SHORT_CITE])
    assert _plan_evidence_intact(ok, _SRC) == ""


def test_evidence_gate_rejects_a_short_citation_that_misses():
    """短引文（4-7 字）必须**整段**逐字——短引文再容忍少抄字就等于不设闸."""
    bad = _plan(['节拍：丁踢翻茶壶｜原文依据："丁把茶壶碰翻了。"'])
    assert "节拍 1" in _plan_evidence_intact(bad, _SRC)


def test_evidence_gate_rejects_too_short_citation_as_evidence():
    """归一化后不足 4 字（如「住嘴」）不算证据——避免拿两字词当原文依据."""
    bad = _plan(['节拍：甲喝止｜原文依据："住嘴！"'])
    assert "节拍 1" in _plan_evidence_intact(bad, _SRC)


def test_evidence_gate_rejects_fabricated_beat():
    bad = _plan(['节拍：编造｜原文依据："此人手持长剑立于城头之上，风吹衣袂猎猎作响。"'])
    assert "节拍 1" in _plan_evidence_intact(bad, _SRC)


def test_evidence_gate_rejects_beat_without_citation():
    assert "缺「原文依据」" in _plan_evidence_intact(_plan(["节拍：没有出处"]), _SRC)


def test_evidence_gate_rejects_fact_without_citation():
    bad = _plan(['节拍：丙失手｜原文依据："%s"' % _LONG_CITE], facts=["丙失手｜没有引文"])
    assert "必保留事实 1" in _plan_evidence_intact(bad, _SRC)


def test_evidence_gate_rejects_fact_with_fabricated_evidence():
    bad = _plan(['节拍：丙失手｜原文依据："%s"' % _LONG_CITE],
                facts=['丙失手｜原文证据："他纵身跃上屋脊，衣袂在夜风里猎猎作响。"'])
    assert "必保留事实 1" in _plan_evidence_intact(bad, _SRC)


def test_evidence_gate_tolerates_a_one_char_slip_in_long_citation():
    """长引文少抄一两个字不得判死——实测章 2「这人虽是…」原文是「**这**个人虽是…」."""
    src = "这个人虽是一身寒酸，可是五官相貌却与杨旭一般无二。"
    plan = _plan(['节拍：乞丐像杨旭｜原文依据："这人虽是一身寒酸，可是五官相貌却与杨旭一般无二"'],
                 facts=['乞丐像杨旭｜原文证据："这个人虽是一身寒酸，可是五官相貌却与杨旭一般无二。"'])
    assert _plan_evidence_intact(plan, src) == ""


def test_evidence_gate_requires_source():
    assert "无原文可核" in _plan_evidence_intact(_plan(['节拍：丙｜原文依据："%s"' % _LONG_CITE]), "")


# ---------------------------------------------------------------------------
# ③ apply_replan_ops：模型回**操作**，代码在旧计划上应用（v6.143 用户决定）
# ---------------------------------------------------------------------------

def _ops(dlg=(), beat=()):
    return {"dialogue_ops": list(dlg), "beat_ops": list(beat)}


def test_apply_ops_replaces_dialogue_with_a_verified_candidate():
    from step01_script_gen.outline import apply_replan_ops
    cand = representable_replacements(_Q_WITH_END, _SRC)[0]
    new, why = apply_replan_ops(_old_plan(), _ops([{"index": 1, "replace_with": cand}]), _SRC)
    assert new is not None, why
    assert new["dialogue_lines"][0] == cand


def test_apply_ops_rejects_replacement_not_in_source():
    from step01_script_gen.outline import apply_replan_ops
    new, why = apply_replan_ops(
        _old_plan(), _ops([{"index": 1, "replace_with": "这句原文里根本没有。"}]), _SRC)
    assert new is None and "未在原文逐字找到" in why, why


def test_apply_ops_rejects_unrepresentable_replacement():
    from step01_script_gen.outline import apply_replan_ops
    new, why = apply_replan_ops(
        _old_plan(), _ops([{"index": 1, "replace_with": _Q_NO_END}]), _SRC)
    assert new is None and "不可单镜表示" in why, why


def test_apply_ops_rejects_out_of_range_index():
    from step01_script_gen.outline import apply_replan_ops
    _n, why = apply_replan_ops(_old_plan(), _ops([{"index": 9, "replace_with": "住嘴！"}]), _SRC)
    assert _n is None and "越界" in why, why
    _n2, why2 = apply_replan_ops(
        _old_plan(), _ops(beat=[{"index": 9, "new_text": "x｜原文依据：\"%s\"" % _LONG_CITE,
                                 "new_carrier": "画面"}]), _SRC)
    assert _n2 is None and "越界" in why2, why2


def test_apply_ops_rejects_beat_without_evidence():
    from step01_script_gen.outline import apply_replan_ops
    _n, why = apply_replan_ops(
        _old_plan(), _ops(beat=[{"index": 1, "new_text": "节拍：编造的画面",
                                 "new_carrier": "画面"}]), _SRC)
    assert _n is None and "原文依据" in why, why


def test_apply_ops_rejects_beat_with_fabricated_evidence():
    from step01_script_gen.outline import apply_replan_ops
    _n, why = apply_replan_ops(
        _old_plan(),
        _ops(beat=[{"index": 1,
                    "new_text": '节拍：编造｜原文依据："此人手持长剑立于城头之上，风吹衣袂猎猎作响。"',
                    "new_carrier": "台词1"}]), _SRC)
    assert _n is None and "节拍 1" in why, why


def test_apply_ops_rewrites_beat_and_keeps_invariants():
    from step01_script_gen.outline import apply_replan_ops
    new, why = apply_replan_ops(
        _old_plan(),
        _ops(beat=[{"index": 1,
                    "new_text": '节拍：丙失手（画面动作）｜原文依据："%s"' % _LONG_CITE,
                    "new_carrier": "台词1"}]), _SRC)
    assert new is not None, why
    assert new["shot_beats"][0].endswith("｜载体：台词1")
    # 操作**结构上**改不到事实与事件（只在旧计划上打补丁）
    assert new["main_events"] == _old_plan()["main_events"]
    assert new["required_facts"] == _old_plan()["required_facts"]


def test_apply_ops_does_not_duplicate_carrier_tag():
    """模型常在 new_text 里自带一份「｜载体：…」——不得拼成两遍（实跑出现过）."""
    from step01_script_gen.outline import apply_replan_ops
    new, why = apply_replan_ops(
        _old_plan(),
        _ops(beat=[{"index": 1,
                    "new_text": '节拍：丙失手｜原文依据："%s"｜载体：台词9' % _LONG_CITE,
                    "new_carrier": "台词1"}]), _SRC)
    assert new is not None, why
    assert new["shot_beats"][0].count("｜载体：") == 1, new["shot_beats"][0]
    assert new["shot_beats"][0].endswith("｜载体：台词1"), new["shot_beats"][0]


def test_apply_ops_rejects_ops_that_orphan_a_dialogue_line():
    """判别性反例（09-27 复核指定）：把承载某句的节拍改成「画面」→ 该句无人承载 → 拒收.

    实损形态：重选把 D1 换掉、又把原指向 D1 的节拍改成画面，D1 从此无人承载；
    旧验收只验字数/引文，误报通过，问题一路拖到候选层台词门才炸。
    """
    from step01_script_gen.outline import apply_replan_ops
    new, why = apply_replan_ops(
        _old_plan(),
        _ops(beat=[{"index": 1,
                    "new_text": '节拍：甲喝止（画面动作）｜原文依据："%s"' % _LONG_CITE,
                    "new_carrier": "画面"}]), _SRC)
    assert new is None and "无任何节拍承载" in why, why


def test_apply_ops_rejects_comma_truncated_replacement():
    """1a 判别性：**短**的半句本身"可表示"，只有半句判据能拦（提示早就禁了，但没人拦）.

    实跑形态：模型两次把长句换成自己出处的逗号截断前缀——短到单镜装得下，
    于是 `representable` 放行、`source_contains` 也放行，一路通过。
    """
    from step01_script_gen.plan_feasibility import check_dialogue_representable as cdr
    from step01_script_gen.outline import apply_replan_ops
    half = "杨旭让人宰了，"                      # _Q_NO_END 的逗号截断前缀
    assert half in _SRC and cdr(half)["representable"], "夹具必须是**可表示**的半句"
    new, why = apply_replan_ops(_old_plan(), _ops([{"index": 1, "replace_with": half}]), _SRC)
    assert new is None and "半句" in why, why


def test_apply_ops_distinguishes_structure_error_from_no_change():
    """1b：结构错误不得落成 `no_change`（那是「模型认为无需改动」，两回事）."""
    from step01_script_gen.outline import apply_replan_ops
    for bad in ("not-a-dict", {"dialogue_ops": "x"}, {"beat_ops": [1, 2]}):
        new, why = apply_replan_ops(_old_plan(), bad, _SRC)
        assert new is None and why.startswith("invalid_result"), (bad, why)


def test_ops_mode_parse_failure_is_not_no_change(monkeypatch):
    """1b：模型回的不是 JSON → `invalid_result`，不能记成 no_change."""
    import step01_script_gen.outline as o

    class _Svc:
        def generate(self, req):
            class _R:
                success = True
                error = ""
                text = "这不是 JSON"
            return _R()

    monkeypatch.setattr(o, "create_service", lambda *a, **k: _Svc())
    plan, why = o.replan_dialogue_within_budget(_old_plan(), _REP_UNREP, _SRC)
    assert plan is None and why.startswith("invalid_result"), why


def test_apply_ops_empty_is_no_change():
    """模型显式回空操作 → `no_change`（**不算**一次有效重选，不再有「回抄旧稿」的退路）."""
    from step01_script_gen.outline import apply_replan_ops
    new, why = apply_replan_ops(_old_plan(), _ops(), _SRC)
    assert new is None and why == "no_change"


# ---- 端到端：重选函数在可表示性触发时走 ops 通道 ----

class _Svc:
    def __init__(self, payload):
        self.payload = payload
        self.reqs = []

    def generate(self, req):
        self.reqs.append(req)

        class _R:
            success = True
            error = ""
            text = json.dumps(self.payload, ensure_ascii=False)

        return _R()


_BEATS_REPLANNED_OK = [
    '节拍：丙失手（画面动作）｜原文依据："%s"' % _LONG_CITE,
    '节拍：由丁的动作承载后续｜原文依据："%s"' % _SHORT_CITE,
    '节拍：收尾甲｜原文依据："%s"' % _LONG_CITE,
    '节拍：收尾乙｜原文依据："%s"' % _SHORT_CITE,
]


def _replannable_payload(beats):
    """一份能过 `_validate_outline` 的完整计划（**预算**通道仍走整份重写）."""
    return {
        "main_events": ["甲喝止", "乙发抖", "丙失手"],
        "climax": "丙失手",
        "required_facts": ['丙失手｜原文证据："%s"' % _LONG_CITE],
        "causal_chain": ["甲喝止→乙发抖→丙失手"],
        "shot_beats": list(beats),
        "dialogue_lines": [_Q_WITH_END],
        "time_budget": "对白约50%",
    }


def _old_plan():
    return {
        "main_events": ["甲喝止", "乙发抖", "丙失手"],
        "climax": "丙失手",
        "required_facts": ['丙失手｜原文证据："%s"' % _LONG_CITE],
        "causal_chain": ["甲喝止→乙发抖→丙失手"],
        "shot_beats": ['节拍：甲喝止｜原文依据："%s"｜载体：台词1' % _LONG_CITE,
                       '节拍：乙发抖｜原文依据："%s"｜载体：画面' % _SHORT_CITE,
                       '节拍：丙失手｜原文依据："%s"｜载体：画面' % _LONG_CITE,
                       '节拍：收尾｜原文依据："%s"｜载体：画面' % _SHORT_CITE],
        "dialogue_lines": [_Q_NO_END],
        "time_budget": "对白约50%",
    }


_REP_UNREP = {"dialogue_unrepresentable": [
    {"line": _Q_NO_END, "est_sec": 19.8, "reason": "句内无句末边界"}]}


def _run(monkeypatch, payload, rep, plan=None):
    import step01_script_gen.outline as o
    svc = _Svc(payload)
    monkeypatch.setattr(o, "create_service", lambda *a, **k: svc)
    return o.replan_dialogue_within_budget(plan or _old_plan(), rep, _SRC), svc


def test_ops_mode_uses_the_ops_schema_and_applies_operations(monkeypatch):
    """可表示性触发 → 请求用 `REPLAN_OPS_SCHEMA`，操作由代码应用."""
    from _text_gen.deepseek_schema import REPLAN_OPS_SCHEMA
    cand = representable_replacements(_Q_WITH_END, _SRC)[0]
    payload = _ops(
        [{"index": 1, "replace_with": cand}],
        [{"index": 1, "new_text": '节拍：丙失手（画面动作）｜原文依据："%s"' % _LONG_CITE,
          "new_carrier": "台词1"}])
    (plan, why), svc = _run(monkeypatch, payload, _REP_UNREP)
    assert plan is not None, why
    assert svc.reqs[0].json_schema is REPLAN_OPS_SCHEMA, "可表示性触发必须走 ops schema"
    assert plan["dialogue_lines"][0] == cand
    assert plan["shot_beats"][0].endswith("｜载体：台词1")
    assert plan["main_events"] == _old_plan()["main_events"], "事实/事件不得被动到"


def test_ops_mode_reports_no_change(monkeypatch):
    (plan, why), _svc = _run(monkeypatch, _ops(), _REP_UNREP)
    assert plan is None and why == "no_change", why


def test_budget_trigger_still_uses_the_full_plan_schema(monkeypatch):
    """预算通道不变：仍用 `OUTLINE_SCHEMA` 整份重写 + 核心字段校验."""
    from _text_gen.deepseek_schema import OUTLINE_SCHEMA
    (_plan_obj, why), svc = _run(
        monkeypatch, _replannable_payload(_old_plan()["shot_beats"]),
        {"dialogue_unrepresentable": []})
    assert svc.reqs[0].json_schema is OUTLINE_SCHEMA
    assert _plan_obj is not None, why
