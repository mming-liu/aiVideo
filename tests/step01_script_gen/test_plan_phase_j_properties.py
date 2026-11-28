# -*- coding: utf-8 -*-
"""**关系性质 + 归因证据**验收（复核 2026-10-02 之九 第 4、5 项）.

两项要求：

1. **关系性质**：换掉小说措辞、换一份合法编号、调换条目顺序之后，语义相同的裁决必须**一致**；
   关键承载关系被改动时，裁决必须**能区分**——用例不能只适配"这两章"。
2. **`no_change` 归因**：`no_change` 只说明"候选没有改变"，**不能**单独推断模型理解与否、
   有没有合法修法、故事能不能满足。归因必须附：修法可表达性、请求信息、原始响应、
   应用结果、残余问题——本模块逐项断言它们确实被留档，且两种"没变"能被区分。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from shared import call_ledger as cl                                          # noqa: E402
from step01_script_gen import outline as o                                    # noqa: E402
from test_plan_phase_g_rejection_feedback import _LINE, _LINE2, _Svc          # noqa: E402

_SRC = "开头。" + _LINE + _LINE2 + "事件丙。"

#: **同构改写**：只换措辞（人名/物件/句子），关系一模一样——裁决必须一模一样
_RENAME = (("甲板", "船头"), ("帆布", "布帆"), ("屋子", "屋内"), ("打量", "端详"))


def _reword(text: str) -> str:
    for a, b in _RENAME:
        text = text.replace(a, b)
    return text


def _beat(text, carrier, evidence):
    return "节拍：%s｜本镜信息目的：推进｜原文依据：“%s”｜载体：%s" % (text, evidence, carrier)


def _plan(n_events=3, **over):
    """n 个主要事件 / n 条节拍 / 一句台词 / 一条必保留事实（承载双向闭合）."""
    _ev = list("甲乙丙丁戊己")[:n_events]
    p = {"main_events": _ev, "climax": _ev[-1],
         "causal_chain": ["→".join(_ev)],
         "required_facts": ["%s｜原文证据：“%s”｜承载：B1/台词1" % (_reword("甲板上有人"), _LINE)],
         "hook_spoken": [], "hook_carrier": "", "narration_lines": [],
         "dialogue_lines": [_LINE],
         "shot_beats": [_beat("甲板上有风", "台词1", _LINE)]
                       + [_beat(e, "画面", _LINE) for e in _ev[1:]]}
    p.update(over)
    return p


def _reworded(plan):
    _p = json.loads(json.dumps(plan, ensure_ascii=False))
    _p["required_facts"] = [_reword(x) for x in _p["required_facts"]]
    _p["shot_beats"] = [_reword(x) for x in _p["shot_beats"]]
    # **台词/旁白本体也是原文的一部分**：同构改写必须一起改，否则计划与原文不再对得上
    _p["dialogue_lines"] = [_reword(x) for x in _p.get("dialogue_lines") or []]
    _p["narration_lines"] = [dict(x, text=_reword(x["text"]), source=_reword(x["source"]))
                             for x in _p.get("narration_lines") or []]
    return _p


def _codes(plan, novel):
    return sorted({x["code"] for x in o.plan_acceptance_problems(plan, novel)})


# ---------------------------------------------------------------------------
# P1 措辞替换 / 编号 / 顺序：语义相同 ⇒ 裁决一致；关键关系被改 ⇒ 能区分
# ---------------------------------------------------------------------------
def test_p1_rewording_the_novel_and_the_plan_together_keeps_the_verdict():
    """把小说与计划**同步**换措辞（同构）⇒ 裁决逐项一致（都通过、或同样的 code 集合）."""
    _src2 = _reword(_SRC)
    assert _codes(_plan(), _SRC) == _codes(_reworded(_plan()), _src2) == [], "同构合法计划都应通过"
    # 反例（承载错配）：同构改写后**问题集合必须相同**——判据不许依赖具体措辞
    _broken = {"required_facts": ["甲板上有人｜原文证据：“%s”｜承载：B2/台词1" % _LINE]}
    _codes_a = _codes(_plan(**_broken), _SRC)
    _codes_b = _codes(_reworded(_plan(**_broken)), _src2)
    assert _codes_a and _codes_a == _codes_b, (_codes_a, _codes_b)


def test_p1b_changing_only_the_plan_but_not_the_novel_is_detected():
    """**关键关系被改**（只改计划、不改原文）⇒ 裁决必须变（原文逐字核验抓得住）."""
    _ok = _codes(_plan(), _SRC)
    _bad = _codes(o.plan_acceptance_problems(_reworded(_plan()), _SRC), _SRC)
    assert _ok == [] and _bad and _bad != _ok, (_ok, _bad)


@pytest.mark.parametrize("n", [3, 4, 5, 6])
def test_p1c_legal_numbering_variants_decide_the_same(n):
    """**合法编号域**（3–6 个事件）内，编号变化不改变裁决；少一条节拍则必须被抓到."""
    assert _codes(_plan(n_events=n), _SRC) == [], n
    if n > 3:                                    # 节拍少于事件 ⇒ 覆盖判据必须报出来
        _p = _plan(n_events=n)
        _p["shot_beats"] = _p["shot_beats"][:-1]
        assert "structure_beats_cover_events" in _codes(_p, _SRC), _codes(_p, _SRC)


def test_p1d_reordering_independent_entries_keeps_the_verdict():
    """调换**互不相干**的两条（两条旁白）⇒ 裁决不变；把承载指向改错则必须报出来."""
    _n1 = {"text": _LINE, "source": _LINE, "fact_refs": []}
    _n2 = {"text": _LINE2, "source": _LINE2, "fact_refs": []}
    _base = _plan(narration_lines=[_n1, _n2],
                  shot_beats=[_beat("甲板上有风", "台词1", _LINE), _beat("乙", "旁白1", _LINE),
                              _beat("丙", "旁白2", _LINE2)])
    _swap = json.loads(json.dumps(_base, ensure_ascii=False))
    _swap["narration_lines"] = [_n2, _n1]
    _swap["shot_beats"][1] = _beat("乙", "旁白2", _LINE)
    _swap["shot_beats"][2] = _beat("丙", "旁白1", _LINE2)
    assert _codes(_base, _SRC) == _codes(_swap, _SRC) == [], (_codes(_base, _SRC), _codes(_swap, _SRC))
    _wrong = json.loads(json.dumps(_base, ensure_ascii=False))
    _wrong["shot_beats"][1] = _beat("乙", "画面", _LINE)        # 旁白1 没人承载 ⇒ 孤儿
    assert _codes(_wrong, _SRC) != [], "承载指向被改错必须能区分"


# ---------------------------------------------------------------------------
# P2 `no_change` 的归因证据（五种"没变"要能分辨）
# ---------------------------------------------------------------------------
@pytest.fixture()
def ledger(tmp_path):
    cl.set_ledger_path(tmp_path / "step01_calls.jsonl")
    yield
    cl.set_ledger_path(None)


def _rounds(tmp_path):
    return [json.loads(x) for x in
            (tmp_path / "step01_replan_rounds.jsonl").read_text(encoding="utf-8").splitlines()
            if x.strip() and '"first_version"' not in x]


def _run(monkeypatch, plan, payloads):
    _svc = _Svc(payloads)
    monkeypatch.setattr(o, "create_service", lambda *a, **k: _svc)
    # 2026-10（核验协议迁移）：本文件的 `payloads` 只按**规划**调用配量。核验协议演化后
    # 核验也要发请求，若它也走 `create_service` 就会把顺序回放列表耗光 ⇒
    # `核验调用失败：stub: 没有更多响应` → `executable=False` → 缓存不写，成片误红。
    # 经 `generate_outline_result` 的 `verify=` 覆盖缝注入**合法**核验记录
    # （`verification_helpers.legal_record_for`，四轴齐全、身份随 `digest` 绑定）。
    from tests import verification_helpers as _vh
    _gor_orig = o.generate_outline_result

    def _gor_with_verify(*a, **k):
        # 2026-10：原文取**本次调用**传进去的那一份——用模块常量猜会在
        # "本文件传 `_WIDE_NOVEL`、常量里却先命中 `_SRC`"这类情形下把记录
        # 绑到**另一份原文**上 ⇒ `核验记录违约` ⇒ "核验记录无法复核"。
        _novel = (a[0] if a else None) or k.get("novel_text") or _SRC
        k.setdefault("verify",
                     lambda view, digest: _vh.legal_record_for(view, _novel,
                                                              plan_digest=digest))
        return _gor_orig(*a, **k)

    monkeypatch.setattr(o, "generate_outline_result", _gor_with_verify)
    return o.generate_outline_result(_SRC, cache_path=None), _svc


def test_p2_a_no_change_round_carries_the_five_attribution_evidence_items(
        monkeypatch, ledger, tmp_path):
    """`no_change` 的轮次档必须带：修法可表达性 / 请求信息 / 原始响应 / 应用结果 / 残余问题."""
    _orphan = _plan(narration_lines=[{"text": _LINE2, "source": _LINE2, "fact_refs": []}],
                    shot_beats=[_beat("甲板上有风", "台词1", _LINE), _beat("乙", "画面", _LINE),
                                _beat("丙", "画面", _LINE)])
    # 模型回一份**合法但与原状一致**的声明（回填原声明 ⇒ 应用后计划没变）
    _same = {"narration_lines": [{"text": _LINE2, "source": _LINE2, "fact_refs": []}],
             "fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}]}
    _r, _svc = _run(monkeypatch, _orphan,
                    [json.dumps(_orphan, ensure_ascii=False),
                     json.dumps(_same, ensure_ascii=False)] * 2)
    assert _r["executable"] is False
    _rec = _rounds(tmp_path)[0]
    assert _rec["kind"] == "no_change", _rec
    _ev = _rec["evidence"]
    # 逐项断言（能力表新增了 `needs_view` 字段；整字典相等说不出是哪一项变了）
    assert _ev["fix_capability"]["expressible"] is True, _ev
    assert _ev["fix_capability"]["inexpressible"] == [], _ev
    assert _ev["fix_capability"]["undeclared"] == [], _ev
    assert _ev["request"]["problems"] == len(_rec["problems_in"]), _ev
    assert _ev["request"]["expanded"] + _ev["request"]["unexpanded"] == _ev["request"]["problems"]
    assert _ev["response"]["shape"] == "ops" and _ev["response"]["keys"], _ev
    assert _ev["applied"] is False, "回填原声明 ⇒ 应用器判出'没有改动'"
    assert _ev["route"] == "ops", _ev
    assert _rec["problems_out"] is None or isinstance(_rec["problems_out"], list), _rec


def test_p2b_an_empty_response_and_a_replayed_declaration_are_distinguishable(
        monkeypatch, ledger, tmp_path):
    """两种"没变"必须分得清：**空操作**（什么都没回）vs **回填原声明**（回了但结果一致）.

    两次运行分开跑（`no_change` 是计划层终局，一轮就停），各留各的轮次档。
    """
    _orphan = _plan(narration_lines=[{"text": _LINE2, "source": _LINE2, "fact_refs": []}],
                    shot_beats=[_beat("甲板上有风", "台词1", _LINE), _beat("乙", "画面", _LINE),
                                _beat("丙", "画面", _LINE)])

    def _one(sub, payload):
        _dir = tmp_path / sub
        cl.set_ledger_path(_dir / "step01_calls.jsonl")
        _run(monkeypatch, _orphan,
             [json.dumps(_orphan, ensure_ascii=False),
              json.dumps(payload, ensure_ascii=False)] * 2)
        _recs = [json.loads(x) for x in
                 (_dir / "step01_replan_rounds.jsonl").read_text(encoding="utf-8").splitlines()
                 if x.strip() and '"first_version"' not in x]
        assert _recs and _recs[0]["kind"] == "no_change", _recs
        return _recs[0]["evidence"]

    _empty_ev = _one("a", {"dialogue_ops": [], "beat_ops": []})
    _declared_ev = _one("b", {"narration_lines": [{"text": _LINE2, "source": _LINE2,
                                                   "fact_refs": []}],
                              "fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}]})
    assert _empty_ev["response"]["shape"] == _declared_ev["response"]["shape"] == "ops"
    # **这就是两种"没变"的区别**：空操作（各键计数为 0）vs 回填了完整声明
    assert set(_empty_ev["response"]["keys"]) == {"dialogue_ops", "beat_ops"}         and all(v == 0 for v in _empty_ev["response"]["keys"].values()), _empty_ev
    assert "narration_lines" in _declared_ev["response"]["keys"]         and "fact_carriers" in _declared_ev["response"]["keys"], _declared_ev
    assert _empty_ev["applied"] is False and _declared_ev["applied"] is False,         (_empty_ev, _declared_ev)


def test_p2c_an_inexpressible_fix_is_recorded_as_such(monkeypatch, ledger, tmp_path):
    """修法**不可表达**时，这条事实必须留在归因证据里（不许拿它冒充模型意图）."""
    _empty_dlg = _plan(dialogue_lines=[])
    _r, _svc = _run(monkeypatch, _empty_dlg,
                    [json.dumps(_empty_dlg, ensure_ascii=False),
                     "不是 JSON"] * 2)
    _ev = _rounds(tmp_path)[0]["evidence"]
    assert _ev["fix_capability"]["expressible"] is False, _ev
    assert "plan_no_dialogue_lines" in _ev["fix_capability"]["inexpressible"], _ev
    assert _ev["route"] == "full_plan", _ev
