# -*- coding: utf-8 -*-
"""**J 阶段验收矩阵**（复核 2026-10-02 之九 P1）：**按修法能力分流** + 走到成功出口.

背景：前几轮把"拒收/回滚/留档"做可靠了，却没证明**被拒之后系统真的存在一条可执行的
修正路径**。复核独立复现了两条：

  · 空 `dialogue_lines` 走进操作式修订，而 `dialogue_ops` 只能替换已有编号——
    补 D1 ⇒ `index 1 越界（共 0 句）`；回整份 `dialogue_lines` ⇒ 未知键拒收。
    即模型**完全遵照反馈**也完不成这个合法修法。

本矩阵按**最终行为**验收：分流依据由生产者给（`fix`）、表达不了的修法走**受约束的完整
计划修订**、经真实入口补选内容与承载后真的 `executable=True`；同时保住既有操作式路径、
核心内容守卫、非法/超时保留基线。
"""
from __future__ import annotations

import json
import sys
import time
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

#: 规划额度单一来源（2026-11-15 批准 4→6）
_N = o.DEFAULT_REVISION_MAX_CALLS
from test_plan_phase_g_rejection_feedback import (                            # noqa: E402
    _LINE, _SRC, _Svc, _legal_plan,
)


@pytest.fixture()
def ledger(tmp_path):
    cl.set_ledger_path(tmp_path / "step01_calls.jsonl")
    yield
    cl.set_ledger_path(None)


def _run(monkeypatch, payloads, cache=None, **kw):
    """走**真入口**（`generate_outline_result`），模型由本地桩替代."""
    _svc = _Svc(payloads, delay=kw.pop("delay", 0.0))
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
    _r = o.generate_outline_result(_SRC, cache_path=cache, **kw)
    return _r, _svc


def _schemas(svc):
    return [(q.json_schema or {}).get("name") for q in svc.reqs]


def _empty_dialogue_plan():
    """**复核复现的入口形态**：结构合法、但一句台词都没选用."""
    _p = _legal_plan()
    _p["dialogue_lines"] = []
    return _p


# ---------------------------------------------------------------------------
# J01 复现：操作协议**表达不了**「补选台词」（两条实证）
# ---------------------------------------------------------------------------
def test_j01_the_ops_protocol_cannot_add_a_selected_line():
    """补 D1 ⇒ 编号越界；回整份 `dialogue_lines` ⇒ 未知键——两条路都不通.

    这条用例是**分流的依据**（不是缺陷本身）：只要它成立，"空台词表"就不能被送进
    操作式接口，否则模型完全遵照反馈也修不动。
    """
    _p = _empty_dialogue_plan()
    _new, _why = o.apply_replan_ops(
        json.loads(json.dumps(_p)),
        {"dialogue_ops": [{"index": 1, "replace_with": _LINE}]}, _SRC)
    assert _new is None and "越界" in str(_why), _why
    _new2, _why2 = o.apply_replan_ops(
        json.loads(json.dumps(_p)), {"dialogue_lines": [_LINE]}, _SRC)
    assert _new2 is None and "未知操作键" in str(_why2), _why2


# ---------------------------------------------------------------------------
# J02/J03 分流 + **成功出口**：补选内容与承载后真的可执行
# ---------------------------------------------------------------------------
def test_j02_an_inexpressible_fix_is_routed_to_the_constrained_full_plan_revision(
        tmp_path, monkeypatch, ledger):
    """分流依据由**生产者**给（`fix`）：空台词表 ⇒ 修订请求改用**完整计划**契约."""
    _ps = o.plan_acceptance_problems(_empty_dialogue_plan(), _SRC)
    assert [x["code"] for x in _ps if x.get("fix") == "reselect_dialogue"], \
        [x["code"] for x in _ps]
    _r, _svc = _run(monkeypatch, [_empty_dialogue_plan(), _legal_plan()],
                    tmp_path / "c.json")
    assert _schemas(_svc)[:2] == ["chapter_outline", "chapter_outline"], _schemas(_svc)
    _req = _svc.reqs[1].user_prompt
    assert "请回**完整计划**" in _req, "请求必须说明本轮要回完整计划"
    assert "dialogue_lines" in _req and "hook_carrier" in _req, "可改字段要写清（与守卫同源）"
    assert "必须逐字保持不变" in _req, "不可改的字段要写清"


