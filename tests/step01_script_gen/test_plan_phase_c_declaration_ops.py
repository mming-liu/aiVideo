# -*- coding: utf-8 -*-
"""**C 阶段验收矩阵**（工作包 §4 C）：声明类操作与控制器规则.

复核第 45 轮点名的入口缺口：真实 `apply_replan_ops` 只读 `dialogue_ops` / `beat_ops`，
只给 `hook_spoken` / `hook_carrier` 时落 `no_change`——把"接口表达不了"记成"模型认为
无需改动"。本矩阵用**真实应用器**跑三类断言：

- **声明独立修改要成功**（退出条件）：只给一个声明键就能改到计划上，并让统一验收链
  从"不通过"变成"通过"；
- **非法输入要拒收**：未知键、错误类型、越界编号一律 `illegal_response`，且**基线不变**；
- **三态要分清**：缺席 = 不改；显式空值 = 按字段语义清除；应用后内容没变 = `no_change`。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from step01_script_gen import outline as o                                    # noqa: E402
from step01_script_gen import plan_revision as pr                             # noqa: E402

_LINE = "甲板上有风，帆布猎猎作响。"
_LINE2 = "他一步一顿地走进那间屋子。"
_SRC = "开头。%s事件乙。事件丙。%s" % (_LINE, _LINE2)


def _plan(**over):
    """**除声明外**都合法的计划：F1 的承载声明指向 B3（B3 是画面节拍）⇒ 承载不成立."""
    p = {
        "main_events": ["甲", "乙", "丙"],
        "climax": "丙",
        "causal_chain": ["甲→乙→丙"],
        "required_facts": ["甲板上有风，帆布猎猎作响｜原文证据：“%s”｜承载：B3/台词1" % _LINE],
        "hook_spoken": [],
        "hook_carrier": "",
        "dialogue_lines": [_LINE],
        "narration_lines": [],
        "shot_beats": [
            "节拍：甲板上有风｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % _LINE,
            "节拍：乙｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _LINE,
            "节拍：丙｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _LINE],
    }
    p.update(over)
    return p


def _fixed_plan(**over):
    """**完整验收通过**的对照计划（F1 由 B1/台词1 承载）."""
    return _plan(required_facts=["甲板上有风，帆布猎猎作响｜原文证据：“%s”｜承载：B1/台词1" % _LINE],
                 **over)


def _codes(plan):
    return [x["code"] for x in o.plan_acceptance_problems(plan, _SRC)]


def _baseline(p):
    return json.dumps(p, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# C01 **声明独立修改经真实应用器成功**（工作包 §4 C 退出条件）
# ---------------------------------------------------------------------------
def test_c01_a_carrier_only_batch_is_applied_and_closes_the_contract():
    """复核的入口缺口：只给 `fact_carriers` ⇒ 旧实现 `no_change`，现在必须应用."""
    p = _plan()
    assert _codes(p) == ["carrier_fact_line_mismatch"], _codes(p)
    _before = _baseline(p)
    new, why = o.apply_replan_ops(p, {"fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}]},
                                  _SRC)
    assert new is not None and why == "", why
    assert "承载：B1/台词1" in new["required_facts"][0], new["required_facts"]
    assert _baseline(p) == _before, "应用器不许改动传入的计划（返回值才是新版本）"
    assert _codes(new) == [], _codes(new)


def test_c01_a_hook_only_batch_is_applied():
    p = _fixed_plan(hook_spoken=[_LINE])
    assert "carrier_hook_unlabeled" in _codes(p), _codes(p)
    new, why = o.apply_replan_ops(p, {"hook_carrier": "B1/台词1"}, _SRC)
    assert new is not None and why == "", why
    assert new["hook_carrier"] == "B1/台词1"
    assert _codes(new) == [], _codes(new)


def test_c01_a_hook_spoken_only_batch_is_applied():
    p = _fixed_plan()
    new, why = o.apply_replan_ops(p, {"hook_spoken": [_LINE]}, _SRC)
    assert new is not None and why == "", why
    assert new["hook_spoken"] == [_LINE]
    assert "carrier_hook_unlabeled" in _codes(new), "声明了「必须说出口」却没有承载 ⇒ 验收报缺承载"


def test_c01_a_narration_only_batch_is_applied():
    p = _fixed_plan()
    _narr = [{"text": _LINE2, "source": _LINE2, "fact_refs": []}]
    new, why = o.apply_replan_ops(p, {"narration_lines": _narr}, _SRC)
    assert new is not None and why == "", why
    assert new["narration_lines"] == _narr, new["narration_lines"]
    # 应用器只负责"把声明落进计划"：这条旁白没有节拍承载 ⇒ 由**同一条**验收链报出来
    # （模型要给出合法终态，得同时改承载它的节拍——那是 `beat_ops` 的事）。
    assert "orphan_uncarried" in _codes(new), _codes(new)


# ---------------------------------------------------------------------------
# C02 非法输入 ⇒ `illegal_response`，且**基线一个字节不改**（工作包 §4 C）
# ---------------------------------------------------------------------------
_C02 = [
    ("未知键", {"dialogue_ops": [], "beat_ops": [], "fact_carriers": [], "hook_carrier": "",
              "carrier_ops": []}, "未知操作键"),
    ("fact_carriers 不是数组", {"fact_carriers": "x"}, "不是数组"),
    ("fact_carriers 条目不是对象", {"fact_carriers": [1]}, "必须是对象"),
    ("hook_carrier 不是字符串", {"hook_carrier": 7}, "必须是字符串"),
    ("hook_carrier 声明语法非法", {"hook_carrier": "第3节拍"}, "声明非法"),
    ("hook_carrier 声明非法", {"hook_carrier": "B9/台词1"}, "越界"),
    ("hook_spoken 不是数组", {"hook_spoken": 7}, "数组"),
    ("hook_spoken 有空条目", {"hook_spoken": ["  "]}, "空/非法条目"),
    ("narration_lines 不是数组", {"narration_lines": {}}, "数组"),
    ("narration_lines 缺出处", {"narration_lines": [{"text": _LINE, "fact_refs": []}]},
     "text/source"),
    ("narration_lines fact_refs 非整数",
     {"narration_lines": [{"text": _LINE, "source": _LINE, "fact_refs": ["1"]}]}, "非整数"),
    ("fact_carriers 越界事实", {"fact_carriers": [{"index": 9, "beat": 1, "mode": "画面"}]},
     "不存在的必保留事实"),
    ("fact_carriers 越界节拍", {"fact_carriers": [{"index": 1, "beat": 9, "mode": "画面"}]},
     "节拍越界"),
    ("fact_carriers 越界台词", {"fact_carriers": [{"index": 1, "beat": 1, "mode": "台词9"}]},
     "越界"),
    ("fact_carriers 越界旁白", {"fact_carriers": [{"index": 1, "beat": 1, "mode": "旁白1"}]},
     "越界"),
    ("fact_carriers 非法载体", {"fact_carriers": [{"index": 1, "beat": 1, "mode": "镜头"}]},
     "不是合法载体"),
    ("fact_carriers 重复声明", {"fact_carriers": [{"index": 1, "beat": 1, "mode": "画面"},
                                              {"index": 1, "beat": 2, "mode": "画面"}]},
     "声明了多次"),
]


@pytest.mark.parametrize("name,ops,why_frag", _C02, ids=[x[0] for x in _C02])
def test_c02_illegal_inputs_are_rejected_and_the_baseline_is_untouched(name, ops, why_frag):
    p = _plan()
    _before = _baseline(p)
    new, why = o.apply_replan_ops(p, ops, _SRC)
    assert new is None, (name, new)
    assert why != "no_change", (name, "非法输入不能记成「模型认为无需改动」", why)
    assert why_frag in why, (name, why)
    assert _baseline(p) == _before, (name, "拒收后输入不许被改")


# ---------------------------------------------------------------------------
# C03 三态：缺席 / 显式清除 / 应用后内容没变
# ---------------------------------------------------------------------------
def test_c03_absent_and_empty_lists_are_no_change():
    p = _fixed_plan()
    for _ops in ({}, {"dialogue_ops": [], "beat_ops": [], "fact_carriers": []},
                 {"fact_carriers": []}):
        new, why = o.apply_replan_ops(copy.deepcopy(p), _ops, _SRC)
        assert new is None and why == "no_change", (_ops, why)


def test_c03_an_idempotent_declaration_is_no_change():
    """把 `fact_carriers` 声明成**当前就是**的样子 ⇒ 应用后内容不变 ⇒ `no_change`."""
    p = _fixed_plan()
    new, why = o.apply_replan_ops(copy.deepcopy(p),
                                  {"fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}]},
                                  _SRC)
    assert new is None and why == "no_change", why


def test_c03_explicit_empty_values_clear_the_old_declaration():
    """**显式空值 = 清除**（不因 falsey 被忽略）：`hook_carrier` / `hook_spoken`."""
    p = _fixed_plan(hook_spoken=[_LINE], hook_carrier="B1/台词1")
    assert _codes(p) == [], _codes(p)
    new, why = o.apply_replan_ops(copy.deepcopy(p),
                                  {"hook_spoken": [], "hook_carrier": ""}, _SRC)
    assert new is not None and why == "", why
    assert new["hook_spoken"] == [] and not new.get("hook_carrier"), new
    assert _codes(new) == [], _codes(new)


def test_c03_explicit_empty_narration_clears_the_table():
    p = _fixed_plan(
        narration_lines=[{"text": _LINE2, "source": _LINE2, "fact_refs": []}],
        shot_beats=["节拍：甲板上有风｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % _LINE,
                    "节拍：他走进屋子｜本镜信息目的：推进｜原文依据：“%s”｜载体：旁白1" % _LINE2,
                    "节拍：丙｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _LINE])
    new, why = o.apply_replan_ops(copy.deepcopy(p), {"narration_lines": []}, _SRC)
    assert new is not None and why == "", why
    assert new["narration_lines"] == [], new["narration_lines"]


# ---------------------------------------------------------------------------
# C04 额度/反馈链/缓存（工作包 §4 C 的退出条件）
# ---------------------------------------------------------------------------
_LONG = "他一步一顿地走进那间堆满旧物的屋子，四下打量了很久很久，最后什么也没有说出口。"
_SRC_B = "开头。%s" % _LONG


def _over_budget_plan(**over):
    """**只有预算问题**（超时长）的合法计划：11 句长台词 ⇒ 估时 > 上限."""
    p = {"main_events": ["甲", "乙", "丙"], "climax": "丙", "causal_chain": ["甲→乙→丙"],
         "required_facts": ["他走进屋子｜原文证据：“%s”｜承载：B1/台词1" % _LONG],
         "hook_spoken": [], "hook_carrier": "", "narration_lines": [],
         "dialogue_lines": [_LONG] * 11,
         "shot_beats": ["节拍：他走进屋子｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % _LONG]
         + ["节拍：旁白%d｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词%d" % (i, _LONG, i)
            for i in range(2, 12)]}
    p.update(over)
    return p


def test_c04_the_three_call_quota_is_one_context():
    """契约修订与预算重选**共用**一份额度：三次就是三次（失败/非法/无变化都扣）."""
    _seen: list = []

    def _accept(plan, novel_text=""):
        return [{"code": "carrier_x", "kind": "fact", "index": 1,
                 "path": "required_facts[0].承载", "reason": "还缺承载"}]

    def _revise(plan, problems, budget, rejections=None):
        _seen.append(budget.calls_left)
        return {"kind": "applied",
                "plan": {"shot_beats": ["节拍：%d｜载体：画面" % len(_seen)]}}

    _res = o.run_plan_revision(_plan(), _SRC, accept=None) if False else None
    _ctl = pr.RevisionController(accept=_accept, revise=_revise,
                                 budget=pr.RevisionBudget(max_calls=3))
    _res = _ctl.run(_plan(), _SRC)
    assert _res["calls"] == 3 and len(_seen) == 3, _res
    assert _seen == [2, 1, 0], _seen   # `spend()` 在调用**前**扣
    assert _res["stop_reason"] == pr.STOP_CALLS, _res
    assert _res["plan_level_conclusion"] is True, _res


def test_c04_a_carrier_breaking_revision_continues_in_the_same_chain():
    """用**真实应用器**跑：一轮修订把承载改坏 ⇒ **同一条验收链**接着判、按统一停止原因收口.

    退出条件（工作包 §4 C）：预算修订破坏承载后**继续进入同一验收/反馈链**，不被静默接受。
    这里改用声明通道触发同一后果（改坏 F1 的承载），链路与判据完全相同。
    """
    def _accept(plan, novel_text=""):
        return o.plan_acceptance_problems(plan, _SRC)

    def _revise(plan, problems, budget, rejections=None):
        # 把 F1 的承载从 B3 改到 **B2**（同样是画面节拍）——改是改了，仍然不成立
        _new, _why = o.apply_replan_ops(
            plan, {"fact_carriers": [{"index": 1, "beat": 2, "mode": "台词1"}]}, _SRC)
        if _new is None:
            return {"kind": "no_change", "detail": _why}
        return {"kind": "applied", "plan": _new}

    _res = pr.RevisionController(accept=_accept, revise=_revise,
                                 budget=pr.RevisionBudget(max_calls=3)).run(_plan(), _SRC)
    # ① 改坏后**没有被当成修好**：同一个验收链继续报承载问题
    assert _res["calls"] == 2, _res
    assert _res["stop_reason"] == pr.STOP_NO_CHANGE, _res
    assert _res["residual_class"] == pr.RESIDUAL_CONTRACT, _res
    assert _res["plan_level_conclusion"] is True, _res
    assert "carrier_fact_line_mismatch" in [x["code"] for x in _res["problems"]], _res["problems"]
    assert "承载：B2/台词1" in _res["plan"]["required_facts"][0], _res["plan"]["required_facts"]


def _ctl_run(accept, revise, plan, *, max_calls=3):
    return pr.RevisionController(accept=accept, revise=revise,
                                 budget=pr.RevisionBudget(max_calls=max_calls)).run(plan, _SRC_B)


def test_c04_the_pipeline_dedupe_does_not_preempt_the_controller_quota(monkeypatch):
    """`plan_replan.already_attempted` 只对**流水线直调**生效；控制器驱动时次数归额度."""
    from step01_script_gen import plan_feasibility as _pf
    from step01_script_gen import outline as _o
    from step01_script_gen import validation as _val

    # 本用例的 `_over_budget_plan()` 按 60–90s 合同构造；固定上限，避免生产
    # 默认放宽到 120s 后「已超预算」前提消失。
    monkeypatch.setattr(_val, "_REDGUO_DURATION_MIN", 60.0)
    monkeypatch.setattr(_val, "_REDGUO_DURATION_MAX", 90.0)

    _calls: list = []

    class _Svc:
        def generate(self, req):
            _calls.append(req)
            return type("R", (), {"success": True, "text": json.dumps(_over_budget_plan(),
                                                                     ensure_ascii=False)})()

    monkeypatch.setattr(_o, "create_service", lambda *a, **k: _Svc())
    _p = _over_budget_plan(plan_replan={"result": "infeasible",
                                        "criteria_version": _pf.replan_criteria_version()})
    # ① 流水线直调：旧口径仍生效（0 次调用，标 already_attempted）
    _p1, _info1 = _o.enforce_budget_by_replanning(copy.deepcopy(_p), _SRC_B)
    assert _info1.get("skipped") == "already_attempted" and _calls == [], _info1
    # ② 控制器驱动：**照样花一次**（次数由控制器的额度管，不被历史元数据抢先）
    _revise = lambda plan, problems, budget, rejections=None: _o.revise_plan_once(  # noqa: E731
        plan, problems, budget, rejections, novel_text=_SRC_B)
    _res = _ctl_run(lambda plan, nt="": o.plan_acceptance_problems(plan, _SRC_B), _revise,
                    copy.deepcopy(_p), max_calls=1)
    assert len(_calls) == 1, ("控制器驱动必须真的调用一次", len(_calls))
    assert _res["calls"] == 1, _res


def test_c04_a_cached_version_that_fails_acceptance_is_not_reused(tmp_path, monkeypatch):
    """可执行缓存**只放通过验收的版本**：读的时候也要核，历史失败元数据不算验收."""
    from step01_script_gen import outline as _o
    from step01_script_gen import plan_feasibility as _pf

    _cache = tmp_path / "chapter_outline.cache.json"
    _calls: list = []
    _legal = _fixed_plan()
    _novel = _SRC

    class _Svc:
        def generate(self, req):
            _calls.append(req)
            return type("R", (), {"success": True,
                                  "text": json.dumps(_legal, ensure_ascii=False)})()

    monkeypatch.setattr(_o, "create_service", lambda *a, **k: _Svc())
    # 该测试断言的是缓存准入/隔离；核验使用与本次调用原文及候选摘要绑定的合法记录，
    # 避免缓存测试被真实核验通道或空响应桩干扰。
    from tests import verification_helpers as _vh
    _generate_result = _o.generate_outline_result

    def _with_verification(*a, **k):
        _novel_text = (a[0] if a else None) or k.get("novel_text") or ""
        k.setdefault("verify", lambda view, digest: _vh.legal_record_for(
            view, _novel_text, plan_digest=digest))
        return _generate_result(*a, **k)

    monkeypatch.setattr(_o, "generate_outline_result", _with_verification)
    # 手写一份"键对得上、但计划过不了验收（承载错位）"的缓存
    _o.generate_outline(_novel, cache_path=_cache)
    _n_first = len(_calls)
    assert _n_first >= 1
    _cached = json.loads(_cache.read_text(encoding="utf-8"))
    _cached["outline"] = _plan()                       # 承载错位 ⇒ 过不了统一验收
    _cache.write_text(json.dumps(_cached, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(_o, "OUTLINE_PROMPT", _o.OUTLINE_PROMPT)
    _o.generate_outline(_novel, cache_path=_cache)
    assert len(_calls) > _n_first, "缓存里是失败版本 ⇒ 不许复用，必须重新规划"
    assert (_cache.with_name(_cache.name + ".bak-rejected")).exists(), "旧缓存必须留档"


def test_c05_replan_ops_schema_and_applier_share_one_key_set():
    """**键集合只此一处**（工作包 §4 C）：schema 的 properties 与 `REPLAN_OP_KEYS` 必须一致.

    判别性：任一侧加了键而另一侧没跟上 ⇒ 本用例失败（"请求契约与消费契约自相矛盾"
    正是这一轮入口缺口的成因）。
    """
    from _text_gen.deepseek_schema import REPLAN_OPS_SCHEMA
    from step01_script_gen.plan_feasibility import REPLAN_OP_KEYS
    assert set(REPLAN_OPS_SCHEMA["schema"]["properties"]) == set(REPLAN_OP_KEYS), (
        sorted(REPLAN_OPS_SCHEMA["schema"]["properties"]), sorted(REPLAN_OP_KEYS))


# ---------------------------------------------------------------------------
# C06 **请求协议三处同源**（复核 2026-10-02 P1）
# 提示 / schema 必填规则 / 应用器语义必须是**同一套**：必填键的缺省填充值只能
# 是"不改"；"清除"只能由**非缺省**的显式写法表达。
# ---------------------------------------------------------------------------
def _schema_default(spec):
    """模型在**必填**键上会填的缺省值（按 schema 声明的类型）。"""
    return {"array": [], "string": "", "integer": 0, "boolean": False}.get(spec.get("type"))


def _wire(**over):
    """**符合 schema 的响应**：只含 `required` 里的键（各填类型缺省值），叠加本次真要改的。"""
    from _text_gen.deepseek_schema import REPLAN_OPS_SCHEMA
    _sch = REPLAN_OPS_SCHEMA["schema"]
    _w = {_k: _schema_default(_sch["properties"][_k]) for _k in _sch["required"]}
    _w.update(over)
    return _w


def test_c06_required_keys_have_non_destructive_defaults():
    """**必填键的缺省填充值必须安全**（P1 的共同修法，不是给某个键打补丁）.

    判别性：把 `hook_carrier` 放回 `required`（或把它在语义表里标成"空值 = 清除"）
    ⇒ ①静态不变量失败；②行为断言失败——按 schema 填的 `""` 会把合法钩子承载清掉。
    """
    from _text_gen.deepseek_schema import REPLAN_OPS_SCHEMA
    from step01_script_gen.plan_feasibility import DECL_OP_EMPTY_MEANS_CHANGE
    _sch = REPLAN_OPS_SCHEMA["schema"]
    assert set(_sch["required"]) <= set(_sch["properties"])
    for _k in _sch["required"]:
        assert not DECL_OP_EMPTY_MEANS_CHANGE.get(_k, False), (
            "必填键 %s 的空值语义是「清除」：模型没有话可说时按 schema 填的缺省值 "
            "会破坏合法计划" % _k)
    # 行为面：**只按 required 填缺省值**的响应不得改动合法计划（也不许变成"改动成功"）
    _p = _fixed_plan(hook_spoken=[_LINE], hook_carrier="B1/台词1")
    _before = _baseline(_p)
    _new, _why = o.apply_replan_ops(_p, _wire(), _SRC)
    assert _new is None and _why == "no_change", (_new, _why)
    assert _baseline(_p) == _before, "no_change 时基线必须逐字不变"


def test_c06_a_schema_conforming_fact_only_batch_keeps_the_hook():
    """**只修事实、不动原有钩子**（复核给的退出用例）：响应按 schema 形态构造."""
    _p = _plan(hook_spoken=[_LINE], hook_carrier="B1/台词1")
    assert _codes(_p) == ["carrier_fact_line_mismatch"], _codes(_p)   # 只有事实承载错位
    _new, _why = o.apply_replan_ops(
        _p, _wire(fact_carriers=[{"index": 1, "beat": 1, "mode": "台词1"}]), _SRC)
    assert _new is not None and _why == "", (_new, _why)
    assert _new.get("hook_carrier") == "B1/台词1", "合法钩子承载不许被顺手清掉"
    assert _new.get("hook_spoken") == [_LINE], "钩子说出口的声明同样不许被顺手清掉"
    assert _codes(_new) == [], _codes(_new)


def test_c06_clearing_the_hook_is_explicit_and_omission_is_not_clearing():
    """**明确清除钩子**与**省略 = 不改**必须分得开（退出用例之二）."""
    _p = _fixed_plan(hook_spoken=[_LINE], hook_carrier="B1/台词1")
    _clear, _why = o.apply_replan_ops(_p, _wire(hook_carrier=""), _SRC)
    assert _clear is not None and not _clear.get("hook_carrier"), (_clear, _why)
    _keep, _why2 = o.apply_replan_ops(_p, _wire(), _SRC)
    assert _keep is None and _why2 == "no_change", (_keep, _why2)
    assert _p.get("hook_carrier") == "B1/台词1", "不声明就不许动"


# ---------------------------------------------------------------------------
# C07 **实际发送的请求**必须"只回操作"（P1 的请求侧）
# ---------------------------------------------------------------------------
class _StubSvc:
    """本地服务桩：只记请求、只回给定文本（不碰网络）."""

    def __init__(self, text="", ok=True):
        self.reqs = []
        self.text = text
        self.ok = ok

    def generate(self, request):
        self.reqs.append(request)
        from _text_gen.models import TextGenerationResult
        return TextGenerationResult(success=self.ok, text=self.text,
                                    error=None if self.ok else "stub")


def _stub(monkeypatch, payload=None):
    _svc = _StubSvc(json.dumps(payload, ensure_ascii=False) if payload is not None else "")
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
    return _svc


def test_c07_the_revision_request_asks_only_for_ops(monkeypatch):
    """**实际发送的请求**里不许再出现"输出整份大纲"的要求（P1：请求协议自相矛盾）.

    判别性：基底换回 `OUTLINE_PROMPT` ⇒ 转红（它的「# 输出格式」块列出整份 JSON 字段表，
    与"只回操作"互相矛盾）；把 `_PLAN_OPS_INSTRUCTION` 摘掉也转红。
    """
    from _text_gen.deepseek_schema import REPLAN_OPS_SCHEMA
    from step01_script_gen.plan_feasibility import REPLAN_OP_KEYS
    _p = _plan(hook_spoken=[_LINE], hook_carrier="B1/台词1")
    _svc = _stub(monkeypatch)
    o._replan_by_ops(_p, o.plan_acceptance_problems(_p, _SRC), _SRC)
    assert len(_svc.reqs) == 1
    _req = _svc.reqs[0]
    assert _req.json_schema is REPLAN_OPS_SCHEMA
    _up = _req.user_prompt
    assert "## 请**只输出操作**" in _up, "请求必须带上操作式说明"
    assert "声明键的三种写法" in _up and "省略该键 = 不改" in _up, (
        "三态语义必须在**实际发送的**提示里写清")
    assert "# 输出格式" not in _up and '"time_budget"' not in _up, (
        "操作式请求不许再带「输出整份大纲」的格式要求")
    for _k in REPLAN_OP_KEYS:
        assert _k in _up, "提示没有为受支持的键 %s 写出写法" % _k
    assert _SRC[:20] in _up, "原文仍是上下文"
    assert "【当前计划（已规范化" in _up, "必须给只读的当前计划（编号与验收同一套）"


def test_c07_the_ops_semantics_are_written_once():
    """三态语义**只有一处**（`_PLAN_OPS_INSTRUCTION`）：反馈块不再维护第二份规则.

    判别性：把旧的矛盾文案（同一段既说"空数组 = 沿用"又说"空数组 = 清除"）放回去 ⇒ 转红。
    """
    from step01_script_gen.plan_feasibility import REPLAN_OP_KEYS
    from step01_script_gen.plan_feasibility import DECL_OP_KEYS
    _txt = o._PLAN_OPS_INSTRUCTION
    assert "声明键的三种写法" in _txt, "三态语义必须有一处明确的说明"
    assert "**省略该键 = 不改**（旧值原样保留；这些键在 schema 里没有 `null` 取值" in _txt
    assert "**给内容** = 设置" in _txt
    assert "留空数组 = 沿用" not in _txt, "自相矛盾的旧文案不许回来"
    for _k in REPLAN_OP_KEYS:
        assert _k in _txt, "操作说明没有覆盖受支持的键 %s" % _k
    for _k in DECL_OP_KEYS:
        assert _k in _txt, "声明键 %s 没有写出用法" % _k
    _fb = o._revision_feedback_block(o.plan_acceptance_problems(_plan(), _SRC), _plan())
    assert "**可以改**" not in _fb, (
        "「可以改什么」只在操作说明里写一份；反馈块重复维护必然漂移")


# ---------------------------------------------------------------------------
# C08 **所有受支持形态走同一条声明校验**（复核 2026-10-02 P2）
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ops,why_frag", [
    ({"hook_carrier": {"beat": 999, "mode": "台词1"}}, "越界"),
    ({"hook_carrier": {"beat": 0, "mode": "台词1"}}, "越界"),
    ({"hook_carrier": {"beat": 1, "mode": "台词9"}}, "越界"),
    ({"hook_carrier": {"beat": 1, "mode": "旁白1"}}, "越界"),
    # `beat: null` 的行由**形状层**先挡下（复核 2026-10-02 之四 C：嵌套 `null` 不是"不改"，
    # 也不许归一成空载体——旧实现只核键集合，`B/台词1` 会拼出来交给声明解析器报"声明非法"）
    ({"hook_carrier": {"beat": None, "mode": "台词1"}}, "null"),
    ({"hook_carrier": {"beat": 1}}, "声明非法"),
    ({"hook_carrier": "B1/台词1；B99/画面"}, "越界"),
])
def test_c08_declaration_forms_go_through_the_same_validation(ops, why_frag):
    """对象形态不得绕过编号/载体校验（旧实现直接拼 `B{beat}/{mode}` 就写进计划）."""
    _p = _plan(hook_spoken=[_LINE], hook_carrier="B1/台词1")
    _before = _baseline(_p)
    _new, _why = o.apply_replan_ops(_p, ops, _SRC)
    assert _new is None and why_frag in _why, (_new, _why)
    assert _baseline(_p) == _before, "拒收时基线必须逐字不变"


def test_c08_a_legal_object_form_still_applies():
    """合法对照：对象形态是**历史兼容**形态，归一后照样能设上（不是一律拒收）."""
    _p = _plan(hook_spoken=[_LINE2], hook_carrier="")
    _new, _why = o.apply_replan_ops(_p, {"hook_carrier": {"beat": 1, "mode": "画面"}}, _SRC)
    assert _new is not None and _new["hook_carrier"] == "B1/画面", (_new, _why)


def test_c08_an_illegal_declaration_via_the_real_request_is_rejected(monkeypatch):
    """从 `_replan_by_ops` 的本地桩回一个**越界**声明 ⇒ 拒收，基线逐字不变（退出用例）."""
    _p = _plan(hook_spoken=[_LINE], hook_carrier="B1/台词1")
    _before = _baseline(_p)
    _svc = _stub(monkeypatch, _wire(hook_carrier={"beat": 999, "mode": "台词1"}))
    _new, _why, _cid = o._replan_by_ops(_p, o.plan_acceptance_problems(_p, _SRC), _SRC)
    assert _new is None and "越界" in _why, (_new, _why)
    assert len(_svc.reqs) == 1 and _baseline(_p) == _before


def test_c08_a_schema_conforming_clear_via_the_real_request_applies(monkeypatch):
    """从同样的桩回一个**明确清除**：应用成功且只清声明（退出用例之三）."""
    _p = _fixed_plan(hook_spoken=[_LINE], hook_carrier="B1/台词1")
    _stub(monkeypatch, _wire(hook_carrier=""))
    _new, _why, _cid = o._replan_by_ops(_p, [], _SRC)
    assert _new is not None and _why == "", (_new, _why)
    assert not _new.get("hook_carrier"), _new
    assert _new.get("hook_spoken") == [_LINE], "只清承载声明，说出口的声明不受影响"


_NARR = [{"text": _LINE2, "source": _LINE2, "fact_refs": []}]
_NARR_BEATS = ["节拍：甲板上有风｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % _LINE,
               "节拍：他走进屋子｜本镜信息目的：推进｜原文依据：“%s”｜载体：旁白1" % _LINE2,
               "节拍：丙｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _LINE]


@pytest.mark.parametrize("key", ["hook_carrier", "hook_spoken", "narration_lines"])
def test_c06_null_is_absent_not_clearing(key):
    """**可省略的**声明键给 `null` = 缺席（不改），不是"清除".

    同类静默破坏（主动查的兄弟入口）：旧实现里 `{"narration_lines": null}` 会**清空整张
    旁白表**、`{"hook_carrier": null}` 会清掉钩子承载——与 P1 同一根因（拿缺省填充值
    当破坏性操作）。要清除必须写**空串/空数组**。

    **必填键（`fact_carriers`）不在此列**：它的 `null` 违反 schema，见
    `test_c12_a_required_key_null_is_illegal`（复核 2026-10-02 之三 P1）。
    """
    _p = _fixed_plan(hook_spoken=[_LINE], hook_carrier="B1/台词1",
                     narration_lines=list(_NARR), shot_beats=list(_NARR_BEATS))
    assert _codes(_p) == [], _codes(_p)
    _before = _baseline(_p)
    _new, _why = o.apply_replan_ops(copy.deepcopy(_p), {key: None}, _SRC)
    assert _new is None and _why == "no_change", (key, _new, _why)
    assert _baseline(_p) == _before


@pytest.mark.parametrize("key", ["hook_carrier", "hook_spoken", "narration_lines"])
def test_c06_a_null_declaration_in_a_mixed_batch_is_ignored(key):
    """混合批次里的 `null` 同样不许当清除（真正要改的那项照改）——第二层防护.

    入口闸门（`effective_decl_ops`）挡住"只有 null"的批次；这一条走的是"批次里还有
    真操作 ⇒ 闸门放行"，此时 `null` 由**各键自己的**处理挡掉。
    """
    _p = _plan(hook_spoken=[_LINE], hook_carrier="B1/台词1",
               narration_lines=list(_NARR), shot_beats=list(_NARR_BEATS))
    assert _codes(_p) == ["carrier_fact_line_mismatch"], _codes(_p)
    _new, _why = o.apply_replan_ops(
        copy.deepcopy(_p),
        {"fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}], key: None}, _SRC)
    assert _new is not None and _why == "", (key, _new, _why)
    assert _new.get("hook_carrier") == "B1/台词1", key
    assert _new.get("hook_spoken") == [_LINE], key
    assert _new.get("narration_lines") == _NARR, key
    assert _codes(_new) == [], (key, _codes(_new))


# ---------------------------------------------------------------------------
# C09 复核 2026-10-02（第二轮）：同批新增旁白 + 绑定（P1）、提示/schema/裁决逐键一致（P2）
# ---------------------------------------------------------------------------
def _add_narration_batch(carrier="旁白1"):
    """**同一批**新增一条旁白、并把它指派给一条新节拍（合法的联动修法）."""
    return {"narration_lines": [{"text": _LINE2, "source": _LINE2, "fact_refs": []}],
            # 动了节拍编号 ⇒ 事实承载必须逐项重声明（既有裁决）——F1 仍在 B1
            "fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}],
            "beat_ops": [{"op": "insert_after", "index": 1,
                          "new_text": "节拍：他走进屋子｜本镜信息目的：推进｜原文依据：“%s”"
                                      % _LINE2,
                          "new_carrier": carrier}]}


def test_c09_a_beat_bound_to_a_narration_added_in_the_same_batch_applies():
    """P1：`旁白k` 的合法范围按**本批完成后**的表算.

    判别性：把范围换回旧计划的条数 ⇒ 本用例转红（旧实现返回"本集共 0 条旁白"，
    控制器记成 `illegal_response`——一份**合法**的联动修法被诊断成"模型没按契约回"）。
    """
    _p = _fixed_plan()
    _new, _why = o.apply_replan_ops(copy.deepcopy(_p), _add_narration_batch(), _SRC)
    assert _new is not None and _why == "", (_new, _why)
    assert "旁白1" in _new["shot_beats"][1], _new["shot_beats"]
    assert _new["narration_lines"] == [{"text": _LINE2, "source": _LINE2, "fact_refs": []}]
    assert _codes(_new) == [], _codes(_new)


def test_c09_the_same_batch_through_the_real_request_applies(monkeypatch):
    """同一条修法走**真实入口**（`_replan_by_ops` + 本地服务桩）⇒ 应用 + 完整通过验收."""
    _p = _fixed_plan()
    _svc = _stub(monkeypatch, _add_narration_batch())
    _new, _why, _cid = o._replan_by_ops(_p, o.plan_acceptance_problems(_p, _SRC), _SRC)
    assert len(_svc.reqs) == 1
    assert _new is not None and _why == "", (_new, _why)
    assert _codes(_new) == [], _codes(_new)


def test_c09_a_carrier_beyond_the_post_batch_table_is_still_rejected():
    """**对照**：越出**本批完成后**的旁白表仍然拒收（"按新表算"不等于"不校验"）."""
    _p = _fixed_plan()
    _new, _why = o.apply_replan_ops(copy.deepcopy(_p), _add_narration_batch("旁白2"), _SRC)
    assert _new is None and "越界" in _why, (_new, _why)


_EMPTY_FORM = {"fact_carriers": [], "hook_carrier": "", "hook_spoken": [],
               "narration_lines": []}


@pytest.mark.parametrize("key", ["fact_carriers", "hook_carrier", "hook_spoken",
                                 "narration_lines"])
def test_c09_prompt_schema_and_applier_agree_per_key(key):
    """P2：**逐键**核对提示 / schema 描述 / 应用器裁决三处（不再只看文字标记）.

    判别性：任一处与语义表（唯一源）不一致 ⇒ 转红——包括"提示把空值统称清除"
    （`fact_carriers` 的裁决其实是"不改"）这种**方向性**错误。
    """
    from _text_gen.deepseek_schema import REPLAN_OPS_SCHEMA
    from step01_script_gen.plan_feasibility import (
        DECL_OP_EMPTY_MEANS_CHANGE, DECL_OP_SEMANTICS, decl_semantics_clause)
    _empty_clears, _clause = DECL_OP_SEMANTICS[key]
    assert DECL_OP_EMPTY_MEANS_CHANGE[key] is _empty_clears
    # ① 提示：那句话是**渲染**出来的（改表即改提示），且逐键都在
    _txt = o._PLAN_OPS_INSTRUCTION
    assert decl_semantics_clause() in _txt, "提示里的空值语义必须由判据侧渲染"
    assert _clause in _txt, "提示没有写清 %s 的空值语义" % key
    assert "**显式给空值**（空串 / 空数组）" not in _txt, (
        "不许把四个键的空值统称一种语义——`fact_carriers: []` 是「不改」")
    assert "这些键在 schema 里没有 `null` 取值" in _txt, (
        "提示不许把 `null` 说成「不改」（schema 没有这个取值，模型照写会被通道拒）")
    # ② schema：描述逐字含同一句话；**空值是破坏性**的键不许必填
    _sch = REPLAN_OPS_SCHEMA["schema"]
    _desc = _sch["properties"][key]["description"]
    assert _clause in _desc, (key, "schema 描述与判据侧的说法不一致", _desc)
    if _empty_clears:
        assert key not in _sch["required"], (
            "%s 的空值是破坏性的，不能必填：模型「没有话可说」时按 schema 填的"
            "缺省值会改坏合法计划" % key)
    # ③ 应用器：喂该键的空值 ⇒ 裁决与表一致
    _p = _fixed_plan(hook_spoken=[_LINE], hook_carrier="B1/台词1",
                     narration_lines=list(_NARR), shot_beats=list(_NARR_BEATS))
    _new, _why = o.apply_replan_ops(copy.deepcopy(_p), {key: _EMPTY_FORM[key]}, _SRC)
    if _empty_clears:
        assert _new is not None and _why == "", (key, _new, _why)
    else:
        assert _new is None and _why == "no_change", (key, _new, _why)


# ---------------------------------------------------------------------------
# C10 复核 2026-10-02（之三）：旁白**新增 / 删除 / 替换** × 同批重绑节拍
# 残留孤儿的枚举身份取自**候选计划**（旧实现按旧表，把本批删掉的旁白当成残留）
# ---------------------------------------------------------------------------
_LINE3 = "风停了，甲板上只剩下他一个人。"
_LINE4 = "他把门轻轻带上，屋子里安静下来。"
_SRC3 = "开头。%s事件乙。事件丙。%s事件丁。%s事件戊。%s" % (_LINE, _LINE2, _LINE3, _LINE4)
_NARR2 = [{"text": _LINE2, "source": _LINE2, "fact_refs": []},
          {"text": _LINE3, "source": _LINE3, "fact_refs": []}]
_NARR2_BEATS = ["节拍：甲板上有风｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % _LINE,
                "节拍：他走进屋子｜本镜信息目的：推进｜原文依据：“%s”｜载体：旁白1" % _LINE2,
                "节拍：风停了｜本镜信息目的：推进｜原文依据：“%s”｜载体：旁白2" % _LINE3]
_FACT1 = [{"index": 1, "beat": 1, "mode": "台词1"}]


def _narr_plan(**over):
    return _fixed_plan(narration_lines=list(_NARR2), shot_beats=list(_NARR2_BEATS), **over)


def _codes3(plan):
    return [x["code"] for x in o.plan_acceptance_problems(plan, _SRC3)]


_NARR_CASES = [
    ("删除 N2 并同批把承载它的节拍改成画面",
     {"narration_lines": [dict(_NARR2[0])], "fact_carriers": [dict(_FACT1[0])],
      "beat_ops": [{"op": "replace", "index": 3,
                    "new_text": "节拍：风停了｜本镜信息目的：推进｜原文依据：“%s”" % _LINE3,
                    "new_carrier": "画面"}]}),
    ("新增 N3 并同批绑定承载节拍",
     {"narration_lines": [dict(_NARR2[0]), dict(_NARR2[1]),
                          {"text": _LINE4, "source": _LINE4, "fact_refs": []}],
      "fact_carriers": [dict(_FACT1[0])],
      "beat_ops": [{"op": "insert_after", "index": 2,
                    "new_text": "节拍：安静下来｜本镜信息目的：推进｜原文依据：“%s”" % _LINE4,
                    "new_carrier": "旁白3"}]}),
    ("替换 N2 的正文（承载节拍不动）",
     {"narration_lines": [dict(_NARR2[0]),
                          {"text": _LINE4, "source": _LINE4, "fact_refs": []}]}),
]


@pytest.mark.parametrize("name,ops", _NARR_CASES, ids=[x[0] for x in _NARR_CASES])
def test_c10_narration_add_delete_and_replace_all_apply(name, ops):
    """复核 P1 的对照矩阵：新增 / 删除 / 替换旁白 × 同批重绑节拍都必须能应用且验收归零."""
    _p = _narr_plan()
    assert _codes3(_p) == [], _codes3(_p)
    _new, _why = o.apply_replan_ops(copy.deepcopy(_p), ops, _SRC3)
    assert _new is not None and _why == "", (name, _new, _why)
    assert _codes3(_new) == [], (name, _codes3(_new))


def test_c10_an_incomplete_result_goes_to_acceptance_not_the_applier():
    """对照：删了 N2 却**没**改承载它的节拍 ⇒ 应用器**不拒收**，由验收链报悬空.

    判别性：把旁白孤儿/悬空塞回应用器 ⇒ 本用例转红（会以 `illegal_response` 结束整轮，
    模型连反馈都拿不到）。悬空记录由 `carrier_dangling` 给出——"旁白"是第三条合法载体，
    这条镜像检查是它的配套面（此前只核了台词）。
    """
    _p = _narr_plan()
    _new, _why = o.apply_replan_ops(copy.deepcopy(_p), {"narration_lines": [dict(_NARR2[0])]},
                                    _SRC3)
    assert _new is not None and _why == "", (_new, _why)
    _probs = o.plan_acceptance_problems(_new, _SRC3)
    assert [x["code"] for x in _probs] == ["carrier_dangling"], _probs
    assert _probs[0]["index"] == 3 and "旁白2" in _probs[0]["reason"], _probs[0]


# ---------------------------------------------------------------------------
# C11 复核 2026-10-02（之三）P2：允许写的载体必须**逐字段**教全（应用器支持 ≠ 请求侧教了）
# ---------------------------------------------------------------------------
_CARRIER_DESC_PATHS = (("fact_carriers", "items", "mode"),
                       ("beat_ops", "items", "new_carrier"),
                       ("beat_ops", "items", "parts", "items", "new_carrier"))


def _schema_node(sch, path):
    """按 `("fact_carriers", "items", "mode")` 这样的路径取到**字段节点**（`items` 进数组元素）."""
    _n = sch["properties"]
    for _p in path:
        _n = _n["items"]["properties"] if _p == "items" else _n[_p]
    return _n


def test_c11_the_parser_accepts_exactly_the_advertised_carrier_kinds():
    """"能写什么"与"解析器认什么"必须**互相**对得上（两个方向都要判）."""
    from step01_script_gen.plan_feasibility import (
        CARRIER_MODE_KINDS, carrier_mode_hint, carrier_mode_problem)
    from step01_script_gen.plan_feasibility import CARRIER_KIND_FORMS, parse_beat_carrier
    assert [k for k, _h in CARRIER_MODE_KINDS] == ["画面", "台词", "旁白"]
    # 写法表必须覆盖解析器**能返回的全部**种类（加新载体时两处一起改，漏一处即红）
    assert set(CARRIER_KIND_FORMS) == {parse_beat_carrier(_s)[0]
                                       for _s in ("画面", "台词1", "旁白1")}
    assert carrier_mode_hint() == "`画面`、`台词k`、`旁白k`"
    for _kind, _hint in CARRIER_MODE_KINDS:           # 教了就要能写
        _mode = _hint.replace("k", "1")
        assert carrier_mode_problem(_mode, 1, 1) == "", _mode
    assert "不是合法载体" in carrier_mode_problem("镜头", 1, 1)   # 没教的写法拒收


@pytest.mark.parametrize("path", _CARRIER_DESC_PATHS,
                         ids=["%s.%s" % (p[0], p[-1]) for p in _CARRIER_DESC_PATHS])
def test_c11_every_carrier_field_in_the_schema_teaches_all_kinds(path):
    from _text_gen.deepseek_schema import REPLAN_OPS_SCHEMA
    from step01_script_gen.plan_feasibility import CARRIER_MODE_KINDS
    _desc = _schema_node(REPLAN_OPS_SCHEMA["schema"], path)["description"]
    for _kind, _hint in CARRIER_MODE_KINDS:
        assert _hint in _desc, (path, _kind, _desc)


def test_c11_the_actual_request_teaches_every_carrier_field(monkeypatch):
    """**实际发送的**提示里，每个"可写载体"的字段都要给全三种写法（复核 P2 的落点）."""
    from step01_script_gen.plan_feasibility import carrier_mode_hint
    _p = _narr_plan()
    _svc = _stub(monkeypatch)
    o._replan_by_ops(_p, o.plan_acceptance_problems(_p, _SRC3), _SRC3)
    _up = _svc.reqs[0].user_prompt
    assert carrier_mode_hint() in _up, "提示没给出允许写的全部载体"
    assert _up.count(carrier_mode_hint()) >= len(_CARRIER_DESC_PATHS), (
        "提示里 %d 个载体字段都要写全（现在 %d 处）"
        % (len(_CARRIER_DESC_PATHS), _up.count(carrier_mode_hint())))


# ---------------------------------------------------------------------------
# C12 复核 2026-10-02（之三）P1：**非法响应不许被改写成合法计划**
# 一处**本地响应校验**（按请求 schema）核对必填键的 `null` 规则、类型与嵌套键；
# 不许靠转换（`str(123)`）或丢字段（未知键）把违反 schema 的响应"修好"。
# ---------------------------------------------------------------------------
def test_c12_a_required_key_null_is_illegal():
    """必填键给 `null` **不是"缺席"**：整轮拒收（旧实现记成 `no_change`/照常应用）.

    判别性：把必填键的 `null` 重新按"缺席"读 ⇒ 转红（`_why` 变成 `no_change`）。
    """
    from step01_script_gen.plan_feasibility import REPLAN_REQUIRED_KEYS
    _p = _fixed_plan(hook_spoken=[_LINE], hook_carrier="B1/台词1")
    _before = _baseline(_p)
    for _k in REPLAN_REQUIRED_KEYS:
        _new, _why = o.apply_replan_ops(copy.deepcopy(_p), {_k: None}, _SRC)
        assert _new is None and "null" in _why, (_k, _new, _why)
        assert _why != "no_change", (_k, "必填键的 null 不许记成「模型认为无需改动」")
        assert _baseline(_p) == _before, (_k, "拒收后基线不许被改")


def test_c12_a_required_key_null_in_a_mixed_batch_is_illegal():
    """混合批次里也一样：别的操作合法，**不能**顺带把 `dialogue_ops: null` 当缺席放过."""
    _p = _plan(hook_spoken=[_LINE], hook_carrier="B1/台词1")
    _before = _baseline(_p)
    _new, _why = o.apply_replan_ops(
        copy.deepcopy(_p),
        {"fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}], "dialogue_ops": None},
        _SRC)
    assert _new is None and "null" in _why, (_new, _why)
    assert _baseline(_p) == _before


def test_c12_a_numeric_narration_field_is_not_coerced():
    """旁白 `text`/`source` 给数字 ⇒ 拒收——**不许** `str()` 成 `"123"` 写进计划.

    判别性：这一条由**两层同源判据**守着——①应用器入口的响应校验（类型错）、
    ②`_norm_narration_entries` 的 `isinstance(str)` 判据（去掉 `str(...)` 转换）。
    判别性实验用 `seam_multi` **同时**撤掉两层才转红（单撤一层仍由另一层挡下）。
    """
    _p = _fixed_plan()
    _before = _baseline(_p)
    for _entry in ({"text": 123, "source": _LINE2, "fact_refs": []},
                   {"text": _LINE2, "source": 456, "fact_refs": []}):
        _new, _why = o.apply_replan_ops(copy.deepcopy(_p),
                                        {"narration_lines": [_entry]}, _SRC)
        assert _new is None and "字符串" in _why, (_entry, _new, _why)
        assert _baseline(_p) == _before


def test_c12_unknown_nested_keys_are_not_dropped():
    """条目里**没定义过的键**不许被静默丢掉（旧实现重建条目时只留 text/source/fact_refs）."""
    _p = _fixed_plan()
    _before = _baseline(_p)
    _cases = [
        {"narration_lines": [{"text": _LINE2, "source": _LINE2, "fact_refs": [],
                              "note": "模型自己加的字段"}]},
        {"beat_ops": [{"op": "replace", "index": 2, "new_text": "节拍：乙｜本镜信息目的：推进"
                       "｜原文依据：“%s”" % _LINE, "new_carrier": "画面", "why": "补充说明"}]},
        {"dialogue_ops": [{"index": 1, "replace_with": _LINE2, "reason": "换这句"}]},
    ]
    for _ops in _cases:
        _new, _why = o.apply_replan_ops(copy.deepcopy(_p), _ops, _SRC)
        assert _new is None and "未定义的键" in _why, (_ops, _new, _why)
        assert _baseline(_p) == _before


def test_c12_wrong_types_and_enums_are_rejected():
    """类型与 `enum` 同样按 schema 核（旧实现只核**顶层**四五个键的类型）."""
    _p = _fixed_plan()
    _cases = [
        ({"narration_lines": [{"text": _LINE2, "source": _LINE2, "fact_refs": [True]}]},
         "非整数"),
        # 注意：`fact_carriers[].index` / `.beat` 与两个 `index` 一样走**安全归一**
        # （`SHAPE_SKIP_PATHS`，见 `test_c12_the_safe_index_forms_are_not_killed_by_the_shape_layer`）
        # ——别在这里拿编号写法当"类型错"的反例。
        ({"beat_ops": [{"op": "split", "index": 2, "parts": "两段"}]}, "不是数组"),
        ({"beat_ops": [{"op": "rewrite", "index": 2, "new_text": "x", "new_carrier": "画面"}]},
         "只能是"),
        ({"hook_spoken": [_LINE, 7]}, "必须是字符串"),
    ]
    for _ops, _frag in _cases:
        _new, _why = o.apply_replan_ops(copy.deepcopy(_p), _ops, _SRC)
        assert _new is None and _frag in _why, (_ops, _frag, _new, _why)


def test_c12_the_real_controller_rejects_a_nonconforming_response(monkeypatch):
    """**复核的实际回放**：桩回一份违反 schema 的响应 ⇒ 控制器记 `illegal_response`、
    基线逐字不变（旧实现得到 `stop_reason=ok`、问题数 0）.

    判别性：撤掉应用器入口的 `replan_ops_response_problems` ⇒ 本用例转红（回到 ok）。
    """
    _p = _plan()                                   # 有承载问题 ⇒ 会进控制器
    _base_txt = _baseline(_p)
    _payload = _wire(narration_lines=[{"text": 123, "source": _LINE2, "fact_refs": []}])
    _svc = _stub(monkeypatch, _payload)
    _res = o.run_plan_revision(_p, _SRC, max_calls=1)
    assert len(_svc.reqs) == 1
    assert _res["stop_reason"] == "illegal_response", _res
    assert _baseline(_res["plan"]) == _base_txt, "拒收时基线必须逐字不变"
    assert _res["problems"], "问题清单不许为空（内容问题另行记录）"


def test_c12_the_prompt_teaches_the_null_rule():
    """提示里的"`null` 规则"由**请求 schema 渲染**（谁必填、谁可省略不手抄）."""
    from step01_script_gen.plan_feasibility import (
        REPLAN_REQUIRED_KEYS, REPLAN_OP_KEYS, replan_response_shape_clause)
    _clause = replan_response_shape_clause()
    assert _clause in o._PLAN_OPS_INSTRUCTION, "提示必须带上响应形态那句"
    for _k in REPLAN_REQUIRED_KEYS:
        assert "`%s`" % _k in _clause, _k
    for _k in REPLAN_OP_KEYS:
        if _k not in REPLAN_REQUIRED_KEYS:
            assert "`%s`" % _k in _clause, _k
    assert "不能给 `null`" in _clause


def test_c12_the_actual_request_teaches_the_response_shape(monkeypatch):
    _p = _plan()
    _svc = _stub(monkeypatch)
    o._replan_by_ops(_p, o.plan_acceptance_problems(_p, _SRC), _SRC)
    from step01_script_gen.plan_feasibility import replan_response_shape_clause
    assert replan_response_shape_clause() in _svc.reqs[0].user_prompt


def test_c12_the_safe_index_forms_are_not_killed_by_the_shape_layer():
    """**豁免单源**：编号写法（`2` / `"2"` / `"B2"`）由应用器的安全归一处理，
    形状层**不许**先按 integer 判死——agent_plan 通道降级成 `json_object` 后模型就是这么回的
    （2026-09-27 复核的现场），判死等于把合法修订记成 `illegal_response`。

    判别性：把 `SHAPE_SKIP_PATHS` 里的编号路径删掉 ⇒ 本用例转红。
    """
    from step01_script_gen.plan_feasibility import SHAPE_SKIP_PATHS
    assert set(SHAPE_SKIP_PATHS) == {"beat_ops.[].index", "dialogue_ops.[].index",
                                     "fact_carriers.[].index", "fact_carriers.[].beat"}
    _p = _fixed_plan()
    _new, _why = o.apply_replan_ops(
        _p, {"beat_ops": [{"op": "replace", "index": "B2",
                           "new_text": "节拍：乙改｜本镜信息目的：推进｜原文依据：“%s”" % _LINE,
                           "new_carrier": "画面"}],
             "fact_carriers": [{"index": "1", "beat": " 1 ", "mode": "台词1"}]}, _SRC)
    assert _new is not None and _why == "", (_new, _why)
    # 反过来：**错轴**仍然拒收（豁免不等于"照收"）
    _bad, _why2 = o.apply_replan_ops(
        _p, {"beat_ops": [{"op": "replace", "index": "D2",
                           "new_text": "节拍：乙改｜本镜信息目的：推进｜原文依据：“%s”" % _LINE,
                           "new_carrier": "画面"}]}, _SRC)
    assert _bad is None and "另一套编号" in _why2, (_bad, _why2)


# ---------------------------------------------------------------------------
# C13 复核 2026-10-02（之三）P2：提示的**槽位规则**必须与应用器行为一致
# 旧提示写"同一编号出现两次 = 冲突"，应用器却允许"改一条 + 在它后面插一条"——
# 模型照提示只会放弃这个合法组合。
# ---------------------------------------------------------------------------
def _slot_plan():
    """B2 是画面节拍、正文自带原文依据；F1 由 B1/台词1 承载."""
    return _fixed_plan()


def test_c13_the_prompt_teaches_the_real_slot_rule():
    """提示里的槽位规则由常量渲染（改规则即改提示），且不再出现旧的反向说法."""
    _txt = o._PLAN_OPS_INSTRUCTION
    assert o.BEAT_OP_SLOT_RULE in _txt, "槽位规则必须由常量渲染进提示"
    assert "同一编号出现两次 = 冲突" not in _txt, "旧的手写规则与应用器相反，必须删掉"


def test_c13_replace_and_insert_after_on_the_same_index_apply():
    """**对照用例**：同一编号各做一次 `replace` 与 `insert_after` 是**合法**组合.

    判别性：把提示改回"同一编号至多一条操作"（旧文案）⇒ 本用例的思想就无法传递；
    把应用器的双槽位判据改成单槽位 ⇒ 转红（合法组合被拒）。
    """
    _new, _why = o.apply_replan_ops(
        _slot_plan(),
        {"fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}],   # 动了节拍编号 ⇒ 逐项重声明
         "beat_ops": [
            {"op": "replace", "index": 2, "new_text": "节拍：乙改｜本镜信息目的：推进"
             "｜原文依据：“%s”｜载体：画面" % _LINE, "new_carrier": "画面"},
            {"op": "insert_after", "index": 2, "new_text": "节拍：乙之后｜本镜信息目的：推进"
             "｜原文依据：“%s”｜载体：画面" % _LINE, "new_carrier": "画面"}]},
        _SRC)
    assert _new is not None and _why == "", (_new, _why)
    assert len(_new["shot_beats"]) == 4, _new["shot_beats"]


def test_c13_two_edits_on_the_same_index_are_rejected():
    """同**槽位**重复（两条改动）仍整轮拒收——这就是提示该教的东西."""
    _new, _why = o.apply_replan_ops(
        _slot_plan(),
        {"beat_ops": [
            {"op": "replace", "index": 2, "new_text": "节拍：乙改｜本镜信息目的：推进"
             "｜原文依据：“%s”｜载体：画面" % _LINE, "new_carrier": "画面"},
            {"op": "split", "index": 2, "parts": [
                {"new_text": "节拍：乙a｜本镜信息目的：推进｜原文依据：“%s”" % _LINE,
                 "new_carrier": "画面"},
                {"new_text": "节拍：乙b｜本镜信息目的：推进｜原文依据：“%s”" % _LINE,
                 "new_carrier": "画面"}]}]},
        _SRC)
    assert _new is None and "冲突" in _why, (_new, _why)


# ---------------------------------------------------------------------------
# C14 复核 2026-10-02（之四）C：**嵌套 `null` 不是"不改"**
# 字段**缺席**才是「不改」；`null` 是模型确实写了一个空值。旧实现的形状校验默认允许
# 对象字段为 `null`：`beat_ops` 的 `op`/`parts`/`new_carrier` 分别给 `null` 都能整批
# 应用、完整验收零问题（其中 `op=null` 被当默认 `replace`）。许可范围现在只有**已约定
# 的可选顶层声明键**（`REPLAN_NULL_OK_KEYS`）+ schema 自己声明 `null` 类型的字段。
# ---------------------------------------------------------------------------
#: B2 的新正文（`replace` 用；载体段由 `new_carrier` 渲染）
_B2_NEW = "节拍：乙改｜本镜信息目的：推进｜原文依据：“%s”" % _LINE

_NESTED_NULL_OPS = [
    ("op", {"beat_ops": [{"op": None, "index": 2, "new_text": _B2_NEW,
                          "new_carrier": "画面"}]}),
    ("new_text", {"beat_ops": [{"op": "replace", "index": 2, "new_text": None,
                                "new_carrier": "画面"}]}),
    ("new_carrier", {"beat_ops": [{"op": "replace", "index": 2, "new_text": _B2_NEW,
                                   "new_carrier": None}]}),
    ("parts", {"beat_ops": [{"op": "split", "index": 2, "parts": None}]}),
    ("parts[].new_text", {"beat_ops": [{"op": "split", "index": 2, "parts": [
        {"new_text": None, "new_carrier": "画面"},
        {"new_text": _B2_NEW, "new_carrier": "画面"}]}]}),
    ("dialogue_ops[].replace_with", {"dialogue_ops": [{"index": 1, "replace_with": None}]}),
    ("narration_lines[].text", {"narration_lines": [{"text": None, "source": _LINE2,
                                                     "fact_refs": []}]}),
    ("narration_lines[].fact_refs", {"narration_lines": [{"text": _LINE2, "source": _LINE2,
                                                          "fact_refs": None}]}),
    ("fact_carriers[].mode", {"fact_carriers": [{"index": 1, "beat": 1, "mode": None}]}),
    ("hook_carrier 对象形态 .beat", {"hook_carrier": {"beat": None, "mode": "台词1"}}),
    ("hook_carrier 对象形态 .mode", {"hook_carrier": {"beat": 1, "mode": None}}),
]


@pytest.mark.parametrize("where,ops", _NESTED_NULL_OPS, ids=[x[0] for x in _NESTED_NULL_OPS])
def test_c14_a_nested_null_is_illegal(where, ops):
    """**嵌套字段**给 `null` ⇒ 整轮 `illegal_response`，基线逐字不变.

    判别性：把 `_shape_problems` 的 `null_ok` 默认值改回 `True`（或顶层调用不再传
    `null_ok=`）⇒ 本组用例转红（`op=null` 会被当默认 `replace` 照常应用）。
    """
    _p = _fixed_plan()
    _before = _baseline(_p)
    _new, _why = o.apply_replan_ops(copy.deepcopy(_p), ops, _SRC)
    assert _new is None and "null" in _why, (where, _new, _why)
    assert "省略这个键" in _why, (where, "诊断必须说清「不改」该怎么写", _why)
    assert _baseline(_p) == _before, (where, "拒收时基线不许被改")


def test_c14_the_optional_declaration_keys_are_the_only_null_holders():
    """**许可范围单源**：`null` 只允许落在可省略的顶层声明键上（= 三态语义的"不改"）.

    判别性：把 `REPLAN_NULL_OK_KEYS` 换成"全部顶层键"或"空元组" ⇒ 本用例转红。
    """
    from step01_script_gen.plan_feasibility import (
        REPLAN_NULL_OK_KEYS, REPLAN_OP_KEYS, REPLAN_REQUIRED_KEYS)
    assert set(REPLAN_NULL_OK_KEYS) == set(REPLAN_OP_KEYS) - set(REPLAN_REQUIRED_KEYS)
    _p = _fixed_plan(hook_spoken=[_LINE], hook_carrier="B1/台词1")
    _before = _baseline(_p)
    # ① 可省略键的 `null` = 不改：整批**没有**要改的东西 ⇒ `no_change`（不是拒收）
    _new, _why = o.apply_replan_ops(copy.deepcopy(_p),
                                    {k: None for k in REPLAN_NULL_OK_KEYS}, _SRC)
    assert _new is None and _why == "no_change", (_new, _why)
    # ② 必填键的 `null` 仍是非法（许可范围是"可省略"这一条，不是"随便哪个顶层键"）
    for _k in REPLAN_REQUIRED_KEYS:
        _bad, _why2 = o.apply_replan_ops(copy.deepcopy(_p), {_k: None}, _SRC)
        assert _bad is None and "null" in _why2 and _why2 != "no_change", (_k, _why2)
    assert _baseline(_p) == _before


def test_c14_the_real_controller_rejects_a_nested_null(monkeypatch):
    """**复核的实际回放**：合法批次 + `op: null`（旧实现整批应用、完整验收零问题、
    `stop_reason=ok`）⇒ 现在记 `illegal_response`，基线逐字不变.
    """
    _p = _plan()                                   # F1 承载错位 ⇒ 会进控制器
    _base_txt = _baseline(_p)
    _payload = _wire(fact_carriers=[{"index": 1, "beat": 1, "mode": "台词1"}],
                     beat_ops=[{"op": None, "index": 2, "new_text": _B2_NEW,
                                "new_carrier": "画面"}])
    _svc = _stub(monkeypatch, _payload)
    _res = o.run_plan_revision(_p, _SRC, max_calls=1)
    assert len(_svc.reqs) == 1
    assert _res["stop_reason"] == "illegal_response", _res
    assert "null" in _res["history"][-1]["detail"], _res["history"]
    assert _baseline(_res["plan"]) == _base_txt, "拒收时基线必须逐字不变"


def test_c14_the_prompt_teaches_the_nested_null_rule():
    """提示与应用器**同一源**：嵌套 `null` 那句由 `REPLAN_NULL_OK_KEYS` 渲染进请求；
    schema 描述里"沿用原载体"也必须写成**空字符串**（"留空"会被读成 `null`）.

    判别性：把嵌套那句从 `replan_response_shape_clause` 删掉 ⇒ 转红；把 schema 里
    `new_carrier` 的描述改回"留空=沿用原载体"⇒ 转红。
    """
    from _text_gen.deepseek_schema import REPLAN_OPS_SCHEMA
    from step01_script_gen.plan_feasibility import (
        REPLAN_NULL_OK_KEYS, replan_response_shape_clause)
    _clause = replan_response_shape_clause()
    assert _clause in o._PLAN_OPS_INSTRUCTION
    assert "嵌套字段" in _clause and "省略那个键" in _clause, _clause
    for _k in REPLAN_NULL_OK_KEYS:
        assert "`%s`" % _k in _clause, _k
    _nc = (REPLAN_OPS_SCHEMA["schema"]["properties"]["beat_ops"]["items"]
           ["properties"]["new_carrier"]["description"])
    assert "空字符串" in _nc and "不要给 null" in _nc, _nc


# ---------------------------------------------------------------------------
# C15 复核 2026-10-02 之四实跑：**`new_text` 的两种写法都要认**（提示 ⟷ 应用器同源）
# schema 把 `new_text` 说成"该节拍的新正文"，模型就照"正文"写——`｜本镜信息目的：…`
# `｜原文依据：“…”` 齐全、没有 `节拍：` 标签。旧判据只认 `节拍：` 段，把这种显然可读
# 的写法记成"节拍正文为空"，整轮修订以 `illegal_response` 收场（两章实跑都停在这一步）。
# ---------------------------------------------------------------------------
_BARE_BODY = ("小酒家内四人围坐，气氛压抑｜本镜信息目的：交代密会"
              "｜原文依据：“%s”" % _LINE)


def test_c15_a_bare_body_beat_op_is_normalized():
    """**只给正文**（无 `节拍：` 标签）的 `replace` 必须被认下来，落计划时补上标签.

    判别性：撤掉 `_render_beat_op` 的开头段归一 ⇒ 转红（回到"节拍正文为空"）。
    """
    _new, _why = o.apply_replan_ops(
        _slot_plan(),                                   # B2 是画面节拍
        {"fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}],
         "beat_ops": [{"op": "replace", "index": 2, "new_text": _BARE_BODY,
                       "new_carrier": "画面"}]}, _SRC)
    assert _new is not None and _why == "", (_new, _why)
    _b2 = [b for b in _new["shot_beats"] if "小酒家内四人围坐" in b]
    assert _b2 and _b2[0].startswith("节拍："), _new["shot_beats"]
    assert "｜原文依据：“%s”" % _LINE in _b2[0], _b2[0]


def test_c15_a_bare_body_split_part_is_normalized():
    """`split` 的每一段同样两种写法都认（裸正文 ⇒ 补标签；缺依据仍拒收）."""
    _new, _why = o.apply_replan_ops(
        _slot_plan(),
        {"fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}],
         "beat_ops": [{"op": "split", "index": 2, "parts": [
             {"new_text": "乙上｜本镜信息目的：推进｜原文依据：“%s”" % _LINE,
              "new_carrier": "画面"},
             {"new_text": "乙下｜本镜信息目的：推进｜原文依据：“%s”" % _LINE,
              "new_carrier": "画面"}]}]}, _SRC)
    assert _new is not None and _why == "", (_new, _why)
    assert len(_new["shot_beats"]) == 4, _new["shot_beats"]
    assert _new["shot_beats"][1].startswith("节拍：乙上"), _new["shot_beats"][1]
    assert _new["shot_beats"][2].startswith("节拍：乙下"), _new["shot_beats"][2]


def test_c15_a_beat_with_no_body_is_still_rejected():
    """**真没正文**（整条只有标签段）仍然拒收——归一只认"开头那段是正文"，不猜."""
    _new, _why = o.apply_replan_ops(
        _slot_plan(),
        {"fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}],
         "beat_ops": [{"op": "replace", "index": 2,
                       "new_text": "｜本镜信息目的：推进｜原文依据：“%s”" % _LINE,
                       "new_carrier": "画面"}]}, _SRC)
    assert _new is None and "节拍正文为空" in _why, (_new, _why)


def test_c15_the_prompt_and_the_schema_teach_both_forms():
    """**提示 ⟷ 行为对照**：说明与 schema 描述都写明"写标签或只给正文都行"，
    且应用器确实两种都收（旧写法只教/只认一种，模型照 schema 写就被拒）."""
    from _text_gen.deepseek_schema import REPLAN_OPS_SCHEMA
    _props = REPLAN_OPS_SCHEMA["schema"]["properties"]["beat_ops"]["items"]["properties"]
    for _spec in (_props["new_text"], _props["parts"]["items"]["properties"]["new_text"]):
        _d = _spec["description"]
        assert "只给正文" in _d and "节拍：" in _d, _d
    _txt = o._PLAN_OPS_INSTRUCTION
    assert "只给正文" in _txt and "落计划时统一补上 `节拍：`" in _txt, _txt[-400:]
