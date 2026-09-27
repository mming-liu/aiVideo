# -*- coding: utf-8 -*-
"""计划边界：生成前可行性 + 原文核验（第二步 2026-09-25；含当日收口）.

用户口径（原文摘要）：

> **在生成前选内容、算预算；装不下就停在规划层。**
> 时长由代码用现有估算器计算，**不采信模型自报秒数**；对白与可同时发生的画面动作
> **不能简单重复相加**。若必保留内容的**估算下界已超过 90 秒**，报告"本集规划不可行／
> 需拆集"，**不生成完整稿**，也不靠加快语速或删必保留事实过关。

2026-09-25 收口的验收项（审核结论，本文件逐条判别）：

1. 门在**候选扇出前只验算一次**：不可行 → **零次候选生成、零次重试**
   （`PlanInfeasibleError` 刻意**不是** `RuntimeError`，候选循环里"生成失败→重试"
   那条 `except RuntimeError` 接不住它）。
2. **缺计划必须明确停止**（不再是"只有 outline 非空才过门"）。
3. **选中台词真的进提示**：`【选用台词】` 进锚点，初始候选与超限收敛两个完整稿生成
   入口都带锚点（另两个入口是源码级钉住）。
4. **原文核验与预算核算在同一道边界**：非逐字台词从计划里剔除；剔除后为空 →
   不可评估 → 停止（"空台词＋非空节拍"不再放行）。

夹具：`dialogue_lines` 直接取**现有失败稿**里真实的对白，因此 90.62s / 不可行是对真实
数据的判定，不是造出来的。⚠️ 这些用例的 `novel_text` 就是那组台词本身——**只验"判据取
代码估算"这一口径**；原文核验由本文件下方专门的用例（含反例）覆盖。
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

from step01_script_gen import plan_feasibility as pf  # noqa: E402

_FIX = Path(__file__).resolve().parent / "fixtures"


def _failed_draft_lines() -> list:
    """失败稿里**真实**的对白逐句（不带标点差异，直接取 narration_segment）."""
    p = _FIX / "draft_failed_20260925_184302.json"
    assert p.exists(), "夹具缺失：%s（随仓库交付，缺失属检出损坏）" % p
    d = json.loads(p.read_text(encoding="utf-8"))
    return [str(s.get("narration_segment") or "") for s in (d.get("shots") or [])
            if str(s.get("narration_segment") or "").strip()]


_LINES = _failed_draft_lines()
_SOURCE = "\n".join(_LINES)          # 只用于让"原文核验"通过；核验本身另有用例


def _plan(lines=None, beats=None, **extra) -> dict:
    # main_events 3-6 条 / climax / causal_chain 是 `outline._validate_outline` 的硬要求
    # （缺任一项则锚点渲染为空串，提示里就看不到计划了）
    base = {"main_events": ["事件1", "事件2", "事件3"],
            "climax": "船头对峙",
            "required_facts": ["她被杀｜原文证据：一道寒光刺进了心口"],
            "causal_chain": ["起因→行动→结果→悬念"],
            "shot_beats": beats if beats is not None else ["节拍1", "节拍2", "节拍3"],
            "dialogue_lines": _LINES if lines is None else lines,
            "omittable": ["闲笔段落"],
            "time_budget": "对白约30秒；动作约40秒；反应约30秒"}
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# 1) 时长由代码算：模型自报的秒数不参与判定
# ---------------------------------------------------------------------------
def test_duration_is_computed_by_code_not_taken_from_the_plan():
    """计划里 `time_budget` 自称"对白约30秒"，而代码按逐句估算得 90.62s → 判不可行.

    这条同时钉住两件事：①判据取**代码估算**；②真实失败稿的对白在计划路径上
    重现同一个数字（与本仓基线回归 91.03s 的 `duration_sec` 口径同源，逐句估算略低）。
    """
    _plan_ = _plan()
    rep = pf.evaluate_plan(_plan_, novel_text=_SOURCE)
    assert rep["evaluable"] is True, rep
    assert rep["dialogue_sec"] == pytest.approx(90.62, abs=0.05), rep
    assert rep["dialogue_lines"] == len(_LINES)
    assert rep["verdict"] == "infeasible" and rep["feasible"] is False, rep
    assert "超预算上限" in "；".join(rep["reasons"]), rep["reasons"]
    # 自报的"30秒"没有被采信（否则会判可行）
    assert rep["dialogue_sec"] > 60, rep


# ---------------------------------------------------------------------------
# 2) 对白与画面动作不重复相加：判据取 max，不是求和
# ---------------------------------------------------------------------------
def test_lower_bound_takes_max_and_never_the_sum():
    """对白 57s + 节拍下限 60s：判据取 max=60s（可行）；若取和 117s 会误判不可行."""
    line = "她抬头看着他"
    rep = pf.evaluate_plan(
        _plan(lines=[line] * 19, beats=["节拍"] * 20),      # 19×3.0s = 57s
        novel_text="他把伞递过去，" + line + "，雨还在下。")
    assert rep["beats_floor_sec"] == pytest.approx(60.0)     # 20 × MIN_SHOT_DURATION
    assert rep["dialogue_sec"] < rep["beats_floor_sec"]
    assert rep["lower_bound_sec"] == rep["beats_floor_sec"], rep
    assert rep["additive_sum_sec"] == pytest.approx(
        rep["dialogue_sec"] + rep["beats_floor_sec"])
    assert rep["additive_sum_sec"] > rep["lower_bound_sec"], (
        "两者本可同时发生，加法值只作诊断")
    assert rep["verdict"] == "feasible" and rep["feasible"] is True, rep
    assert "不重复相加" in rep["note"], rep["note"]


def test_below_min_is_logged_on_the_pass_path():
    """可行但带读数（下界低于下限）必须**在通过路径上也打出来**——否则没人看得见（用户 2026-09-25）."""
    import logging

    _log = logging.getLogger("step01.plan.test")
    _log.setLevel(logging.INFO)
    _seen: list = []

    class _H(logging.Handler):
        def emit(self, rec):
            _seen.append(rec.getMessage())

    _h = _H()
    _log.addHandler(_h)
    import step01_script_gen.plan_feasibility as _pf
    import unittest.mock as _mock
    with _mock.patch.object(_pf, "_log", lambda: _log):
        _pf.check_plan_before_generation(
            _plan(lines=["嗯"], beats=["节拍1"]), novel_text="他嗯了一声。")
    _log.removeHandler(_h)
    assert any("低于下限" in m and "尚不能据下界判断" in m for m in _seen), _seen


def test_below_min_is_reported_but_not_infeasible():
    """下界低于 60s：报出来，但**不是**「不可行」，也**不能**据此说"内容不足"（用户 2026-09-25）.

    54.74s 是"至少这么多"的必要下界——对白与画面动作可同时发生，成片是否达到 60s
    要等生成稿的实际时长，规划层不据此下结论。
    """
    rep = pf.evaluate_plan(_plan(lines=["嗯"], beats=["节拍1"]), novel_text="他嗯了一声。")
    assert rep["verdict"] == "feasible", rep
    _txt = "；".join(rep["reasons"])
    assert "低于下限" in _txt, rep["reasons"]
    assert "尚不能据下界判断" in _txt, rep["reasons"]
    assert "不足以撑满" not in _txt and "内容不足" not in _txt, (
        "下界不能推出欠时结论：%s" % rep["reasons"])
    assert any("实际" in s for s in rep["suggestions"]), rep["suggestions"]


# ---------------------------------------------------------------------------
# 3) 原文核验：选用的台词必须逐字来自原文
# ---------------------------------------------------------------------------
def test_dialogue_must_be_verbatim_from_the_source():
    """逐字找不到的台词从计划里剔除，预算只按**核验通过**的那一组算."""
    src = "老陈说：这船今日不出港。他又补了一句：货还在舱里。"
    plan = _plan(lines=["这船今日不出港", "我们连夜把货送走"], beats=["节拍1", "节拍2"])
    rep = pf.evaluate_plan(plan, novel_text=src)
    assert rep["dialogue_verified"] == ["这船今日不出港"], rep["dialogue_verified"]
    assert [d["line"] for d in rep["dialogue_rejected"]] == ["我们连夜把货送走"]
    assert rep["dialogue_sec"] == pf.dialogue_seconds(["这船今日不出港"]), rep
    # 剔除是"去幻觉"，不是"为预算删内容"：理由里说明来源，建议里不给"删内容"这条路
    assert any("未在原文中逐字找到" in r for r in rep["reasons"]), rep["reasons"]


def test_all_dialogue_unverified_stops_the_plan():
    """全部台词都找不到 → 不可评估（不是"零对白也算计划可行"）."""
    rep = pf.evaluate_plan(_plan(lines=["杜撰的一句台词", "又一句杜撰"]),
                           novel_text="原文里只有这一段叙述。")
    assert rep["verdict"] == "unevaluable" and rep["evaluable"] is False, rep
    assert "全部" in "；".join(rep["reasons"]), rep["reasons"]
    assert len(rep["dialogue_rejected"]) == 2


def test_empty_dialogue_no_longer_passes_on_beats_alone():
    """收口前实测的洞：空台词＋非空节拍 → `feasible`（下界 3.0s）。现在必须停止."""
    rep = pf.evaluate_plan(_plan(lines=[], beats=["节拍1"]), novel_text="原文若干。")
    assert rep["verdict"] == "unevaluable" and rep["evaluable"] is False, rep
    assert "未选用任何逐字台词" in "；".join(rep["reasons"]), rep["reasons"]


def test_source_verification_folds_ellipsis_like_the_grounding_checker():
    """省略号/标点变体不得误伤：与 `validation._check_grounding` 的折叠顺序一致.

    原文写 `他……走了`，计划摘成 `他…走了`（LLM 常把双省略号折成单省略号）——两处判据
    都必须判"来自原文"，否则会在规划层误删真台词。
    """
    from step01_script_gen.validation import _check_grounding

    src = "他站在船头，半晌没动。他……走了。"
    assert pf.verify_dialogue_lines(["他…走了"], src)[0] == ["他…走了"]
    assert _check_grounding({"narration_segment": "他…走了"}, src) is True
    # 反例：原文没有的句子，两边都不能通过
    assert pf.verify_dialogue_lines(["他走了，再没回来"], src)[0] == []
    assert _check_grounding({"narration_segment": "他走了，再没回来"}, src) is False


def test_source_verification_tolerates_the_speaker_prefix_only():
    """说话人前缀是生成层按规则0补的（与 `_check_grounding` 同一宽容），但台词体必须逐字.

    真实数据（第 1 章 + 失败稿 `narration_segment`）：不容忍前缀 → 12 句**全灭**（门会
    系统性误停）；容忍前缀 → 11 句命中，剩下 1 句正是被改写过的台词（该抓的抓住了）。
    """
    src = "老陈说：这船今日不出港。"
    assert pf.verify_dialogue_lines(["老陈道：这船今日不出港"], src)[0] == ["老陈道：这船今日不出港"]
    # 前缀宽容不等于"有冒号就放行"：台词体仍须逐字
    assert pf.verify_dialogue_lines(["老陈道：我们连夜把货送走"], src)[0] == []


def test_dialogue_lines_that_are_not_a_list_are_treated_as_absent():
    """字段被写成字符串时逐字符迭代会造出几十条假台词 → 一律当"没有台词"（fail-closed）."""
    rep = pf.evaluate_plan(_plan(dialogue_lines="这船今日不出港"),
                           novel_text="老陈说：这船今日不出港。")
    assert rep["verdict"] == "unevaluable", rep
    assert rep["dialogue_verified"] == []


def test_missing_source_text_fails_closed():
    """没带原文 → 无法核验 → 不可评估（原文是事实与台词的依据）."""
    rep = pf.evaluate_plan(_plan())
    assert rep["verdict"] == "unevaluable", rep
    assert "缺少原文" in "；".join(rep["reasons"]), rep["reasons"]


# ---------------------------------------------------------------------------
# 4) 门只判定、不改计划；收窄只剔除未核验台词
# ---------------------------------------------------------------------------
def test_evaluation_never_modifies_the_plan_or_trims_it():
    """`evaluate_plan` 是纯函数：不改计划、不加速语速、不产出"精简稿"."""
    _plan_ = _plan()
    _snapshot = json.dumps(_plan_, ensure_ascii=False, sort_keys=True)
    rep = pf.evaluate_plan(_plan_, novel_text=_SOURCE)
    assert json.dumps(_plan_, ensure_ascii=False, sort_keys=True) == _snapshot, (
        "评估过程改动了计划")
    _txt = "；".join(rep["suggestions"])
    assert "重选" in _txt and "拆集" in _txt, rep["suggestions"]
    assert not any(k in _txt for k in ("加速", "删掉", "删除必保留")), rep["suggestions"]
    assert not hasattr(pf, "trim_plan"), "不得提供「产出精简稿」的接口"


def test_boundary_narrows_the_plan_to_verified_lines_only(tmp_path):
    """边界的改写**只有一处**：把未核验台词从计划里剔除（预算不够时一个字都不动）."""
    plan = _plan(lines=["这船今日不出港", "杜撰的一句"], beats=["节拍1", "节拍2"])
    rep = pf.check_plan_before_generation(
        plan, chapter_dir=tmp_path, novel_text="老陈说：这船今日不出港。")
    assert plan["dialogue_lines"] == ["这船今日不出港"], plan["dialogue_lines"]
    assert rep["verdict"] == "feasible", rep
    # 不可行路径不动计划：整份计划原样留着（只报告）
    _over = _plan()
    with pytest.raises(pf.PlanInfeasibleError):
        pf.check_plan_before_generation(_over, chapter_dir=tmp_path, novel_text=_SOURCE)
    assert _over["dialogue_lines"] == _LINES, "不可行时不得改计划"


# ---------------------------------------------------------------------------
# 5) 主流程级：候选扇出前只验算一次，不可行 = 零候选、零重试
# ---------------------------------------------------------------------------
def test_plan_error_is_not_a_retryable_generation_failure():
    """`PlanInfeasibleError` 刻意不继承 `RuntimeError`——否则候选循环会当生成失败重试."""
    assert not issubclass(pf.PlanInfeasibleError, RuntimeError), (
        "候选生成的重试分支 `except RuntimeError` 会把它按「重试」处理")


def test_infeasible_plan_makes_zero_candidate_attempts(tmp_path, monkeypatch):
    """不可行 → 门在候选扇出前抛出：不建服务、不重试、不写正式产物，只留诊断."""
    import step01_script_gen.main as m

    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    _calls = {"service": 0, "sleep": 0}

    def _boom(*a, **k):
        _calls["service"] += 1
        raise AssertionError("不可行的计划不该走到模型调用")

    def _sleep(*a, **k):
        _calls["sleep"] += 1

    monkeypatch.setattr(m, "create_service", _boom)
    monkeypatch.setattr(time, "sleep", _sleep)
    with pytest.raises(pf.PlanInfeasibleError) as _e:
        m._generate_script_candidates(_SOURCE, None, None, _plan())
    assert "本集规划不可行" in str(_e.value) and "拆集" in str(_e.value), str(_e.value)
    assert _calls == {"service": 0, "sleep": 0}, (
        "不可行时既不该调模型，也不该走候选重试的 sleep：%s" % _calls)
    _diag = tmp_path / "step01_plan_infeasible.json"
    assert _diag.exists(), "必须留下诊断产物（准则 7）"
    _d = json.loads(_diag.read_text(encoding="utf-8"))
    assert _d["verdict"] == "infeasible" and _d["lower_bound_sec"]
    # 正式三件套不受影响（这一步根本到不了提交点）
    assert not (tmp_path / "script_result.json").exists()


def test_missing_plan_stops_before_generation(tmp_path, monkeypatch):
    """大纲提取失败 / OUTLINE_ENABLED=0 → 计划缺失 → 明确停止（不再"没有大纲也照生成"）."""
    import step01_script_gen.main as m

    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    monkeypatch.setattr(m, "create_service", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("缺计划不该走到模型调用")))
    with pytest.raises(pf.PlanInfeasibleError) as _e:
        m._generate_script_candidates(_SOURCE, None, None, None)
    assert "计划缺失" in str(_e.value), str(_e.value)
    _d = json.loads((tmp_path / "step01_plan_infeasible.json").read_text(encoding="utf-8"))
    assert _d["verdict"] == "unevaluable" and _d["dialogue_lines"] == 0


def test_legacy_plan_without_the_new_fields_stops(tmp_path, monkeypatch):
    """老格式计划（无 `dialogue_lines`）→ 不可评估 → 同样停在规划层."""
    import step01_script_gen.main as m

    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    monkeypatch.setattr(m, "create_service", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("不可评估的计划不该走到模型调用")))
    _legacy = {k: v for k, v in _plan().items() if k != "dialogue_lines"}
    assert pf.evaluate_plan(_legacy)["verdict"] == "unevaluable"
    assert "dialogue_lines" in "".join(pf.evaluate_plan(_legacy)["missing"])
    with pytest.raises(pf.PlanInfeasibleError):
        m._generate_script_candidates(_SOURCE, None, None, _legacy)


# ---------------------------------------------------------------------------
# 6) 主流程级：选中台词真的进了生成提示
# ---------------------------------------------------------------------------
class _CaptureService:
    """只截获请求、不发网络调用."""

    def __init__(self, box: dict):
        self._box = box

    def generate(self, req):
        self._box["prompt"] = req.user_prompt
        raise RuntimeError("stop-after-capture")     # 截获即止，不进后续流程


def test_selected_dialogue_reaches_the_initial_generation_prompt(tmp_path, monkeypatch):
    """初始分镜生成入口：契约块必须在**最终提示末尾**，且块内是**全部**选中台词."""
    import step01_script_gen.main as m
    from step01_script_gen.prompts import render_execution_contract

    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    _box: dict = {}
    monkeypatch.setattr(m, "create_service", lambda *a, **k: _CaptureService(_box))
    plan = _plan()
    with pytest.raises(RuntimeError, match="stop-after-capture"):
        m.gen_spy_script(_SOURCE, outline=plan)
    _prompt = _box["prompt"]
    assert _prompt.endswith(render_execution_contract(plan)), "契约块不在提示末尾"
    block = _contract_block(_prompt)
    assert block, "初始生成入口没有带生成执行契约块"
    for line in plan["dialogue_lines"]:
        assert line in block, "契约块缺台词：%s" % line
    assert "【必须保留事实】" in _prompt and "【镜头节拍】" in _prompt


def test_contract_construction_failure_stops_before_any_model_call(tmp_path, monkeypatch):
    """构造失败**不得降级为"无锚点继续生成"**（用户 2026-09-25）.

    否则一份"已过计划门"的稿件会在**没有执行契约**的情况下生成，而报告仍说计划已生效。
    异常在 `create_service()` 之前抛出 ⇒ 不调用模型；且**不是 `RuntimeError`**
    （候选循环的 `except RuntimeError` 是"生成失败→重试"语义，确定性失败不该重试）。
    """
    import step01_script_gen.main as m

    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)

    def _no_model(*a, **k):
        raise AssertionError("构造失败时不得调用模型")

    monkeypatch.setattr(m, "create_service", _no_model)
    monkeypatch.setattr(m, "render_execution_contract",
                        lambda plan: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(pf.PlanInfeasibleError) as _e:
        m.gen_spy_script(_SOURCE, outline=_plan())
    assert "执行契约构造失败" in str(_e.value), str(_e.value)
    assert not isinstance(_e.value, RuntimeError), "不得让候选循环把它当生成失败重试"


def test_selected_dialogue_reaches_the_over_limit_regen_prompt(tmp_path, monkeypatch):
    """超限收敛：契约块在**末尾**（它此前既没有大纲锚点、也没有契约，还在最后拼时长反馈）."""
    import step01_script_gen.main as m

    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    _box: dict = {}
    monkeypatch.setattr(m, "create_service", lambda *a, **k: _CaptureService(_box))
    _draft = json.loads((_FIX / "draft_failed_20260925_184302.json").read_text(encoding="utf-8"))
    _marker = "【选用台词·逐字，本集只拍这些】这船今日不出港"
    _contract_marker = "### 本集生成执行契约（计划已定，只能展开，不得重新选材）\n一、1. 样本台词"
    m._regen_over_limit(_draft, _SOURCE, max_attempts=1, outline_anchor=_marker,
                        contract_block=_contract_marker)
    _prompt = _box["prompt"]
    assert _marker in _prompt, "超限收敛没带大纲锚点（会绕开计划另写一版）"
    assert _prompt.endswith(_contract_marker), "契约块不在末尾（时长反馈之后才是最后一眼）"


def test_selected_dialogue_reaches_the_structural_regen_prompt(tmp_path, monkeypatch):
    """结构收敛：所有反馈（结构点名 + checklist）之后，契约块仍必须在末尾."""
    import step01_script_gen.main as m

    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    _box: dict = {}
    monkeypatch.setattr(m, "create_service",
                        lambda *a, **k: _ScriptedService([RuntimeError("stop")], _box))
    _draft = json.loads((_FIX / "draft_failed_20260925_184302.json").read_text(encoding="utf-8"))
    _contract_marker = "### 本集生成执行契约（计划已定，只能展开，不得重新选材）\n一、1. 样本台词"
    try:
        m._regen_structural_over_limit(_draft, _SOURCE, max_attempts=1,
                                       outline_anchor="【高潮事件】x",
                                       contract_block=_contract_marker)
    except Exception:                      # 桩会中止；prompt 已截获
        pass
    _with = [p for p in _box["prompts"] if _contract_marker in p]
    assert _with, "结构收敛的提示里没有契约块"
    assert _with[-1].endswith(_contract_marker), "契约块不在末尾（后面还拼了反馈）"


def test_all_four_full_draft_entries_carry_the_execution_contract():
    """四个完整稿生成入口的契约块接线，且**追加之后不得再拼任何东西**（用户 2026-09-25）.

    "块在提示里"不等于"块在末尾"：三个重生入口在 builder 之后还要追加反馈
    （gap_context / _structural_feedback+checklist / 时长反馈），所以契约必须由**最终组装处**
    追加。这里的源码钉住"追加即最后一步"，行为级断言见下方三个用例与 GAP 的组装函数用例。
    """
    import inspect
    import step01_script_gen.main as m

    _g = inspect.getsource(m.gen_spy_script)
    assert "render_execution_contract(outline)" in _g
    assert "append_execution_contract(" in _g
    for _fn in (m._regen_over_limit, m._regen_structural_over_limit):
        _src = inspect.getsource(_fn)
        assert 'contract_block: str = ""' in _src, _fn.__name__
        _tail = _src[_src.index("append_execution_contract(prompt, contract_block)"):]
        assert "prompt = prompt +" not in _tail and "prompt +=" not in _tail, (
            "%s：契约追加之后又拼了内容，契约不在末尾" % _fn.__name__)
    _main = inspect.getsource(m.main)
    assert "_contract = render_execution_contract(pre_extracted_outline)" in _main, (
        "契约块要在扇出前算一次，四个入口共用同一份")
    assert "_build_gap_regen_prompt(" in _main, "GAP 重生必须走统一组装（契约在末尾）"
    assert _main.count("contract_block=_contract") == 2, (
        "超限收敛 / 结构收敛两处都要带契约块")
    assert _main.count("_o2a(pre_extracted_outline, novel_text)") == 2, (
        "结构收敛 / 超限收敛两处锚点渲染都要带**核验过**的原文")
    assert "_o2a(outline, novel_text)" in inspect.getsource(m._build_gap_regen_prompt), (
        "GAP 那处的锚点渲染同样要带核验过的原文")


def test_gap_regen_prompt_puts_the_contract_last():
    """GAP 重生（唯一不能靠截获捕获的入口）走 `_build_gap_regen_prompt()`：契约在末尾."""
    import step01_script_gen.main as m

    contract = "### 本集生成执行契约（计划已定，只能展开，不得重新选材）\n一、 样本"
    prompt, _system = m._build_gap_regen_prompt(
        _SOURCE, None, None, _plan(), contract, "\n\n# ★ 重生成铁律（反馈区）\n反馈正文")
    assert prompt.endswith(contract), "契约不在最终提示末尾"
    assert "反馈正文" in prompt and prompt.index("反馈正文") < prompt.index(contract), (
        "反馈必须在契约之前")


def test_every_full_draft_entry_renders_the_anchor_with_the_plan():
    """锚点侧接线（契约块之外的那一半）：超限收敛此前漏了大纲锚点."""
    import inspect
    import step01_script_gen.main as m

    assert 'outline_anchor: str = ""' in inspect.getsource(m._regen_over_limit), (
        "超限收敛必须能收大纲锚点")


# ---------------------------------------------------------------------------
# 8) 规划层重选：**一轮，带代码算出的数字**（用户 2026-09-25 决定 ①）
# ---------------------------------------------------------------------------
def _plan_response(lines, **overrides) -> str:
    """一份合法的大纲响应 JSON（核心字段与 `_plan()` 一致）."""
    base = {"main_events": ["事件1", "事件2", "事件3"], "climax": "船头对峙",
            "required_facts": ["她被杀｜原文证据：一道寒光刺进了心口"],
            "causal_chain": ["起因→行动→结果→悬念"],
            "shot_beats": ["节拍1", "节拍2", "节拍3"],
            "dialogue_lines": list(lines), "omittable": ["闲笔段落"],
            "time_budget": "对白约30秒；动作约40秒；反应约30秒"}
    base.update(overrides)
    return json.dumps(base, ensure_ascii=False)


class _ScriptedService:
    """按脚本依次返回大纲响应（可混入异常）；记录每次请求的 prompt.

    ⚠️ `script`/`box` 都是**共享引用**（不拷贝）：每个 LLM 请求都会 `create_service()`
    新建一个服务对象，而脚本序列要跨这些对象继续推进——拷贝的话每次都会从头开始。
    """

    def __init__(self, script: list, box: dict):
        self._script, self._box = script, box

    def generate(self, req):
        self._box.setdefault("prompts", []).append(req.user_prompt)
        item = self._script.pop(0) if self._script else None
        if isinstance(item, Exception):
            raise item

        class _R:
            success = True
            error = ""
            text = item or ""

        return _R()


def _patch_outline_service(monkeypatch, script: list) -> dict:
    import step01_script_gen.outline as o

    box: dict = {}
    monkeypatch.setattr(o, "create_service", lambda *a, **k: _ScriptedService(script, box))
    return box


def test_replan_feedback_carries_code_computed_numbers(monkeypatch):
    """重选的提示里必须有**代码算出**的数字与规划目标——这就是"有数值反馈"这条验收."""
    import step01_script_gen.outline as o

    plan = _plan()                       # 12 句真实对白 → 90.62s > 90s
    box = _patch_outline_service(monkeypatch, [_plan_response(_LINES[:2])])
    new_plan, info = o.enforce_budget_by_replanning(
        plan, _SOURCE, model=None, base_prompt="BASE-PROMPT", system_prompt="SYS")
    _prompt = box["prompts"][0]
    assert _prompt.startswith("BASE-PROMPT"), "重选必须复用同一份大纲提示"
    assert "90.62" in _prompt, "没有把代码算出的对白估时反馈回去"
    assert "60-90" in _prompt and "70-80" in _prompt, "没有把预算区间/规划目标交出去"
    assert '"dialogue_lines"' in _prompt, "没有把上一版计划带回去"
    assert info["rounds"] == 1 and info["before_dialogue_sec"] == pytest.approx(90.62, abs=0.05)
    assert info["result"] == "feasible" and info["after_lines"] == 2, info
    assert new_plan["dialogue_lines"] == _LINES[:2]


def test_replan_is_rejected_when_core_fields_change(monkeypatch):
    """必保留事实 / 事件顺序被改动 → **不接受**（宁可用首版计划去报告需拆集）."""
    import step01_script_gen.outline as o

    plan = _plan()
    _tampered = json.loads(_plan_response(_LINES[:2]))
    _tampered["required_facts"] = ["她被杀｜原文证据：编造的"]
    _tampered["main_events"] = ["换个顺序的事件", "事件2", "事件1"]
    _patch_outline_service(monkeypatch, [json.dumps(_tampered, ensure_ascii=False)])
    back, info = o.enforce_budget_by_replanning(plan, _SOURCE)
    assert back is plan, "核心字段被改时不得采纳新计划"
    assert info["result"] == "replan_failed", info


def test_replan_runs_exactly_one_round(monkeypatch):
    """重选后仍超限 → **不再试第二次**（用户要的是"一次"，不是新的修复轮）."""
    import step01_script_gen.outline as o

    plan = _plan()
    box = _patch_outline_service(monkeypatch, [_plan_response(_LINES)])
    new_plan, info = o.enforce_budget_by_replanning(plan, _SOURCE)
    assert len(box["prompts"]) == 1, "只允许一轮重选"
    assert info["rounds"] == 1 and info["result"] == "infeasible", info
    # 返回**重选后**的计划（数字最新），"需拆集"由生成前的门按它报告
    assert new_plan["dialogue_lines"] == _LINES


def test_replan_failure_keeps_the_first_plan(monkeypatch):
    """重选调用失败 → 原样保留首版计划（不静默降级、不换一份来源不明的计划）."""
    import step01_script_gen.outline as o

    plan = _plan()
    _patch_outline_service(monkeypatch, [RuntimeError("boom")])
    back, info = o.enforce_budget_by_replanning(plan, _SOURCE)
    assert back is plan and info["result"] == "replan_failed", info
    # 原因要**分别记录**（用户 2026-09-25）：这不是"本集不可行"的判定
    assert info["detail"].startswith("call_failed"), info


def test_replan_call_failure_is_not_reported_as_teardown(monkeypatch, tmp_path):
    """重选**没跑成** ≠ 本集不可行：异常**开头**与诊断产物都不得作拆集结论（用户 2026-09-25）."""
    import step01_script_gen.outline as o

    plan = _plan()
    _patch_outline_service(monkeypatch, [RuntimeError("network down")])
    _back, info = o.enforce_budget_by_replanning(plan, _SOURCE)
    plan["plan_replan"] = info
    with pytest.raises(pf.PlanInfeasibleError) as _e:
        pf.check_plan_before_generation(plan, chapter_dir=tmp_path, novel_text=_SOURCE)
    _msg = str(_e.value)
    _head = _msg.split("：", 1)[0]
    assert "重选未完成" in _head, _head
    assert "需拆集" not in _head and "本集规划不可行" not in _head, (
        "调用失败不得在开头写成内容结论：%s" % _head)
    assert "⇒ 不生成完整稿" in _msg, _msg
    assert "STEP01_FORCE_REPLAN=1" in _msg, "必须给出明确的重新规划入口"
    # 首版超预算的**数字事实**照旧保留
    assert "超预算上限" in _msg and "90.62" in _msg, _msg
    _d = json.loads((tmp_path / "step01_plan_infeasible.json").read_text(encoding="utf-8"))
    assert _d["verdict"] == "infeasible", "计划本身的读数（超预算）要保留"
    assert _d["conclusion"] == "replan_incomplete", _d["conclusion"]
    assert _d["replan"]["result"] == "replan_failed", _d["replan"]
    assert not any("拆集" in s for s in _d["suggestions"]), _d["suggestions"]


def test_replan_still_over_budget_is_reported_as_teardown(monkeypatch, tmp_path):
    """只有**真的重选过一轮仍超预算**，才是"需拆集"的结论（异常开头与诊断都要这么写）."""
    import step01_script_gen.outline as o

    plan = _plan()
    _patch_outline_service(monkeypatch, [_plan_response(_LINES)])   # 重选后还是 12 句
    _back, info = o.enforce_budget_by_replanning(plan, _SOURCE)
    assert info["result"] == "infeasible", info
    plan["plan_replan"] = info
    with pytest.raises(pf.PlanInfeasibleError) as _e:
        pf.check_plan_before_generation(plan, chapter_dir=tmp_path, novel_text=_SOURCE)
    _msg = str(_e.value)
    assert _msg.split("：", 1)[0] == "本集规划不可行／需拆集", _msg
    # v6.136：文案从"仍超预算"改为"仍不可行"——重选失败含超预算与台词不可表示两类
    assert "已重选 1 轮后仍不可行" in _msg, _msg
    _d = json.loads((tmp_path / "step01_plan_infeasible.json").read_text(encoding="utf-8"))
    assert _d["conclusion"] == "teardown", _d["conclusion"]


def test_force_replan_env_is_the_explicit_entry(monkeypatch):
    """`STEP01_FORCE_REPLAN=1` = 明确的重新规划入口：忽略已记录的结论重跑一轮（不自动重试）."""
    import step01_script_gen.outline as o

    plan = _plan()
    plan["plan_replan"] = {"rounds": 1, "result": "replan_failed", "detail": "call_failed: x"}
    # 默认：沿用已记录的结论，不调模型
    box = _patch_outline_service(monkeypatch, [_plan_response(_LINES[:2])])
    monkeypatch.delenv("STEP01_FORCE_REPLAN", raising=False)
    _back, info = o.enforce_budget_by_replanning(plan, _SOURCE)
    assert box.get("prompts", []) == [] and info.get("skipped") == "already_attempted", info
    # 显式入口：真重跑一轮
    box2 = _patch_outline_service(monkeypatch, [_plan_response(_LINES[:2])])
    monkeypatch.setenv("STEP01_FORCE_REPLAN", "1")
    _back2, info2 = o.enforce_budget_by_replanning(plan, _SOURCE)
    assert len(box2["prompts"]) == 1, "FORCE=1 必须真的重跑这一轮"
    assert info2["result"] == "feasible", info2


def test_cached_plan_is_still_checked_and_replan_is_written_back(tmp_path, monkeypatch):
    """缓存命中的计划也要过预算关；重选结果回写缓存，下次不再烧模型."""
    import step01_script_gen.outline as o

    _cache = tmp_path / "chapter_outline.cache.json"
    # 第一轮：首版超预算 → 重选 → 回写缓存
    _patch_outline_service(monkeypatch, [_plan_response(_LINES), _plan_response(_LINES[:2])])
    p1 = o.generate_outline(_SOURCE, cache_path=_cache)
    assert p1["dialogue_lines"] == _LINES[:2] and p1["plan_replan"]["result"] == "feasible"
    assert _cache.exists()
    # 第二轮：命中缓存（不再调模型）→ 拿到的就是重选后的计划
    box = _patch_outline_service(monkeypatch, [])
    p2 = o.generate_outline(_SOURCE, cache_path=_cache)
    assert p2["dialogue_lines"] == _LINES[:2]
    assert box.get("prompts", []) == [], "缓存命中且计划可行时不该再调模型"


def test_cached_infeasible_plan_is_not_replanned_again(tmp_path, monkeypatch):
    """用户 2026-09-25 定的口径："只重选一次"= **同一计划版本一次**（不是每次调用一次）.

    缓存里那份"重选后仍超限"的计划下一轮不得再烧一次模型——记录已尝试的结果，直接沿用结论。
    """
    import step01_script_gen.outline as o

    _cache = tmp_path / "chapter_outline.cache.json"
    box1 = _patch_outline_service(monkeypatch, [_plan_response(_LINES), _plan_response(_LINES)])
    p1 = o.generate_outline(_SOURCE, cache_path=_cache)
    assert len(box1["prompts"]) == 2, "首轮：首版 + 一次重选"
    assert p1["plan_replan"]["result"] == "infeasible"
    # 第二轮命中缓存 → **不再重选**（0 次模型调用），并把"已尝试过"如实标出来
    box2 = _patch_outline_service(monkeypatch, [])
    p2 = o.generate_outline(_SOURCE, cache_path=_cache)
    assert box2.get("prompts", []) == [], "同一计划版本不得重复重选"
    assert p2["plan_replan"]["result"] == "infeasible"
    assert p2["plan_replan"].get("skipped") == "already_attempted", p2["plan_replan"]


# ---------------------------------------------------------------------------
# 9) 生成执行契约（不压缩块）：**每个**剖面都要完整传递本集选材
# ---------------------------------------------------------------------------
def _contract_block(prompt: str) -> str:
    """从提示里切出**契约块整块**.

    用户 2026-09-25 明确要求这样验收：**不能**在整份 prompt 里搜台词——小说原文本身
    也含那些句子，搜到不能证明契约真的传到了。
    """
    from step01_script_gen.prompts import CONTRACT_BLOCK_HEADER

    i = prompt.find(CONTRACT_BLOCK_HEADER)
    if i < 0:
        return ""
    rest = prompt[i + len(CONTRACT_BLOCK_HEADER):]
    j = rest.find("\n### ")
    return rest[:j] if j >= 0 else rest


@pytest.mark.parametrize("model", [None, "deepseek-v4-flash", "doubao-seed-2-1-turbo"])
def test_every_prompt_profile_carries_the_full_execution_contract(model):
    """200 字摘要（doubao/agent_plan）只能装摘要，选材由契约块完整传递.

    用户 2026-09-25：不能只凭当前模型的测试宣称"所有生成入口都受计划约束"；
    且验收必须检查**契约块内的完整集合**（7 句全在、必保留事实在、事件顺序在）。
    """
    from step01_script_gen.outline import outline_to_anchor
    from step01_script_gen.prompts import (append_execution_contract,
                                           render_execution_contract,
                                           resolve_model_prompt_profile)

    plan = _plan()
    contract = render_execution_contract(plan)
    builder, _system = resolve_model_prompt_profile(model)
    prompt = append_execution_contract(
        builder("原文" * 60, None, None, outline_to_anchor(plan, _SOURCE)), contract)
    assert prompt.endswith(contract), "契约块必须在提示**最末尾**（%r）" % model
    block = _contract_block(prompt)
    assert block, "剖面 %r 的提示里没有生成执行契约块" % model
    for line in _LINES:                       # 7 句**逐句**都在块内（不得只列前 N 句）
        assert line in block, "契约块缺台词（%r）：%s" % (model, line)
    assert "%d. " % len(_LINES) in block, "最后一句没有编号——说明被裁掉了"
    for fact in plan["required_facts"]:
        assert fact in block, "契约块缺必保留事实（%r）" % model
    for event in plan["main_events"]:
        assert event in block, "契约块缺事件顺序（%r）" % model


def test_contract_block_is_absent_when_the_plan_has_nothing_to_bind():
    """计划没有可传递的选材时不注入空块（不制造"本集没有台词"的误导）."""
    from step01_script_gen.prompts import render_execution_contract

    assert render_execution_contract({}) == ""
    assert render_execution_contract({"dialogue_lines": [], "required_facts": [],
                                      "main_events": []}) == ""


def test_compressed_summary_keeps_climax_and_packs_what_fits():
    """200 字摘要的职责：必保【高潮事件】、超长行不得挤掉后面的短行.

    真实数据（第 1 章，改动前实测）：压缩锚点只剩 **26 字**（仅【高潮事件】）——
    【本章主要事件】一行就 178 字，旧的"遇第一行超预算即 break"让它后面全部消失。
    本集选材不靠这一块（见契约块用例），所以这里不再要求台词进摘要。
    """
    from step01_script_gen.outline import outline_to_anchor
    from step01_script_gen.prompts import _compact_doubao_outline_anchor as C

    out = C(outline_to_anchor(_plan(), _SOURCE))
    assert len(out) <= 200, len(out)
    assert "【高潮事件】" in out, "必保行丢了"
    # 超长首行之后的短行必须活下来
    plan = _plan()
    plan["main_events"] = ["很长的一条主要事件" * 30, "事件2", "事件3"]   # 单行 > 200
    plan["emotional_arc"] = "平静→惊变"
    plan["end_hook"] = "夜色中马车远去。"
    out2 = C(outline_to_anchor(plan, _SOURCE))
    assert len(out2) <= 200, len(out2)
    assert "【本章主要事件】" not in out2, "超长行不该被截断塞进来"
    assert "【情绪弧线】" in out2 and "【结尾钩子】" in out2, out2
    assert "【高潮事件】" in out2
    # 装得下就一个字都不动
    assert C("【高潮事件】船头对峙") == "【高潮事件】船头对峙"
    assert C("【情绪弧线】平静→惊变") == "【情绪弧线】平静→惊变"


# ---------------------------------------------------------------------------
# 10) 规划契约：提示里必须写明判定用的预算数字（否则规划器无从"先算再选"）
# ---------------------------------------------------------------------------
def test_outline_prompt_states_the_budget_it_is_judged_by():
    from step01_script_gen.outline import OUTLINE_PROMPT, _TARGET_MAX_SEC, _TARGET_MIN_SEC
    from step01_script_gen.validation import _REDGUO_DURATION_MAX, _REDGUO_DURATION_MIN

    assert "合法 %d-%d 秒" % (_REDGUO_DURATION_MIN, _REDGUO_DURATION_MAX) in OUTLINE_PROMPT
    assert "规划目标 %d-%d 秒" % (_TARGET_MIN_SEC, _TARGET_MAX_SEC) in OUTLINE_PROMPT
    assert "留出时间" in OUTLINE_PROMPT


# ---------------------------------------------------------------------------
# 7) 计划契约：schema 必须要求这两个新字段；估算器唯一事实源
# ---------------------------------------------------------------------------
def test_outline_schema_requires_the_plan_fields_the_gate_needs():
    from _text_gen.deepseek_schema import OUTLINE_SCHEMA
    props = OUTLINE_SCHEMA["schema"]["properties"]
    req = OUTLINE_SCHEMA["schema"]["required"]
    for f in ("dialogue_lines", "omittable"):
        assert f in props and f in req, f
    assert OUTLINE_SCHEMA["schema"]["additionalProperties"] is False
    # 估算器是唯一事实源：判据必须走 `timecode`
    from step01_script_gen import timecode
    assert pf._estimate_duration is timecode._estimate_duration
    assert pf.MIN_SHOT_DURATION == timecode.MIN_SHOT_DURATION


# ---------------------------------------------------------------------------
# 台词完整性硬门（2026-09-26，工作包 sunny-kindling-wilkinson）
#
# 背景：09-26 02:30 实跑 BLOCK——选材台词被拆镜截断（shot12 "…急需" 句中碎片），
# 审计报 truncated_text，唯一正确修复字段是 narration_segment（锁定）→
# locked_changed 死锁。本门把完整性前移：破损稿不进 BEST-GUARD／提交门。
# ---------------------------------------------------------------------------
def _mk_script(*narrations):
    """最小稿：narrations 逐镜给旁白；其余字段按最小合法形状."""
    return {"shots": [{"index": i + 1, "narration_segment": n,
                       "ff_desc": "f", "lf_desc": "l", "prompt": "p"}
                      for i, n in enumerate(narrations)]}


def test_dialogue_integrity_single_shot_hit_with_prefix_tolerance():
    """命中①：台词逐字在单镜旁白里；说话人前缀宽容（计划无前缀/稿有前缀都算）."""
    from step01_script_gen.plan_feasibility import check_script_dialogue_integrity

    plan = {"dialogue_lines": ["张十三道：住嘴！"], "shot_beats": ["b"]}
    # 稿件带前缀、计划也带 → 全形态命中
    ok, broken = check_script_dialogue_integrity(
        _mk_script("张十三道：住嘴！"), plan)
    assert ok and broken == []
    # 稿件丢前缀 → 台词体形态命中（宽容与 verify_dialogue_lines 同源）
    ok2, broken2 = check_script_dialogue_integrity(_mk_script("住嘴！"), plan)
    assert ok2 and broken2 == []


def test_dialogue_integrity_compliance_restore_hit():
    """合规替换还原后命中：计划/原文侧"杀人"、稿件侧被 sanitize 成"出人命"."""
    from step01_script_gen.plan_feasibility import check_script_dialogue_integrity

    plan = {"dialogue_lines": ["听香道：公……公子他……杀……杀人啦……"], "shot_beats": ["b"]}
    ok, broken = check_script_dialogue_integrity(
        _mk_script("听香道：公…公子他…杀…出人命啦…"), plan)
    assert ok, broken
    assert broken == []


def test_dialogue_integrity_clean_cross_shot_split_hits():
    """命中②：跨镜整段拼接——每个参审镜头都以句末收尾（干净拆分）.

    只有多句长台词按**句界**拆分才可能干净（单句台词任何跨镜拆分都必然留下
    逗号/句中结尾的碎片 → 判破损，见下方反例）——这正是审计 truncated_text 的
    触发形态与提交门死锁的预测口径。真实计划里的张十三长台词（4 句）是典型。
    """
    from step01_script_gen.plan_feasibility import check_script_dialogue_integrity

    plan = {"dialogue_lines": [
        "且慢，我刚刚收到城里传来的消息。有些生意上的事情急需公子爷赶回去处理。"],
        "shot_beats": ["b"]}
    ok, broken = check_script_dialogue_integrity(_mk_script(
        "张十三道：且慢，我刚刚收到城里传来的消息。",
        "有些生意上的事情急需公子爷赶回去处理。",
        "别的内容。"), plan)
    assert ok, broken


def test_dialogue_integrity_fragment_split_is_broken():
    """反例①（09-26 实损形态）：句中截断 + 下一镜补前缀——拼接救不回，判破损.

    纯整稿拼接会把台词拼回来（实测 02:30 快照 0/7 破损＝放行死锁稿），所以
    跨镜命中必须要求每个参审镜头句末完整。shot12 "…急需" 无句末标点 ⇒ 破损。

    v6.138（九审 P2-4）：本夹具的 shot2/shot3 还各自带**计划外**的后续对白
    （"公子如今酩酊大醉…"、"去把公子的马车…"不在计划句里）——排他性检查把它们
    各记一条 `extra_dialogue`；台词门现在同时拦「计划句破损」与「自选对白」两类。
    """
    from step01_script_gen.plan_feasibility import check_script_dialogue_integrity

    plan = {"dialogue_lines": [
        "且慢，我刚刚收到城里传来的消息，有些生意上的事情急需公子爷赶回去处理。"],
        "shot_beats": ["b"]}
    ok, broken = check_script_dialogue_integrity(_mk_script(
        "张十三道：且慢，我刚刚收到城里传来的消息，有些生意上的事情急需",
        "张十三道：公子爷赶回去处理，公子如今酩酊大醉，难以起身，你们来的正好",
        "张十三道：去把公子的马车赶到水边来，我和听香姑娘要扶公子马上回城"), plan)
    assert not ok
    _by_reason: dict = {}
    for b in broken:
        _by_reason.setdefault(b["reason"], []).append(b)
    assert len(_by_reason.get("cross_shot_split", [])) == 1
    assert "且慢" in _by_reason["cross_shot_split"][0]["line"]
    assert len(_by_reason.get("extra_dialogue", [])) == 2, broken


def test_dialogue_integrity_exclusive_dialogue_set():
    """v6.138（九审 P2-4）：排他性——5/5 命中 + 1 句自选对白必须判破损.

    - 自选句带说话人前缀、正文与任何计划句无包含关系 → `extra_dialogue`；
    - 计划句的**跨镜碎片**（正文是某计划句的子串）不算自选；
    - 无说话人前缀的旁白（描述/画外音）不按对白拦截（审查明确要求）。
    """
    from step01_script_gen.plan_feasibility import check_script_dialogue_integrity

    plan = {"dialogue_lines": ["张十三道：住嘴！", "听香道：公……公子他……杀……杀人啦……"],
            "shot_beats": ["b"]}
    # 全中 + 1 句自选
    ok, broken = check_script_dialogue_integrity(_mk_script(
        "听香道：公……公子他……杀……杀人啦……",
        "张十三道：住嘴！",
        "张十三道：闭嘴，公子是何等人物。"), plan)
    assert not ok, "自选对白必须拦下"
    assert len(broken) == 1 and broken[0]["reason"] == "extra_dialogue", broken
    assert "闭嘴" in broken[0]["line"]

    # 计划句碎片 + 无前缀旁白 → 不误报
    ok2, broken2 = check_script_dialogue_integrity(_mk_script(
        "听香道：公……公子他……杀……",
        "杀人啦……",
        "张十三道：住嘴！",
        "湖面恢复平静。"), plan)
    assert ok2, broken2


def test_dialogue_integrity_accidental_boundary_join_is_broken():
    """反例②：相邻镜头偶然跨界拼接——起点未对齐（首镜只含台词尾部片段）判破损."""
    from step01_script_gen.plan_feasibility import check_script_dialogue_integrity

    plan = {"dialogue_lines": ["他说要杀公子。"], "shot_beats": ["b"]}
    # 首镜只含台词的**尾部片段**（"他说"），且不以句末收尾——偶然拼接不得命中
    ok, broken = check_script_dialogue_integrity(
        _mk_script("他说", "要杀公子。"), plan)
    assert not ok
    assert broken[0]["reason"] in ("cross_shot_split", "not_found")


def test_dialogue_integrity_missing_line_is_broken():
    """台词整句在稿件里找不到（含拼接）→ not_found."""
    from step01_script_gen.plan_feasibility import check_script_dialogue_integrity

    plan = {"dialogue_lines": ["张十三道：这句台词稿子里根本没有。"], "shot_beats": ["b"]}
    ok, broken = check_script_dialogue_integrity(
        _mk_script("张十三道：别的台词。", "听香道：另一句。"), plan)
    assert not ok
    # v6.138 排他性：两句旁白都是带前缀的计划外对白 → 各记 extra_dialogue；
    # 计划句找不到仍是 not_found。
    _reasons = sorted(b["reason"] for b in broken)
    assert _reasons == ["extra_dialogue", "extra_dialogue", "not_found"]
    assert any(b["reason"] == "not_found" and "根本没有" in b["line"] for b in broken)


def test_dialogue_integrity_empty_dialogue_lines_fail_closed():
    """`dialogue_lines` 为空 → 不可评估 → fail-closed 判不合格（同 evaluate_plan 口径）."""
    from step01_script_gen.plan_feasibility import check_script_dialogue_integrity

    ok, broken = check_script_dialogue_integrity(_mk_script("张十三道：住嘴！"), {})
    assert not ok
    assert broken and "fail-closed" in broken[0]["reason"]


def test_dialogue_integrity_hook_narration_not_counted():
    """hook 按设计不含选用台词——其旁白不参与命中，台词落在 hook 里仍判破损."""
    from step01_script_gen.plan_feasibility import check_script_dialogue_integrity

    plan = {"dialogue_lines": ["张十三道：住嘴！"], "shot_beats": ["b"]}
    script = _mk_script("别的画面。")
    script["hook"] = {"index": 0, "narration_segment": "张十三道：住嘴！"}
    ok, broken = check_script_dialogue_integrity(script, plan)
    assert not ok
    assert broken and broken[0]["reason"] == "not_found"


def test_dialogue_integrity_rejects_broken_fixtures():
    """冻结失败稿：184302 夹具的破损形态被本门判破损；跨镜拆散记 cross_shot_split.

    该夹具是 09-25 18:43 的 pre-BEST-GUARD 新稿——张十三长台词被拆到 shot17/18/19，
    shot17 句中截断（"…急需"）+ shot18 重复前缀，与 09-26 02:30 实损同形。
    台词取自本章真实计划（chapter_outline.cache.json 的 7 句中与本夹具相关的句子）。
    """
    import json
    from pathlib import Path
    from step01_script_gen.plan_feasibility import check_script_dialogue_integrity

    _fix = Path(__file__).resolve().parent / "fixtures"
    script = json.loads((_fix / "draft_failed_20260925_184302.json").read_text(encoding="utf-8"))
    plan = {"dialogue_lines": [
        "且慢，我刚刚收到城里传来的消息，有些生意上的事情急需公子爷赶回去处理，"
        "公子如今酩酊大醉，难以起身，你们来的正好，去把公子的马车赶到水边来，"
        "我和听香姑娘要扶公子马上回城。"], "shot_beats": ["b"]}
    ok, broken = check_script_dialogue_integrity(script, plan)
    assert not ok
    _by_reason: dict = {}
    for b in broken:
        _by_reason.setdefault(b["reason"], []).append(b)
    assert len(_by_reason.get("cross_shot_split", [])) == 1
    assert "且慢" in _by_reason["cross_shot_split"][0]["line"]
    # v6.138 排他性：该夹具的旁白还带大量**计划外**对白（fixture 的 plan 只列了
    # 与本夹具相关的一句）——这些各自记 extra_dialogue，不再被"计划句都在"放过。
    assert len(_by_reason.get("extra_dialogue", [])) >= 1


def test_dialogue_integrity_restore_both_forms_edges():
    """二审 M1：还原的**双形态**边界——只保留"还原后"会误伤两类真实形态.

    ① 原文/计划本身就用安全词（"自尽"），稿件逐字引用——还原分支匹配不上，
       未还原分支必须命中；
    ② 单字 safe（"春药"→"药"）："抓药去了"会被还原成"抓春药去了"
       （`_check_grounding` 08-17 起的潜伏缺陷，整句门下是硬失败）——未还原分支命中；
    ③ 双词并存（"杀人"与"出人命"同稿）——守卫不还原，直接命中。
    """
    from step01_script_gen.plan_feasibility import check_script_dialogue_integrity

    ok, broken = check_script_dialogue_integrity(
        _mk_script("张十三道：他自尽了。"), {"dialogue_lines": ["他自尽了。"], "shot_beats": ["b"]})
    assert ok, broken

    ok2, broken2 = check_script_dialogue_integrity(
        _mk_script("张十三道：他到镇上抓药去了。"),
        {"dialogue_lines": ["他到镇上抓药去了。"], "shot_beats": ["b"]})
    assert ok2, broken2

    ok3, broken3 = check_script_dialogue_integrity(
        _mk_script("听香道：杀人了…不，是出人命了"),
        {"dialogue_lines": ["杀人了。"], "shot_beats": ["b"]})
    assert ok3, broken3


def test_dialogue_integrity_comma_ending_split_is_broken():
    """二审 M2 固化边界：逗号/顿号结尾的分句跨镜 = 破损（有意从严）.

    句中碎片正是审计 `truncated_text` 的触发形态——不做"逗号也算句末"的宽容，
    宁可显式失败。长台词要干净跨镜只能按句号/问号/叹号等句末拆。
    """
    from step01_script_gen.plan_feasibility import check_script_dialogue_integrity

    plan = {"dialogue_lines": [
        "且慢，我刚刚收到城里传来的消息，有些生意上的事情急需公子爷赶回去处理。"],
        "shot_beats": ["b"]}
    ok, broken = check_script_dialogue_integrity(_mk_script(
        "张十三道：且慢，我刚刚收到城里传来的消息，",
        "有些生意上的事情急需公子爷赶回去处理。"), plan)
    assert not ok
    assert broken and broken[0]["reason"] == "cross_shot_split"


def test_terminal_gate_intercepts_full_line_split_fragment():
    """二审 H2②：终稿门拦截**拆分层**产生的句中碎片（09-26 三镜全句复刻）.

    职责边界：候选层门在拆分之前（对拆分碎片天然不敏感），终稿门（finalize 后、
    BEST-GUARD 前）才盖到送选稿/提交门的最终形态。本用例用 09-26 的三镜全句复刻
    钉住该形态：三镜拼接恰为完整台词、但首镜句中截断 → 判破损。
    """
    from step01_script_gen.plan_feasibility import check_script_dialogue_integrity

    plan = {"dialogue_lines": [
        "且慢，我刚刚收到城里传来的消息，有些生意上的事情急需公子爷赶回去处理，"
        "公子如今酩酊大醉，难以起身，你们来的正好，去把公子的马车赶到水边来，"
        "我和听香姑娘要扶公子马上回城。"], "shot_beats": ["b"] * 3}
    ok, broken = check_script_dialogue_integrity(_mk_script(
        "张十三道：且慢，我刚刚收到城里传来的消息，有些生意上的事情急需",
        "张十三道：公子爷赶回去处理，公子如今酩酊大醉，难以起身，你们来的正好",
        "张十三道：去把公子的马车赶到水边来，我和听香姑娘要扶公子马上回城"), plan)
    assert not ok
    assert len(broken) == 1 and broken[0]["reason"] == "cross_shot_split"


# ---------------------------------------------------------------------------
# v6.136 A+B 统一判据（2026-09-26 用户决策）：单镜可表示性
# ---------------------------------------------------------------------------

_X64 = ("听香道：不……不知道，那人……那人一下子从水里跳出来，就……就杀了公子，"
        "然后又……又跳进水里不见了，奴家……奴家连他是男是女都没看清……")
_X84 = ("且慢，我刚刚收到城里传来的消息，有些生意上的事情急需公子爷赶回去处理，"
        "公子如今酩酊大醉，难以起身，你们来的正好，去把公子的马车赶到水边来，"
        "我和听香姑娘要扶公子马上回城。")
_SRC_AB = _X64 + "\n" + _X84 + "\n" + "住嘴！"


def test_check_dialogue_representable_64_char_line_is_kept():
    """A 判据（用户决策反例①）：64 字口吃台词按省略号句末切分 → **可保留**.

    判据**不是**"整条 ≤12s"——原镜约 12.5s，但句末切分后每段 ≤ 上限
    （cap 12s 下 DP 解为两段；cap 8s 下三段 ≈5.9/5.9/3.8s），必须保留；
    只有"无合法切点"才重选。
    """
    import step01_script_gen.plan_feasibility as pf
    from step01_script_gen.timecode import _estimate_duration

    r = pf.check_dialogue_representable(_X64)
    assert r["representable"], r
    assert r["split"] and 2 <= len(r["split"]) <= 4, r
    for part in r["split"]:
        assert _estimate_duration(part) <= pf.active_shot_cap() + 0.01, part
        assert part.rstrip()[-1] in "。！？…；", part


def test_check_dialogue_representable_84_char_single_sentence_is_rejected():
    """A 判据（用户决策反例②）：84 字**单句**台词（句内只有逗号）→ 不可表示.

    09-26 三次实跑的确定性失败源：整条装不进单镜、拆开必产生句中碎片。
    reason 必须点名"无句末边界"，重选反馈据此让规划阶段换原文台词。
    """
    import step01_script_gen.plan_feasibility as pf

    r = pf.check_dialogue_representable(_X84)
    assert not r["representable"], r
    assert r["split"] is None
    assert "无句末边界" in r["reason"]


def test_find_legal_split_returns_none_when_whole_line_fits():
    """整条已装得下 → None（无切分需求，不是"不可切"）."""
    import step01_script_gen.plan_feasibility as pf

    assert pf.find_legal_split("张十三道：住嘴！", 12.0) is None


def test_evaluate_plan_flags_unrepresentable_dialogue():
    """A 判据接入 evaluate_plan：不可表示台词 → infeasible + 结构化明细.

    **不是静默剔除**（对比 `_drop_unverified` 只删原文没有的台词）——走
    infeasible → 规划重选反馈（点名台词）→ 仍不可行才显式停止交内容取舍。
    """
    from step01_script_gen.plan_feasibility import evaluate_plan
    rep = evaluate_plan(_plan(lines=[_X84]), novel_text=_SRC_AB)
    assert rep["verdict"] == "infeasible"
    assert len(rep["dialogue_unrepresentable"]) == 1
    assert rep["dialogue_unrepresentable"][0]["line"] == _X84
    assert any("无句末合法切分" in r for r in rep["reasons"])
    assert any("必保留事实" in s for s in rep["suggestions"])
    # 预算内但不可表示 ⇒ 不可行的原因是可表示性，不是预算
    assert rep["lower_bound_sec"] <= rep["budget"]["max"]


def test_evaluate_plan_keeps_multi_sentence_long_line_feasible():
    """64 字多句（省略号分句）台词 → 计划可行（不误伤可合法切分的台词）."""
    from step01_script_gen.plan_feasibility import evaluate_plan
    rep = evaluate_plan(_plan(lines=[_X64]), novel_text=_SRC_AB)
    assert rep["verdict"] == "feasible", rep["reasons"]
    assert rep["dialogue_unrepresentable"] == []


def test_replan_feedback_names_unrepresentable_lines():
    """重选反馈必须点名不可表示台词并带上必保留事实/事件顺序硬边界."""
    import step01_script_gen.outline as o

    rep = {"budget": {"min": 60, "max": 90}, "dialogue_lines": 1,
           "dialogue_sec": 21.59, "lower_bound_sec": 21.59,
           "beats": 3, "beats_floor_sec": 9.0,
           "dialogue_unrepresentable": [{"line": _X84, "est_sec": 21.59,
                                         "reason": "估时 21.6s 超单镜上限 12s…"}]}
    block = o._budget_feedback_block(rep, {"dialogue_lines": [_X84],
                                           "shot_beats": ["b1", "b2"]})
    assert "且慢" in block and "21.6" in block, "必须点名台词与估时"
    assert "必保留事实" in block and "事件顺序" in block, "必须带上硬边界"
    assert "逐字" in block, "替换台词同样必须逐字来自原文"
    # 六审 P1-1：可表示性单独触发（预算内）也必须附完整旧计划——
    # "其余字段原样返回"要求模型看得见旧字段，否则重选必被 _plan_core_preserved 拒收
    assert '"dialogue_lines"' in block and '"shot_beats"' in block,         "旧计划 JSON 必须附在反馈里"
    # 预算未超 → 不得出现预算块（"装不下"是假命题）
    assert "对白一项就" not in block


def test_replan_resolves_unrepresentable_dialogue(monkeypatch):
    """A 判据贯通：计划含不可表示台词 → 重选反馈 → 换成合法原文台词后采纳."""
    import step01_script_gen.outline as o

    plan = _plan(lines=[_X84])
    box = _patch_outline_service(monkeypatch, [_plan_response(["住嘴！"])])
    new_plan, info = o.enforce_budget_by_replanning(
        plan, _SRC_AB, model=None, base_prompt="BASE", system_prompt="SYS")
    assert "必保留事实" in box["prompts"][0], "反馈必须点名可表示性问题"
    assert info["result"] == "feasible" and info["after_unrepresentable"] == 0, info
    assert new_plan["dialogue_lines"] == ["住嘴！"]


def test_find_legal_split_prefers_fewest_parts_and_never_cuts_inside_punct_run():
    """六审 P2：最少段数优先（两段合法不选三段）+ 不在 `……` 标点串中间切.

    64 字口吃台词 cap 12s：两段合法解存在（DP 解 ≈6.4/8.2s；用户 2026-09-26
    判断"两段可保留"的正是这条）——跨段数取「最长段最短」会误选三段。
    """
    from step01_script_gen.plan_feasibility import (find_legal_split,
                                                    _SENT_END_CHARS,
                                                    _split_speaker_prefix)
    from step01_script_gen.timecode import _estimate_duration

    parts = find_legal_split(_X64, 12.0)
    assert parts and len(parts) == 2, parts
    for p in parts:
        assert _estimate_duration(p) <= 12.01
    for a, b in zip(parts, parts[1:]):
        head = _split_speaker_prefix(b)[1]
        assert head and head[0] not in _SENT_END_CHARS, \
            "切点落在标点串中间（下一段以句末标点开头）: %r" % b


def test_find_legal_split_is_exact_with_many_bounds():
    """六审 P1-3：切点多（>14）时**不得截窗**——DP 精确解与穷举参考一致.

    本判据直接驱动规划重选/显式停止，漏掉合法切点会误报"不可表示"、引发错误的
    内容取舍。用种子化伪随机句长造 21 个切点的台词，与穷举参考在可行性与
    最少段数上必须一致（可行解的最长段不得差于穷举最优）。
    """
    import itertools
    import random

    import step01_script_gen.plan_feasibility as pf
    from step01_script_gen.timecode import _estimate_duration

    rnd = random.Random(42)
    sents = []
    for _ in range(22):
        n = rnd.choice([2, 3, 5, 8, 12, 16])
        sents.append("甲" * n + "。")
    body = "".join(sents)
    text = "张十三道：" + body
    bounds = [i + 1 for i, ch in enumerate(body)
              if ch in pf._SENT_END_CHARS and 0 < i + 1 < len(body)
              and body[i + 1] not in pf._SENT_END_CHARS]
    assert len(bounds) > 14, "夹具必须覆盖旧实现的截窗路径"

    def brute(cap):
        best = None
        for k in range(2, 5):
            for cuts in itertools.combinations(bounds, k - 1):
                prev, parts = 0, []
                for c in cuts:
                    parts.append(body[prev:c]); prev = c
                parts.append(body[prev:])
                ests = [_estimate_duration("张十三道：" + p) for p in parts]
                if all(e <= cap + 0.01 for e in ests):
                    m = max(ests)
                    if best is None or (k, m) < best[:2]:
                        best = (k, m)
        return best

    for cap in (10.0, 8.0, 6.0):
        dp = pf.find_legal_split(text, cap)
        ref = brute(cap)
        assert (dp is None) == (ref is None), (cap, dp is None, ref)
        if dp is not None:
            assert len(dp) == ref[0], (cap, len(dp), ref)
            assert max(_estimate_duration(p) for p in dp) <= ref[1] + 0.01, (cap, ref)


def test_evaluate_plan_no_alternative_marker_stops_plan():
    """六审 P1-2：「无合适替代」标记有代码出口——即使其余全部可行也判 infeasible.

    提示要求模型在原文没有更短替代时在 `time_budget` 明写该标记；标记 = 内容
    取舍状态（必保留事实被静默丢弃的风险由用户裁决），代码不代决、不放行。
    """
    from step01_script_gen.plan_feasibility import evaluate_plan

    plan = _plan(lines=["住嘴！"],
                 time_budget="台词不可表示：无合适替代")
    rep = evaluate_plan(plan, novel_text="住嘴！他说完就跳了河。住嘴！")
    assert rep["verdict"] == "infeasible"
    assert any("无合适替代" in r for r in rep["reasons"])
    assert any("用户决策" in s or "内容取舍" in s for s in rep["suggestions"])
    # 预算与可表示性都不是原因——原因只有标记本身
    assert rep["lower_bound_sec"] <= rep["budget"]["max"]
    assert rep["dialogue_unrepresentable"] == []