def test_j03_a_legal_full_plan_that_fixes_it_reaches_the_success_exit(
        tmp_path, monkeypatch, ledger):
    """**成功出口**：补选台词 + 重绑承载 ⇒ `executable=True`、缓存写入、无失败.

    这是"反馈闭环能不能走完"的主线证据：复核指出 I06 只证明原因进了请求，没证明
    修正能够成功——这一条补的就是它（经**真实修订入口**，不是直接调用应用器）。
    """
    _cache = tmp_path / "c.json"
    _r, _svc = _run(monkeypatch, [_empty_dialogue_plan(), _legal_plan()], _cache)
    assert _r["executable"] is True, _r["failure"]
    assert _r["failure"] is None, _r["failure"]
    assert _r["acceptance"]["problems"] == [], _r["acceptance"]
    assert _r["cache"]["written"] is True and _cache.exists(), _r["cache"]
    _hist = _r["revision"]["history"]
    assert [x["kind"] for x in _hist] == ["applied"], _hist
    assert int(_r["revision"]["calls"] or 0) == 1, "只花掉一次规划机会就修好了"


def test_j03b_the_revised_plan_is_the_one_that_gets_used(tmp_path, monkeypatch, ledger):
    """下发的是**修订后**那份（补选上台词、承载已重绑），不是旧的那份."""
    _r, _svc = _run(monkeypatch, [_empty_dialogue_plan(), _legal_plan()],
                    tmp_path / "c.json")
    _plan = _r["plan"]
    from step01_script_gen.plan_feasibility import plan_dialogue_lines
    assert plan_dialogue_lines(_plan) == [_LINE], plan_dialogue_lines(_plan)
    assert "台词1" in " ".join(_plan["shot_beats"]), _plan["shot_beats"]


# ---------------------------------------------------------------------------
# J04/J05 反例：核心内容被改 / 非法响应 ⇒ 保留基线、不写缓存
# ---------------------------------------------------------------------------
def test_j04_a_full_plan_that_breaks_the_structure_is_rejected_with_the_field(
        tmp_path, monkeypatch, ledger):
    """完整计划**结构**不合格（事件数非法）⇒ 不采纳，且拒收原因**点名到字段**.

    状态量要具体：只说"结果不合法"就回灌不了下一轮（模型不知道改哪儿）。
    """
    _cache = tmp_path / "c.json"
    _bad = _legal_plan(main_events=["甲", "乙"], climax="乙", causal_chain=["甲→乙"])
    _r, _svc = _run(monkeypatch, [_empty_dialogue_plan()] + [_bad] * _N, _cache)
    assert _r["executable"] is False and _r["failure"]["code"] == "illegal_response", _r["failure"]
    _blob = json.dumps(_r["revision"], ensure_ascii=False)
    # 断言**具体的结构原因**（期望值），而不是"里面提到了 main_events"——后者
    # 在结构诊断被撤掉、只剩一句"净化事务拒收"时也可能命中（复核之十 K4 的复查）
    assert "main_events" in _blob and "3–6 条" in _blob, _blob[:400]
    assert _r["cache"]["written"] is not True and not _cache.exists(), _r["cache"]


