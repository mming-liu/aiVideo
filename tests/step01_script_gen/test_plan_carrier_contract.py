# -*- coding: utf-8 -*-
"""计划契约（承载）：**首版 / 重选版 / 缓存版共用的那道门**（2026-09-30 复核裁决）.

复核原话：「另做**不依赖 ops 的计划验收函数**，供首版、重选版和缓存版共用。核对实际
节拍正文／信息目的及选用台词，不能靠"原文依据"互抄判事实已表达；`画面`声明也须与节拍
实际载体一致。」「在生成前统一入口拒绝缺失或无效的承载，**单独报告
`plan_contract_invalid`**；不要把它送进预算重选，也不要报告"需拆集"。」

本文件钉住复核逐条给出的反例/正例：

- **A. 引文互抄不算演出**：把事实声明给一个只写别的事的画面节拍，而该节拍的
  「原文依据」**抄了事实的引文** —— 旧门据此判过（≥6 字交集），现在必须拒收。
- **B. 钩子**：只留开头几个字的台词 ⇒ 拒收；整句入选 + 显式声明承载 ⇒ 通过。
- **C. 正例**：承载与事实同源 ⇒ 通过，且承载**写进计划**（随执行契约下发给生成器）。
- **D. 缓存契约版本**：无版本的旧缓存 ⇒ **备份 + 重新产出首版计划**（不是「故事不可行」）。
- **E. 可行但缺承载不进预算重选**：零次重选调用，报告 `plan_contract_invalid`，
  且**不得**写成「需拆集」。
- **F. 画面声明要与节拍实际载体一致**：标着「台词k」的节拍不能报「画面」承载。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import step01_script_gen.outline as o                                    # noqa: E402
from step01_script_gen.outline import apply_replan_ops                    # noqa: E402
from step01_script_gen import plan_feasibility as pf
from tests import verification_helpers as vh                      # noqa: E402


_LONG = "三年前，我们四人奉命离开故都，潜入这座边城。"
_HOOK = "长刀啊长刀，要到几时你的锋芒才能重现人间？"
_TEA = "四人围桌喝茶。"
_SRC = "甲道：“%s”乙道：“%s”丙道：“%s”" % (_LONG, _HOOK, _TEA)
_FACT = '三年前奉命潜入边城｜原文证据：“%s”' % _LONG
#: 与事实同源的承载（B1 标着「台词1」，那句台词就是事实出处）
_FACTS_OK = [{"index": 1, "beat": 1, "mode": "台词1"}]
_HOOK_OK = {"beat": 2, "mode": "台词2"}


def _beat(body, cite, carrier):
    return "节拍：%s｜本镜信息目的：推进｜原文依据：“%s”｜载体：%s" % (body, cite, carrier)


def _plan(lines, beats, hook=_HOOK, facts=None, hook_carrier="B2/台词2",
          events=("甲发问", "乙独白", "丙喝茶")):
    """一份计划；`hook_carrier` 默认声明钩子那句由 B2 承载（传 None = 不声明）."""
    d = {
        "main_events": list(events),
        "climax": "乙独白",
        "required_facts": list(facts if facts is not None else [_FACT]),
        "causal_chain": ["甲发问→乙独白→丙喝茶"],
        "shot_beats": beats,
        "dialogue_lines": list(lines),
        "end_hook": "乙拿起绣春刀，喃喃自语“%s”暗示新行动" % hook,
        # 2026-09-30 复核第 4 轮：钩子「必须说出口」是**显式契约字段**，不再从
        # `end_hook` 的自由文本里猜（本夹具的语义就是「这句必须说出口」）。
        "hook_spoken": [hook] if hook else [],
        "time_budget": "对白约50%",
    }
    if hook_carrier:
        d["hook_carrier"] = hook_carrier
    return d


def _carried_plan():
    """正例底稿：事实由 B1（台词1=那句话本身）承载；钩子那句是台词2、由 B2 承载."""
    return _plan([_LONG, _HOOK],
                 [_beat("甲发问", _LONG, "台词1"),
                  _beat("乙独白", _HOOK, "台词2"),
                  _beat("丙喝茶", _TEA, "画面")])


# ---------------------------------------------------------------------------
# A. 复核反例：引文互抄
# ---------------------------------------------------------------------------
def test_fact_declared_on_an_unrelated_visual_beat_is_rejected():
    """复核反例 1：事实声明给「只写四人围桌喝茶」的画面节拍 ⇒ **拒收**."""
    new, why = apply_replan_ops(
        _carried_plan(),
        {"dialogue_ops": [{"index": 1, "replace_with": _LONG}],
         "fact_carriers": [{"index": 1, "beat": 3, "mode": "画面"}],
         "hook_carrier": _HOOK_OK}, _SRC)
    assert new is None, why
    assert "没有交集" in why, why


def test_copying_the_fact_quote_into_an_unrelated_beat_does_not_count():
    """**复核反例 2（本轮新增，旧门在它上面通过）**：节拍的「原文依据」**抄了事实引文**，
    但节拍**正文**讲的是另一件事 ⇒ 仍然拒收.

    实测（2026-09-30 归档计划）：旧判据拿事实与节拍的「原文依据」比对——那条引文只要抄
    得对，「观众能不能从画面得知这条事实」根本没人核。现在判据读的是节拍**正文/信息
    目的**（画面）与**选中的台词**（台词 k）。
    """
    fact = _FACT + "｜承载：B3/画面"
    beats = [_beat("甲发问", _LONG, "台词1"),
             _beat("乙独白", _HOOK, "台词2"),
             # ⚠️ 引文 = 事实的引文（一字不差），正文却与这条事实无关
             _beat("四人围桌喝茶", _LONG, "画面")]
    probs = pf.plan_contract_problems(_plan([_LONG, _HOOK], beats, facts=[fact]))
    assert probs and "没有交集" in probs[0], probs
    # 对照组：同一位置，把**正文**改成真的讲这条事实 ⇒ 通过
    ok_beats = list(beats)
    ok_beats[2] = _beat("甲回忆三年前四人奉命潜入边城", _LONG, "画面")
    assert pf.plan_contract_problems(
        _plan([_LONG, _HOOK], ok_beats, facts=[fact])) == []


# ---------------------------------------------------------------------------
# B. 钩子：必须整句入选 + 显式声明承载
# ---------------------------------------------------------------------------
def test_hook_line_that_only_states_the_opening_is_rejected():
    """复核反例 3：钩子那句只留开头五字（「绣春刀啊绣春刀」）⇒ **拒收**."""
    plan = _plan([_LONG, "绣春刀啊绣春刀"],
                 [_beat("甲发问", _LONG, "台词1"),
                  _beat("乙独白", "绣春刀啊绣春刀", "台词2"),
                  _beat("丙喝茶", _TEA, "画面")])
    new, why = apply_replan_ops(
        plan,
        {"dialogue_ops": [{"index": 1, "replace_with": _LONG}],
         "fact_carriers": _FACTS_OK}, _SRC)
    assert new is None, why
    assert "钩子要求说出口" in why, why


def test_hook_requires_an_explicit_carrier_declaration():
    """钩子那句在 `dialogue_lines` 里、也有节拍承载，但计划里**没有** `hook_carrier`
    ⇒ 拒收（承载必须**落进计划**、可核可传：登记缺失就是登记缺失，不靠代码替它猜）."""
    _no_hc = _plan([_LONG, _HOOK],
                   [_beat("甲发问", _LONG, "台词1"), _beat("乙独白", _HOOK, "台词2"),
                    _beat("丙喝茶", _TEA, "画面")],
                   facts=[_FACT + "｜承载：B1/台词1"], hook_carrier=None)
    probs = pf.plan_contract_problems(_no_hc)
    assert probs and "hook_carrier" in probs[0], probs
    # 对照组：同样这条钩子，显式声明承载 ⇒ 通过（缺的是登记，不是内容）
    _hc = dict(_no_hc, hook_carrier="B2/台词2")
    assert pf.plan_contract_problems(_hc) == []


# ---------------------------------------------------------------------------
# C. 正例：承载写进计划，并随执行契约下发
# ---------------------------------------------------------------------------
def test_correct_carriers_pass_and_are_written_into_the_plan():
    """正例：承载与事实同源、钩子整句入选 ⇒ 通过，且承载**写进计划**（可传给生成器）."""
    new, why = apply_replan_ops(
        _carried_plan(),
        {"dialogue_ops": [{"index": 1, "replace_with": _LONG}],
         "fact_carriers": _FACTS_OK, "hook_carrier": _HOOK_OK}, _SRC)
    assert new is not None, why
    assert _FACT in new["required_facts"][0], "事实本体与原文证据不得被改"
    assert "承载：B1/台词1" in new["required_facts"][0], new["required_facts"]
    assert new.get("hook_carrier") == "B2/台词2", new.get("hook_carrier")
    from step01_script_gen.prompts import render_execution_contract
    assert "承载：B1/台词1" in render_execution_contract(new), (
        "承载必须随生成执行契约传给生成器（复核必要条件 2）")


# ---------------------------------------------------------------------------
# D. 缓存契约版本：旧缓存 ⇒ 备份 + 重新产出首版计划
# ---------------------------------------------------------------------------
class _ScriptedService:
    """按脚本依次返回响应，并记录每次请求（用于断言"有没有再调模型"）."""

    def __init__(self, script: list, box: dict):
        self._script, self._box = script, box

    def generate(self, req):
        self._box.setdefault("prompts", []).append(req.user_prompt)
        item = self._script.pop(0) if self._script else None
        if isinstance(item, Exception):
            raise item
        return type("R", (), {"success": True, "text": item or "",
                              "error": "", "usage": None})()


def _patch_service(monkeypatch, script: list) -> dict:
    box: dict = {}
    monkeypatch.setattr(o, "create_service", lambda *a, **k: _ScriptedService(script, box))
    # 2026-10：**内容核验默认开启**，而本夹具只脚本化"规划"响应。核验若走同一个脚本
    # 服务就会拿到空串 ⇒ 记录违约 ×3 ⇒ "核验未完成 ⇒ 零规划调用"，与规划无关的失败。
    # 经 `generate_outline_result` 的 `verify=` 覆盖缝注入**合法**核验记录（不消耗脚本）。
    _gor = o.generate_outline_result

    def _with_verify(*a, **k):
        k.setdefault("verify", lambda view, digest: vh.legal_record_for(
            view, _SRC, plan_digest=digest))
        return _gor(*a, **k)

    monkeypatch.setattr(o, "generate_outline_result", _with_verify)
    return box


def _first_version_plan(lines):
    """契约完整的**首版计划**（响应用）：事实由 B1（台词1）承载，钩子由 B2 承载.

    ⚠️ `_validate_outline` 要求 `main_events` 3-6 条、节拍条数 ≥ 事件条数。
    """
    return _plan(lines,
                 [_beat("甲发问", _LONG, "台词1"),
                  _beat("乙独白", _HOOK, "台词2"),
                  _beat("丙喝茶", _TEA, "画面")],
                 facts=[_FACT + "｜承载：B1/台词1"])


_VALID_JSON = json.dumps(_first_version_plan([_LONG, _HOOK]), ensure_ascii=False)


def test_new_cache_carries_the_contract_version(tmp_path, monkeypatch):
    _patch_service(monkeypatch, [_VALID_JSON])
    cache = tmp_path / "c.json"
    assert o.generate_outline(_SRC, cache_path=cache) is not None
    _d = json.loads(cache.read_text(encoding="utf-8"))
    assert _d.get("plan_contract_version") == pf.PLAN_CONTRACT_VERSION, _d.keys()


def test_cache_without_contract_version_is_backed_up_and_regenerated(tmp_path, monkeypatch):
    """复核裁决：旧缓存缺新字段 ⇒ **需重新规划**（备份 + 重新产出首版计划），
    不是「故事不可行」，也不套用 `STEP01_FORCE_REPLAN` 那轮预算重选."""
    cache = tmp_path / "c.json"
    box = _patch_service(monkeypatch, [_VALID_JSON])
    assert o.generate_outline(_SRC, cache_path=cache) is not None
    assert len(box["prompts"]) == 1
    # 把缓存退化成「旧格式」：删掉契约版本（同 source_hash 走迁移分支）
    _d = json.loads(cache.read_text(encoding="utf-8"))
    _d.pop("plan_contract_version")
    cache.write_text(json.dumps(_d, ensure_ascii=False), encoding="utf-8")
    box2 = _patch_service(monkeypatch, [_VALID_JSON])
    p2 = o.generate_outline(_SRC, cache_path=cache)
    assert len(box2["prompts"]) == 1, "无版本的旧缓存必须重新产出首版计划（不复用）"
    assert p2 is not None
    _bak = tmp_path / ("c.json.bak-contract%d" % pf.PLAN_CONTRACT_VERSION)
    assert _bak.exists(), "旧缓存必须留档"
    assert json.loads(_bak.read_text(encoding="utf-8")) == _d, "留档内容 = 被替换的旧缓存"
    assert json.loads(cache.read_text(encoding="utf-8"))["plan_contract_version"] \
        == pf.PLAN_CONTRACT_VERSION


# ---------------------------------------------------------------------------
# E. 可行/超预算但缺承载：**不进预算重选**，报告 plan_contract_invalid
# ---------------------------------------------------------------------------
def test_missing_carrier_never_enters_the_budget_replan(tmp_path, monkeypatch):
    """缺承载的**超预算**计划：一次重选调用都不许发（顺序：契约门 → 预算门）.

    判别性：把契约门放在重选之后（旧顺序）时，这份计划会被当成「装不下」送去重选
    ——那是**错误归因**（换内容解决不了「取舍没做完」），也会白烧一轮模型。
    """
    over = _plan([_LONG] * 40,               # 40 句 × ~3s ⇒ 远超上限
                 [_beat("四人围桌喝茶", _TEA, "画面"),
                  _beat("乙不言语", _TEA, "画面"),
                  _beat("丙收拾茶具", _TEA, "画面")],
                 hook_carrier=None)
    box = _patch_service(monkeypatch, [json.dumps(over, ensure_ascii=False)])
    cache = tmp_path / "c.json"
    # 2026-10-01（复核第 31 轮）：契约问题走**有界契约修订**（≤3 次、与预算重选共用额度）。
    # 2026-10-02（工作包 §4 D）：生产结论从**结构化结果**读——`generate_outline` 只转发
    # `executable=True` 的计划，契约未完成的计划**不再被当成计划下发**。
    _res = o.generate_outline_result(_SRC, cache_path=cache)
    # 判别性不变：**不得**走预算重选——反馈里没有预算块（"重选台词"那段），也不报"拆集"。
    assert len(box["prompts"]) >= 1, box["prompts"]
    _extra = box["prompts"][1:]
    assert _extra, "契约不闭合必须**真的有反馈轮**（不是直接停下）"
    # 判据用**预算反馈块自己的标题**：「重选台词」这四个字在首版提示（`OUTLINE_PROMPT`）
    # 里也出现（「之后重选台词增删时…」），加了完整计划修订路径后它不再能区分「走了预算重选」。
    assert not any("重选本集台词（预算反馈" in str(p) for p in _extra), _extra
    assert not any("拆集" in str(p) for p in _extra), _extra
    assert _res["executable"] is False
    assert "budget_infeasible" in _res["acceptance"]["codes"], _res["acceptance"]["codes"]
    assert not cache.exists(), "契约未完成的计划不进缓存（下次重新产出首版）"
    bad = _res["plan"]
    assert isinstance(bad, dict), "结构化结果必须给出**基线计划**供诊断"
    _patch_service(monkeypatch, [json.dumps(over, ensure_ascii=False)])
    with pytest.raises(pf.PlanContractInvalidError) as _e:
        pf.check_plan_before_generation(bad, chapter_dir=tmp_path, novel_text=_SRC)
    _msg = str(_e.value)
    assert "契约未完成" in _msg and "承载" in _msg, _msg
    assert "拆集" not in _msg, "缺承载不得写成「需拆集」：%s" % _msg
    _d = json.loads((tmp_path / "step01_plan_contract_invalid.json").read_text(encoding="utf-8"))
    assert _d["verdict"] == "plan_contract_invalid" and _d["conclusion"] == "plan_incomplete"
    assert _d["problems"], _d
    assert not (tmp_path / "step01_plan_infeasible.json").exists(), (
        "契约问题不得落成「不可行」诊断（两份诊断分开）")


def test_contract_gate_is_independent_of_ops():
    """同一道门对**没有 ops**的计划照样判（首版/缓存版路径）."""
    probs = pf.plan_contract_problems(
        _plan([_LONG, _HOOK],
              [_beat("甲发问", _LONG, "台词1"), _beat("乙独白", _HOOK, "台词2")]))
    assert probs and "未声明承载" in probs[0], probs
    assert pf.plan_contract_problems(_first_version_plan([_LONG, _HOOK])) == []


# ---------------------------------------------------------------------------
# F. 「画面」声明必须与节拍实际载体一致
# ---------------------------------------------------------------------------
def test_visual_declaration_may_ride_a_voice_beat_if_the_body_expresses_it():
    """2026-10-18 契约变更（用户裁决"允许同镜声画共存"）：画面承载可落在承声节拍上。

    改动前：标着「台词k」的节拍报「画面」承载 ⇒ `carrier_fact_visual_mismatch`。
    改动后：允许；但**正文仍须表达该事实**——反例必须继续被拒收。
    """
    fact = _FACT + "｜承载：B1/画面"
    # 正例：节拍名（画面正文）表达该事实，且该节拍同时标着「台词1」⇒ 允许
    probs = pf.plan_contract_problems(_plan(
        [_LONG, _HOOK],
        [_beat("三年前四人奉命潜入边城", _LONG, "台词1"), _beat("乙独白", _HOOK, "台词2")],
        facts=[fact]))
    assert not probs, probs
    # 反例：节拍名与该事实只共享"四人"一个要素 ⇒ 仍须拒收
    probs2 = pf.plan_contract_problems(_plan(
        [_TEA, _HOOK], [_beat("四人围桌喝茶", _TEA, "台词1"), _beat("乙独白", _HOOK, "台词2")],
        facts=[fact]))
    assert probs2 and "没有交集" in " ".join(probs2), probs2


# ---------------------------------------------------------------------------
# G. 主流程级：契约未完成 = **零候选生成**，且不与其他失败混报
# ---------------------------------------------------------------------------
def test_contract_error_is_not_a_retryable_generation_failure():
    """`PlanContractInvalidError` 刻意不继承 `RuntimeError`——否则候选循环会当生成失败重试."""
    assert not issubclass(pf.PlanContractInvalidError, RuntimeError), (
        "候选生成的重试分支 `except RuntimeError` 会把它按「重试」处理")


def test_contract_invalid_plan_makes_zero_candidate_attempts(tmp_path, monkeypatch):
    """契约未完成 → 停在计划层：不建服务、不写正式产物、只留契约诊断."""
    import step01_script_gen.main as m

    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    _calls = {"service": 0}

    def _boom(*a, **k):
        _calls["service"] += 1
        raise AssertionError("契约未完成的计划不该走到模型调用")

    monkeypatch.setattr(m, "create_service", _boom)
    plan = _plan([_LONG, _HOOK],
                 [_beat("甲发问", _LONG, "台词1"), _beat("乙独白", _HOOK, "台词2"),
                  _beat("丙喝茶", _TEA, "画面")], hook_carrier=None)
    with pytest.raises(pf.PlanContractInvalidError) as _e:
        m._generate_script_candidates(_SRC, None, None, plan)
    _msg = str(_e.value)
    assert "契约未完成" in _msg and "不生成完整稿" in _msg, _msg
    assert "拆集" not in _msg and "不可行" not in _msg.split("⇒")[0], (
        "不得把「登记缺失」写成内容结论：%s" % _msg)
    assert _calls["service"] == 0
    _d = json.loads((tmp_path / "step01_plan_contract_invalid.json").read_text(encoding="utf-8"))
    assert _d["conclusion"] == "plan_incomplete"
    assert not (tmp_path / "step01_plan_infeasible.json").exists()
    assert not (tmp_path / "script_result.json").exists()


# ---------------------------------------------------------------------------
# H. 大纲 Schema 模板必须与正文规则一致地声明「旁白k」载体
# ---------------------------------------------------------------------------
def test_outline_prompt_shot_beats_schema_declares_narration_carrier():
    """`shot_beats` 的 JSON 模板必须列出全部三种合法载体（含 `旁白k`）.

    2026-10 复核：模板此前只写 `载体：台词k 或 画面`，而**同一个** `OUTLINE_PROMPT`
    的规则正文（「第三种承载：图外旁白」）已经要求 `载体：旁白k`。模板少一个合法
    取值 ⇒ 模型照模板填 ⇒ `narration_lines` 里有编号却无 beat 承接 ⇒
    `plan_voice_carrier_problems` 报缺失、触发重抽（假阳性）。
    模板与正文是同一份提示的两个部分，取值集合必须一致。
    """
    prompt = o.OUTLINE_PROMPT
    assert "载体：台词k 或 旁白k 或 画面" in prompt, (
        "shot_beats 模板没有列出全部三种合法载体（台词k / 旁白k / 画面）")
    # 正文规则里的旁白承载必须同时在场——否则模板与规则又会对不上
    assert "载体：旁白k" in prompt, "正文规则里的旁白承载声明不见了"
