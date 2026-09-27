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
# 短引文（归一化 7 字，走「整段逐字」档）
_SHORT_CITE = "丁把茶壶踢翻了。"
_SRC = ('甲道：“%s”乙道：“%s”%s%s'
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
# ③ 验收路径：可表示性触发时允许重排核心字段，但要有原文证据
# ---------------------------------------------------------------------------

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
_BEATS_REPLANNED_BAD = [
    '节拍：编造的画面｜原文依据："此人手持长剑立于城头之上，风吹衣袂猎猎作响。"',
    '节拍：由丁的动作承载后续｜原文依据："%s"' % _SHORT_CITE,
    '节拍：收尾甲｜原文依据："%s"' % _LONG_CITE,
    '节拍：收尾乙｜原文依据："%s"' % _SHORT_CITE,
]


def _replannable_payload(beats):
    """一份能过 `_validate_outline` 的新计划（3 事件 + 4 节拍 + 台词逐字来自原文）."""
    return {
        "main_events": ["甲喝止", "乙发抖", "丙失手"],
        "climax": "丙失手",
        "required_facts": ['丙失手｜原文证据："%s"' % _LONG_CITE],
        "causal_chain": ["甲喝止→乙发抖→丙失手"],
        "shot_beats": list(beats),
        "dialogue_lines": [_Q_WITH_END],   # 逐字来自 _SRC
        "time_budget": "对白约50%",
    }


def _old_plan():
    return {
        "main_events": ["甲喝止", "乙发抖", "丙失手"],
        "climax": "丙失手",
        "required_facts": ['丙失手｜原文证据："%s"' % _LONG_CITE],
        "causal_chain": ["甲喝止→乙发抖→丙失手"],
        "shot_beats": ['节拍：甲喝止｜原文依据："%s"' % _LONG_CITE,
                       '节拍：乙发抖｜原文依据："%s"' % _SHORT_CITE,
                       '节拍：丙失手｜原文依据："%s"' % _LONG_CITE,
                       '节拍：收尾｜原文依据："%s"' % _SHORT_CITE],
        "dialogue_lines": [_Q_NO_END],
        "time_budget": "对白约50%",
    }


_REP_UNREP = {"dialogue_unrepresentable": [
    {"line": _Q_NO_END, "est_sec": 19.8, "reason": "句内无句末边界"}]}


def _run(monkeypatch, payload, rep, plan=None):
    import step01_script_gen.outline as o
    svc = _Svc(payload)
    monkeypatch.setattr(o, "create_service", lambda *a, **k: svc)
    return o.replan_dialogue_within_budget(plan or _old_plan(), rep, _SRC)


def test_feedback_tail_does_not_contradict_the_replan_permission():
    """反馈**末尾**那句「请原样返回」必须跟着放开——它是模型最后读到的指令.

    实损：首版只改了上面的条款，尾部仍写「除台词/可省略/时间倾向外，请原样返回」，
    模型据此把计划原样退回（`_plan_core_preserved` 直接通过，例外分支根本没走到）。
    两句相反的话放在同一个提示里，后一句赢。
    """
    import step01_script_gen.outline as o

    plan = {"dialogue_lines": [_Q_NO_END], "shot_beats": ["b1", "b2"]}
    with_unrep = o._budget_feedback_block(_REP_UNREP, plan, _SRC)
    assert "除台词/可省略/时间倾向外，请原样返回" not in with_unrep,         "尾部仍在要求原样返回，与「允许重排节拍」自相矛盾"
    assert "重排的 `shot_beats` 正文与载体" in with_unrep
    # 没有可表示性问题时，尾部保持原口径（不给预算路径开后门）
    plain = o._budget_feedback_block(
        {"dialogue_unrepresentable": [], "budget": {"min": 60, "max": 90},
         "dialogue_lines": 1, "dialogue_sec": 200.0, "lower_bound_sec": 200.0,
         "beats": 3, "beats_floor_sec": 9.0}, plan, _SRC)
    assert "除台词/可省略/时间倾向外，请原样返回" in plain


# ---------------------------------------------------------------------------
# ②b _plan_invariants_intact：删事实 / 调换事件顺序一律拒收（复核实测的验收漏洞）
# ---------------------------------------------------------------------------

def test_invariants_reject_deleted_fact():
    from step01_script_gen.outline import _plan_invariants_intact
    old = {"main_events": ["a", "b"], "required_facts": ["f1", "f2"], "climax": "a"}
    new = {"main_events": ["a", "b"], "required_facts": ["f1"], "climax": "a"}
    assert "必保留事实被删除" in _plan_invariants_intact(old, new)


def test_invariants_reject_reordered_events():
    from step01_script_gen.outline import _plan_invariants_intact
    old = {"main_events": ["a", "b", "c"], "required_facts": ["f1"], "climax": "a"}
    new = {"main_events": ["c", "b", "a"], "required_facts": ["f1"], "climax": "a"}
    assert "main_events" in _plan_invariants_intact(old, new)


def test_invariants_reject_changed_climax():
    from step01_script_gen.outline import _plan_invariants_intact
    old = {"main_events": ["a"], "required_facts": ["f1"], "climax": "a"}
    new = {"main_events": ["a"], "required_facts": ["f1"], "climax": "b"}
    assert "climax" in _plan_invariants_intact(old, new)


def test_invariants_allow_added_fact():
    from step01_script_gen.outline import _plan_invariants_intact
    old = {"main_events": ["a"], "required_facts": ["f1"], "climax": "a"}
    new = {"main_events": ["a"], "required_facts": ["f1", "f2"], "climax": "a"}
    assert _plan_invariants_intact(old, new) == ""


def test_replan_rejects_fact_deletion_even_with_valid_citations(monkeypatch):
    """复核实测形态：删掉一条必保留事实、其余条目引文都合法 → 仍须拒收.

    这是「只核对新稿里还在的条目」的盲区——`_plan_core_preserved` 正是因为这些字段
    变了才把控制权交过来，所以不变量必须在证据闸**之前**逐项比对。
    """
    payload = _replannable_payload(_BEATS_REPLANNED_OK)
    payload["required_facts"] = ['换了一条事实｜原文证据："%s"' % _SHORT_CITE]
    plan, why = _run(monkeypatch, payload, _REP_UNREP)
    assert plan is None and "必保留事实被删除" in why, why


def test_replan_rejects_event_reorder_even_with_valid_citations(monkeypatch):
    payload = _replannable_payload(_BEATS_REPLANNED_OK)
    payload["main_events"] = list(reversed(payload["main_events"]))
    plan, why = _run(monkeypatch, payload, _REP_UNREP)
    assert plan is None and "main_events" in why, why


def test_replan_accepts_replanned_beats_with_evidence(monkeypatch):
    """可表示性触发 + 节拍正文被重排（核心字段改动）+ 引文可核 → 接受为新计划版本."""
    plan, why = _run(monkeypatch, _replannable_payload(_BEATS_REPLANNED_OK), _REP_UNREP)
    assert plan is not None, why
    assert plan["shot_beats"][0].startswith("节拍：丙失手（画面动作）")


def test_replan_rejects_replanned_beats_without_evidence(monkeypatch):
    """节拍被重排但引文核不过 → 拒收（例外不是放宽，是换更严的闸）."""
    plan, why = _run(monkeypatch, _replannable_payload(_BEATS_REPLANNED_BAD), _REP_UNREP)
    assert plan is None and "core_field_changed" in why, why


def test_replan_still_rejects_core_change_when_not_representability(monkeypatch):
    """**预算**触发的重选不享受例外：改了核心字段照旧拒收（只许改台词）."""
    plan, why = _run(monkeypatch, _replannable_payload(_BEATS_REPLANNED_OK),
                     {"dialogue_unrepresentable": []})
    assert plan is None and "core_field_changed" in why, why


def test_replan_unchanged_core_still_passes_without_evidence_rule(monkeypatch):
    """核心字段没动 → 走原有路径，不因新增的证据闸多一道要求."""
    plan, why = _run(monkeypatch, _replannable_payload(_old_plan()["shot_beats"]), _REP_UNREP)
    assert plan is not None, why