def test_j04b_a_structurally_valid_full_plan_that_changes_the_core_content_is_rejected(
        tmp_path, monkeypatch, ledger):
    """**结构合法但核心内容被换**（主要事件换成别的事）⇒ 必须由**核心内容守卫**挡住.

    这条与 J04 分工明确：J04 的响应结构就不合法（结构门先挡），这一条的响应结构完全合法、
    只是把"主要事件"换成了另一批——只有 `_plan_core_preserved` 认得出来。缺了它，
    模型可以借"补选台词"之名把整集的事件与事实换掉，验收还会通过。
    """
    _cache = tmp_path / "c.json"
    _bad = _legal_plan(main_events=["戊", "己", "庚"], climax="庚", causal_chain=["戊→己→庚"])
    assert o.outline_structure_problems(_bad) == [], "前提：这份响应**结构合法**"
    _r, _svc = _run(monkeypatch, [_empty_dialogue_plan()] + [_bad] * 3, _cache)
    assert _r["executable"] is False, "核心内容被换不许采纳"
    assert not _cache.exists() and _r["cache"]["written"] is not True
    _blob = json.dumps(_r["revision"], ensure_ascii=False)
    assert "core_field_changed" in _blob, _blob[:400]
    assert any(_k in _blob for _k in ("main_events", "causal_chain", "climax")), _blob[:400]


def test_j05_a_full_plan_that_is_not_json_is_rejected(tmp_path, monkeypatch, ledger):
    """正文不是 JSON ⇒ 不采纳、不写缓存，拒收原因说明是解析失败."""
    _cache = tmp_path / "c.json"
    _r, _svc = _run(monkeypatch, [_empty_dialogue_plan()] + ["这不是 JSON"] * _N, _cache)
    assert _r["executable"] is False and _r["failure"]["code"] == "illegal_response", _r["failure"]
    assert "JSON" in json.dumps(_r["revision"], ensure_ascii=False)
    assert not _cache.exists() and _r["cache"]["written"] is not True


def test_j05b_a_timeout_keeps_the_baseline_and_writes_no_cache(tmp_path, monkeypatch, ledger):
    """**超时**（过点）：不采纳、不写缓存、终局是时间门（不拿内容结论顶替）."""
    _cache = tmp_path / "c.json"
    _now = time.monotonic()
    _r, _svc = _run(monkeypatch, [_empty_dialogue_plan(), _legal_plan()], _cache,
                    delay=0.15, deadline_monotonic=_now + 60.0,
                    run_deadline_at=_now + 60.0, revision_deadline_sec=0.05)
    assert _r["executable"] is False and _r["cache"]["written"] is not True
    assert not _cache.exists() and _r["cache"]["write_error"] is None
    assert _r["failure"]["code"] == "deadline_exhausted", _r["failure"]


# ---------------------------------------------------------------------------
# J06 合法对照：**既有操作式路径不变**
# ---------------------------------------------------------------------------
def test_j06_an_ops_expressible_problem_still_uses_the_ops_path(
        tmp_path, monkeypatch, ledger):
    """有台词、问题只是"选中了却没人承载"⇒ 仍走操作式，且**真的被采纳**（路径不变）."""
    _cache = tmp_path / "c.json"
    _orphan = _legal_plan(narration_lines=[{"text": _LINE, "source": _LINE,
                                            "fact_refs": []}])
    _ops = {"beat_ops": [{"op": "replace", "index": 2,
                          "new_text": "节拍：乙｜本镜信息目的：推进｜原文依据：“%s”｜载体：旁白1"
                                      % _LINE,
                          "new_carrier": "旁白1"}],
            "fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}]}
    _r, _svc = _run(monkeypatch, [_orphan, _ops], _cache)
    assert _schemas(_svc)[:2] == ["chapter_outline", "replan_ops"], _schemas(_svc)
    assert [x["kind"] for x in _r["revision"]["history"]] == ["applied"], _r["revision"]
    assert _r["executable"] is True and _r["cache"]["written"] is True, _r["failure"]


def test_j07_the_full_plan_rule_and_the_guard_are_one_source():
    """提示里"可以改"的字段与守卫 `_plan_core_preserved` 的放行清单**同源**.

    漂移即红：写两份必然漂移，而漂移的恰好是"什么能动、什么不能动"。
    """
    _rule = o.render_full_plan_rule()          # 与请求**同一份**渲染入口
    for _f in o._REPLAN_FREE_FIELDS:
        assert "`%s`" % _f in _rule, _f
    assert "__FREE__" not in _rule, "占位符必须已替换"
    assert "main_events" in _rule and "required_facts" in _rule and "shot_beats" in _rule
    assert "原文证据" in _rule and "原文依据" in _rule, "不可改的部分要写到'证据/依据'一级"


def test_j08_the_fix_capability_comes_from_the_producer_not_from_the_code_prefix():
    """`fix` 由生产者给：空表 ⇒ `reselect_dialogue`；有台词但不在原文 ⇒ 空（操作式可表达）."""
    _a = {x["code"]: x.get("fix") for x in
          o.plan_acceptance_problems(_empty_dialogue_plan(), _SRC)}
    assert _a.get("plan_no_dialogue_lines") == "reselect_dialogue", _a
    assert _a.get("carrier_fact_line_missing") == "reselect_dialogue", _a
    _b = {x["code"]: x.get("fix") for x in o.plan_acceptance_problems(
        _legal_plan(dialogue_lines=["原文里根本没有的句子。"]), _SRC)}
    assert _b.get("dialogue_line_not_in_novel") == "", _b
    assert _b.get("acceptance_incomplete") == "", _b
    # **正面结论的支点**（复核之十 P1）：总量超限只有内容取舍能解 ⇒ 生产者必须标出来，
    # 否则"未标记"会被当成"操作式可修"（31 句那份计划就是这么被送进只能替换的接口的）
    _c = {x["code"]: x.get("fix") for x in o.plan_acceptance_problems(_wide_plan(), _WIDE_NOVEL)}
    assert _c.get("budget_infeasible") == "reselect_dialogue", _c
    assert _c.get("orphan_uncarried") == "", _c

#: 31 句台词、逐字取自原文、每句 ≥8 字（估算每句 ~3s ⇒ 合计远超 90s 上限）
_WIDE = ["第%02d句：甲板上风起，帆布猎猎作响许久。" % _i for _i in range(1, 32)]
_WIDE_NOVEL = "开头。" + "".join(_WIDE) + "结尾。"


def _wide_plan():
    """**混合问题**：总量超预算（31 句）**同时**大量台词孤儿，且一条事实绑在被删的台词上."""
    _beats = ["节拍：甲板上有风｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % _WIDE[0]]
    # 第二条节拍的**正文**要与它承载的事实有交集（否则即使重绑也会被判"画面与事实无关"）
    _beats += ["节拍：风起帆响｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _WIDE[1]]
    _beats += ["节拍：画面%d｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % (_i, _WIDE[_i])
               for _i in range(2, 6)]
    return {"main_events": ["甲", "乙", "丙"], "climax": "丙", "causal_chain": ["甲→乙→丙"],
            "characters": ["甲", "乙"], "emotional_arc": "平→紧", "key_scenes": ["甲板"],
            "character_motives": ["甲：推进"], "end_hook": "风起帆响", "omittable": [],
            "time_budget": "对白较短，画面为主",
            "required_facts": ["%s｜原文证据：“%s”｜承载：B1/台词1" % (_WIDE[0], _WIDE[0]),
                               "风起帆响｜原文证据：“%s”｜承载：B2/台词2" % _WIDE[1]],
            "hook_spoken": [], "hook_carrier": "", "narration_lines": [],
            "dialogue_lines": list(_WIDE), "shot_beats": _beats}


def _narrowed():
    """把 31 句收窄到 1 句，并把绑在被删台词上的那条事实**重绑**到画面节拍上."""
    _p = json.loads(json.dumps(_wide_plan(), ensure_ascii=False))
    _p["dialogue_lines"] = [_WIDE[0]]
    _p["required_facts"] = [_WIDE[0] + "｜原文证据：“%s”｜承载：B1/台词1" % _WIDE[0],
                            "风起帆响｜原文证据：“%s”｜承载：B2/画面" % _WIDE[1]]
    return _p


def test_j09_a_mixed_budget_and_orphan_plan_goes_to_the_full_plan_revision(
        tmp_path, monkeypatch, ledger):
    """**预算超限 × 台词孤儿**（31 句）⇒ 走完整计划修订，收窄条目后真的可执行.

    复核之十 P1 的复现形态：这类计划 `fix` 曾经全为空、被判成"操作式可修"，而操作协议
    **只能替换、删不掉条目**，总量超限根本修不动。分流判据必须是**正面**结论：
    `budget_infeasible`（总量超限）与"无合适替代"都由生产者标成 `reselect_dialogue`。
    """
    _p = _wide_plan()
    _ps = o.plan_acceptance_problems(_p, _WIDE_NOVEL)
    _codes = {x["code"] for x in _ps}
    assert "budget_infeasible" in _codes and "orphan_uncarried" in _codes, _codes
    assert o._fix_capability(_ps)["expressible"] is False, o._fix_capability(_ps)
    assert "budget_infeasible" in o._fix_capability(_ps)["inexpressible"]
    _cache = tmp_path / "c.json"
    _svc = _Svc([_p, _narrowed()])
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
        _novel = (a[0] if a else None) or k.get("novel_text") or ""
        k.setdefault("verify",
                     lambda view, digest: _vh.legal_record_for(
                         view, _novel, plan_digest=digest))
        return _gor_orig(*a, **k)

    monkeypatch.setattr(o, "generate_outline_result", _gor_with_verify)
    _r = o.generate_outline_result(_WIDE_NOVEL, cache_path=_cache)
    assert _schemas(_svc)[:2] == ["chapter_outline", "chapter_outline"], _schemas(_svc)
    assert _r["executable"] is True, (_r["failure"], _r["acceptance"])
    assert _r["cache"]["written"] is True and _cache.exists()
    _out = _r["plan"]
    assert len(_out["dialogue_lines"]) == 1, _out["dialogue_lines"]     # **真的删掉了条目**
    assert _out["main_events"] == _p["main_events"], "核心内容不许动"
    assert "画面" in " ".join(_out["required_facts"]), "被删台词上的事实必须重绑"

def test_j10_a_response_that_is_not_a_complete_plan_says_so(tmp_path, monkeypatch, ledger):
    """完整计划修订的响应**不是完整计划** ⇒ 拒收原因要**如实说缺什么**（不许说成"改了核心字段"）.

    复核之十实跑暴露：模型对着"回完整计划"的要求回了操作式的三个键
    （`hook_carrier`/`hook_spoken`/`narration_lines`），旧写法把它交给核心守卫，
    拒收原因变成"改动了核心字段「causal_chain」"——模型据此**改不动**（两章各重复三次
    同一个形状），归因也被带到错误的原因上。完整性按**首版 schema 的 `required`**（单源）核。
    """
    _cache = tmp_path / "c.json"
    _partial = {"hook_carrier": "", "hook_spoken": [], "narration_lines": []}
    _r, _svc = _run(monkeypatch, [_empty_dialogue_plan()] + [json.dumps(_partial)] * 3, _cache)
    assert _r["executable"] is False and not _cache.exists(), _r["cache"]
    _blob = json.dumps(_r["revision"], ensure_ascii=False)
    assert "完整计划" in _blob, _blob[:400]
    assert "main_events" in _blob or "causal_chain" in _blob, "要点名缺了哪些顶层字段"
    assert "core_field_changed" not in _blob, "这不是「改了核心字段」，别把原因指错"
    # 证据按**期望形态**记（不按内容猜）：完整计划轮次必须是 full_plan + 缺键清单
    _rounds = [json.loads(y) for y in
               (tmp_path / "step01_replan_rounds.jsonl").read_text(encoding="utf-8").splitlines()
               if y.strip() and '"first_version"' not in y]
    _resp = (_rounds[0]["evidence"] or {}).get("response") or {}
    assert _resp.get("shape") == "full_plan" and _resp.get("expected") == "full_plan", _resp
    assert _resp.get("missing_fields"), _resp
