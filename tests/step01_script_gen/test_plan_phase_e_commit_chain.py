# -*- coding: utf-8 -*-
"""**E 阶段贯通矩阵**（工作包 §4 E）：计划链 → 提交入口 → manifest 核验，全离线.

链路里**每一段都用真实现**，只有文本模型被本地桩替代（不发网络请求）：

    真实反馈构造（`plan_acceptance_problems`）
      → 桩规划服务返回**操作**
      → 真实 `apply_replan_ops`（控制器内应用）
      → 规范化 + 完整重验（`normalize_plan_ranges` + `plan_acceptance_problems`）
      → `render_execution_contract`（生成侧真正收到的契约块）
      → 按该契约构造脚本（`plan_refs` 声明 + 台词逐字进旁白）
      → 真实 `main._commit_step01_outputs`
      → 真实 `verify_step01_commit_manifest`

退出条件（§4 E）：**正例可提交**、**反例不得 COMMITTED**，且 manifest 与同一份脚本
revision / 摘要绑定。

- E01 正例：合法**声明式修订**修好承载 ⇒ `executable` ⇒ 提交 `COMMITTED` ⇒ manifest 核验通过；
- E02 反例（各自独立目录，且都用**真消费者**核验终局）：
  ① **身份失效**（稿件带修订前版本的 `plan_id`）② **声明被删** ③ **修复轮把契约改坏**。
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.quality_gate import (                                        # noqa: E402
    QualityGateError, compute_script_revision, verify_step01_commit_manifest)
from step01_script_gen import outline as o                               # noqa: E402
from step01_script_gen import plan_feasibility as pf                     # noqa: E402
from step01_script_gen.prompts import render_execution_contract           # noqa: E402
from tests import verification_helpers as vh                             # noqa: E402

_LINE = "甲板上有风，帆布猎猎作响。"
_LINE2 = "他一步一顿地走进那间屋子。"
_SRC = "开头。%s事件乙。事件丙。%s" % (_LINE, _LINE2)


# ---------------------------------------------------------------------------
# 计划侧夹具（与 C/D 矩阵同形：F1 声明由 B3 承载，而 B3 是画面节拍 ⇒ 承载错位）
# ---------------------------------------------------------------------------
def _plan(**over):
    p = {"main_events": ["甲", "乙", "丙"], "climax": "丙", "causal_chain": ["甲→乙→丙"],
         "required_facts": ["甲板上有风，帆布猎猎作响｜原文证据：“%s”｜承载：B3/台词1" % _LINE],
         "hook_spoken": [], "hook_carrier": "", "narration_lines": [],
         "dialogue_lines": [_LINE],
         "shot_beats": [
             "节拍：甲板上有风｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % _LINE,
             "节拍：乙｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _LINE,
             "节拍：丙｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _LINE]}
    p.update(over)
    return p


#: 合法**声明式修订**：只重声明承载（不动台词/节拍）
_FIX_DECL = {"fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}]}
#: 合法**结构修订**：末尾插一条新节拍（动 `shot_beats` ⇒ 计划身份随之改变）
_FIX_INSERT = {"fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}],
               "beat_ops": [{"op": "insert_after", "index": 3,
                             "new_text": "节拍：丁｜本镜信息目的：推进｜原文依据：“%s”" % _LINE2,
                             "new_carrier": "画面"}]}
#: 合法**拆分**修订：B2 拆成两条 ⇒ 其后的节拍编号整体后移（引用要跟着重映射）
_FIX_SPLIT = {"fact_carriers": [{"index": 1, "beat": 1, "mode": "台词1"}],
              "beat_ops": [{"op": "split", "index": 2, "parts": [
                  {"new_text": "节拍：乙上｜本镜信息目的：推进｜原文依据：“%s”" % _LINE,
                   "new_carrier": "画面"},
                  {"new_text": "节拍：乙下｜本镜信息目的：推进｜原文依据：“%s”" % _LINE,
                   "new_carrier": "画面"}]}]}


def _plan_with_narration():
    """带**图外旁白**的计划：N1 由 B3 承载（`载体：旁白1`），F1 由 B1/台词1 承载."""
    return _plan(narration_lines=[{"text": _LINE2, "source": _LINE2, "fact_refs": []}],
                 required_facts=["甲板上有风，帆布猎猎作响｜原文证据：“%s”｜承载：B1/台词1" % _LINE],
                 shot_beats=[
                     "节拍：甲板上有风｜本镜信息目的：推进｜原文依据：“%s”｜载体：台词1" % _LINE,
                     "节拍：乙｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _LINE,
                     "节拍：丙｜本镜信息目的：推进｜原文依据：“%s”｜载体：旁白1" % _LINE2])


def _plan_with_hook():
    """带**必须说出口**的钩子：`hook_spoken=[D1]`、承载声明 `B1/台词1`."""
    return _plan(hook_spoken=[_LINE], hook_carrier="B1/台词1",
                 required_facts=["甲板上有风，帆布猎猎作响｜原文证据：“%s”｜承载：B1/台词1" % _LINE])


class _Svc:
    """本地规划服务桩：按顺序回放给定响应（payload 用尽即失败）."""

    def __init__(self, payloads=()):
        self.payloads = list(payloads)
        self.reqs: list = []

    def generate(self, request):
        from _text_gen.models import TextGenerationResult
        self.reqs.append(request)
        if not self.payloads:
            return TextGenerationResult(success=False, error="stub: 没有更多响应")
        _p = self.payloads.pop(0)
        if isinstance(_p, str):
            return TextGenerationResult(success=True, text=_p)
        return TextGenerationResult(success=True, text=json.dumps(_p, ensure_ascii=False))


def _patch_svc(monkeypatch, payloads=()):
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
    return _svc


def _chain(monkeypatch, ops):
    """真实链到 `executable` 计划：桩回 [基线计划, 操作] ⇒ 控制器应用并完整重验."""
    _svc = _patch_svc(monkeypatch, [_plan(), ops])
    _res = o.generate_outline_result(_SRC)
    assert _res["executable"] is True, _res["failure"]
    assert _res["source"] == "initial" and _res["revision"]["calls"] == 1, _res["revision"]
    return _svc, _res["plan"]


# ---------------------------------------------------------------------------
# 提交侧夹具（与 test_step01_commit.py 同形：只桩模型与文件系统噪声）
# ---------------------------------------------------------------------------
def _shot(i: int) -> dict:
    return {
        "index": i, "duration_sec": 5.0, "duration": 5.0,
        "prompt": "[中景] 测试镜头%d，画面各不相同" % i,
        "ff_desc": "第%d镜起始画面，人物立于画左" % i,
        "lf_desc": "第%d镜结束画面，人物立于画右" % i,
        "motion_desc": "0-2秒：动作甲；2-5秒：动作乙",
        "narration_segment": "", "subtitle": "",
        "main_subject": "群演", "scene": "水湾", "location": "水湾",
        "keyframes": [{"desc": "过程画面", "stage": "approach", "t_sec": 2.5}],
        "frame_layouts": {
            "ff": [{"name": "群演", "on_screen": True, "position": "画左",
                    "pose": "站立", "gaze_target": "画右", "props": []}],
            "lf": [{"name": "群演", "on_screen": True, "position": "画右",
                    "pose": "站立", "gaze_target": "画左", "props": []}],
            "kfs": [[{"name": "群演", "on_screen": True, "position": "画中",
                      "pose": "站立", "gaze_target": "画前", "props": []}]],
        },
    }


def _script_for(plan, *, plan_id=None, declarations=True):
    """**按计划构造**一份满足事件履约契约的稿件（每镜 `plan_refs`，台词逐字进旁白）.

    声明形状直接照 `render_execution_contract` 第五节的写法：beats/lines 只填数字；
    镜数 = 计划节拍数（本夹具一镜承载一条节拍，台词落在承载它那条节拍的那一镜）。
    """
    _line = (pf.plan_dialogue_lines(plan) or [""])[0]
    _n = len([b for b in (plan.get("shot_beats") or []) if str(b).strip()])
    script = {"title": "t", "characters": [], "hook": None,
              "plan_id": pf.plan_identity(plan) if plan_id is None else plan_id,
              "shots": [_shot(i) for i in range(1, _n + 1)]}
    script["shots"][0]["narration_segment"] = _line
    script["shots"][0]["subtitle"] = _line
    if declarations:
        for _i in range(_n):
            script["shots"][_i]["plan_refs"] = {"beats": [_i + 1],
                                                "lines": [1] if _i == 0 else []}
    return script


def _chapter(monkeypatch, tmp_path):
    """把提交入口指到临时目录，并屏蔽外部依赖（与 test_step01_commit.py 同形）."""
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_VERSION
    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    monkeypatch.setattr(m, "OUTPUT_FILE", tmp_path / "script_result.json")
    monkeypatch.setattr(m, "SCRIPT_FILE", tmp_path / "script_result.json")
    monkeypatch.setattr(m, "collect_structured_issues",
                        lambda *a, **k: ([], False, RULE_VERSION))
    monkeypatch.setattr(m, "_snapshot_run_artifacts", lambda script=None: None)
    return tmp_path


def _audit_payload(script, *, issues=None):
    import step01_script_gen.main as m
    from step01_script_gen.audit import audit_prompt_version as _apv
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT
    return {"status": "ok", "model": "fake-model", "error": "",
            "issues": list(issues or []),
            "input_revision": compute_script_revision(script),
            "rule_source_fingerprint": RULE_SOURCE_FINGERPRINT,
            "audit_scope": m.STEP01_AUDIT_SCOPE,
            "audit_prompt_version": _apv(),
            "audit_focus_version": m._audit_focus_version(),
            "created_at": "2026-10-02T00:00:00", "run_id": "test-run"}


def _fake_audit(monkeypatch, issues=None):
    """替换**审计模型调用**（不是 `_run_final_semantic_audit`）——返回调用计数容器.

    计的是"真正发出去的审计调用"：契约门在审计**之前**，所以被门拦下时计数必须为 0。
    """
    import step01_script_gen.audit as audit_mod
    calls = {"n": 0}

    def fake(novel_text, plan, script, *, model=None, timeout_sec=180,
             audit_focus=None, metadata=None, **kwargs):
        calls["n"] += 1
        from step01_script_gen.quality_issues import Issue
        result = audit_mod.AuditResult("ok", [], "")
        result.issues = [
            Issue(code=str(i.get("code")), severity=str(i.get("severity") or "visual_risk"),
                  message=str(i.get("message") or ""), shot_id=i.get("shot_id"),
                  frame_id="", field_path=str(i.get("field_path") or ""), evidence="",
                  repair_targets=i.get("repair_targets"), repairable=True)
            for i in (issues or [])]
        result.raw_text = ""
        return result

    monkeypatch.setattr(audit_mod, "run_same_model_audit_with_status", fake)
    return calls


# ---------------------------------------------------------------------------
# E01 正例：声明式修订 → 契约 → 提交 → manifest 核验
# ---------------------------------------------------------------------------
def test_e01_the_chained_revision_commits_and_the_manifest_verifies(tmp_path, monkeypatch):
    """**贯通正例**：计划链产出的计划 ⇒ 按契约构造的脚本 ⇒ COMMITTED ⇒ manifest 可核验."""
    import step01_script_gen.main as m

    _svc, plan = _chain(monkeypatch, _FIX_DECL)
    # ① 修订请求确实是**操作式**（不是让模型重写整份大纲），且契约已闭合
    assert "## 请**只输出操作**" in _svc.reqs[1].user_prompt, _svc.reqs[1].user_prompt
    assert "承载：B1/台词1" in plan["required_facts"][0], plan["required_facts"]
    assert o.plan_acceptance_problems(plan, _SRC) == []

    # ② 生成侧真正收到的契约块：脚本要满足的就是它（台词逐字 + 节拍编号）
    _contract = render_execution_contract(plan)
    for _b in ("B1", "B2", "B3"):
        assert _b in _contract, _b
    assert _LINE in _contract and "二、必保留事实" in _contract, _contract[:200]

    # ③ 真实提交入口
    _chapter(monkeypatch, tmp_path)
    _fake_audit(monkeypatch)
    script = _script_for(plan)
    _on_disk, summary = m._commit_step01_outputs(script, _SRC, plan=plan)
    assert summary["status"] == "COMMITTED", summary
    assert not summary["blocking"], summary["blocking"]

    # ④ 真消费者核验：manifest 与**盘上那份**脚本的 revision / 三份产物摘要绑定
    _disk = json.loads((tmp_path / "script_result.json").read_text(encoding="utf-8"))
    manifest = verify_step01_commit_manifest(tmp_path, _disk, stage="step01")
    assert manifest["status"] == "COMMITTED"
    assert manifest["script_revision"] == compute_script_revision(_disk)
    assert set(manifest["artifacts"]) >= {"script_result.json", "step01_quality.json",
                                          "step01_audit.json"}
    assert _disk["plan_id"] == pf.plan_identity(plan), "落盘稿必须与**本轮计划**同一身份"


# ---------------------------------------------------------------------------
# E01b 正例：合法**拆分 + 编号重映射** → 契约 → 提交
# ---------------------------------------------------------------------------
def test_e01b_a_split_revision_renumbers_and_the_rebuilt_script_commits(tmp_path, monkeypatch):
    """**拆分正例**：B2 拆成两条 ⇒ 原 B3（丙）后移成 B4；脚本按**新编号**重建后提交.

    判别性：把应用器的 `split` 改成"不展开"（沿用旧编号）⇒ 本用例的节拍/契约断言转红。
    """
    import step01_script_gen.main as m

    _svc, plan = _chain(monkeypatch, _FIX_SPLIT)
    _heads = [b.split("｜")[0] for b in plan["shot_beats"]]
    assert _heads == ["节拍：甲板上有风", "节拍：乙上", "节拍：乙下", "节拍：丙"], _heads
    # 承载声明仍在 **B1**（拆分点在它之后）——重映射只动被移动的编号
    assert "承载：B1/台词1" in plan["required_facts"][0], plan["required_facts"]
    # 生成契约块用的是**重映射后**的编号（生成侧照它声明 plan_refs）
    _contract = render_execution_contract(plan)
    assert "B2. 节拍：乙上" in _contract and "B4. 节拍：丙" in _contract, _contract[-500:]

    _chapter(monkeypatch, tmp_path)
    _fake_audit(monkeypatch)
    _on_disk, summary = m._commit_step01_outputs(_script_for(plan), _SRC, plan=plan)
    assert summary["status"] == "COMMITTED", summary
    _disk = json.loads((tmp_path / "script_result.json").read_text(encoding="utf-8"))
    assert verify_step01_commit_manifest(tmp_path, _disk, stage="step01")["status"] == "COMMITTED"
    assert _disk["plan_id"] == pf.plan_identity(plan)


# ---------------------------------------------------------------------------
# E02 反例①：身份失效（稿件带**修订前**版本的 plan_id）
# ---------------------------------------------------------------------------
def test_e02_a_stale_plan_id_after_a_structural_revision_cannot_commit(tmp_path, monkeypatch):
    """插节拍的修订换掉了计划身份：稿件仍是旧身份 ⇒ 契约门 BLOCK、**一次审计都不发**.

    判别性：两段式——同一份稿换成**当前**身份即可提交（证明不是"怎么都 BLOCK"）。
    """
    import step01_script_gen.main as m

    _svc, plan = _chain(monkeypatch, _FIX_INSERT)
    assert len([b for b in plan["shot_beats"] if str(b).strip()]) == 4, plan["shot_beats"]
    _old_id = pf.plan_identity(_plan())                    # 修订**前**那份计划的身份
    assert _old_id != pf.plan_identity(plan), "前提：这次修订确实换了身份"

    # ① 旧身份 ⇒ 停在契约门（**零审计调用**）
    _chapter(monkeypatch, tmp_path / "a")
    calls = _fake_audit(monkeypatch)
    _s1, summary1 = m._commit_step01_outputs(
        _script_for(plan, plan_id=_old_id), _SRC, plan=plan)
    assert summary1["status"] == "BLOCK", summary1
    assert summary1["repair_outcome"] == "event_contract_broken", summary1
    assert "plan_refs_stale_plan" in summary1["detail"], summary1["detail"]
    assert calls["n"] == 0, "契约门必须在审计之前拦下（不得先烧一次审计）"
    with pytest.raises(QualityGateError) as _e:
        verify_step01_commit_manifest(tmp_path / "a", _script_for(plan, plan_id=_old_id))
    assert "not COMMITTED" in str(_e.value), str(_e.value)

    # ② 对照：同一份稿、当前身份 ⇒ 提交
    _chapter(monkeypatch, tmp_path / "b")
    _s2, summary2 = m._commit_step01_outputs(_script_for(plan), _SRC, plan=plan)
    assert summary2["status"] == "COMMITTED", summary2
    assert calls["n"] == 1, "对照相只发一次审计"


# ---------------------------------------------------------------------------
# E02 反例②：声明被删
# ---------------------------------------------------------------------------
def test_e02_dropping_a_declaration_after_the_chain_blocks_before_any_audit(tmp_path,
                                                                            monkeypatch):
    """链上计划带承载契约 ⇒ 稿件丢掉 `plan_refs` ⇒ 提交点 BLOCK（审计零调用）."""
    import step01_script_gen.main as m

    _svc, plan = _chain(monkeypatch, _FIX_DECL)
    _chapter(monkeypatch, tmp_path)
    calls = _fake_audit(monkeypatch)
    script = _script_for(plan)
    script["shots"][1].pop("plan_refs")                    # 只删中间一镜的声明
    _on_disk, summary = m._commit_step01_outputs(script, _SRC, plan=plan)
    assert summary["status"] == "BLOCK", summary
    assert summary["repair_outcome"] == "event_contract_broken", summary
    assert "plan_refs_missing" in summary["detail"], summary["detail"]
    assert calls["n"] == 0, "契约门在审计之前 ⇒ 一次审计都不该发"
    with pytest.raises(QualityGateError):
        verify_step01_commit_manifest(tmp_path, script)


# ---------------------------------------------------------------------------
# E02 反例③：修复轮把契约改坏
# ---------------------------------------------------------------------------
def test_e02_a_repair_that_breaks_the_contract_is_not_committed(tmp_path, monkeypatch):
    """审计报一条可修问题 ⇒ 修复稿改坏契约 ⇒ **不采纳**、BLOCK（基线保留，不下发）."""
    import step01_script_gen.main as m

    _svc, plan = _chain(monkeypatch, _FIX_DECL)
    _chapter(monkeypatch, tmp_path)
    _rounds = {"n": 0}

    def _bad_repair(script, novel_text, **kw):
        _rounds["n"] += 1
        cand = copy.deepcopy(script)
        # 台词没了、声明还在 ⇒ `plan_refs_line_not_in_shot`（新正文不含计划台词）
        cand["shots"][0]["narration_segment"] = "船上没有一个人说话。"
        cand["shots"][0]["subtitle"] = cand["shots"][0]["narration_segment"]
        return cand
    monkeypatch.setattr(m, "_repair_shot_warnings", _bad_repair)
    calls = _fake_audit(monkeypatch, issues=[
        {"code": "model_audit_x", "shot_id": "1", "severity": "visual_risk",
         "message": "m", "field_path": "prompt",
         "repair_targets": [{"shot_id": "1", "field_path": "prompt"}]}])

    script = _script_for(plan)
    _on_disk, summary = m._commit_step01_outputs(script, _SRC, plan=plan)
    assert summary["status"] == "BLOCK", summary
    assert summary["repair_outcome"] == "contract_broken", summary
    assert "不采纳" in summary["detail"], summary["detail"]
    # 基线必须保留：终稿归一可能压掉句末标点，所以判"修复稿的新正文**没进来**"
    _seg = _on_disk["shots"][0]["narration_segment"]
    assert "船上没有一个人说话" not in _seg, "修复稿不许被采纳（基线必须保留）"
    assert "甲板上有风" in _seg, (_seg, "基线旁白应原样保留")
    assert _rounds["n"] == 1 and calls["n"] == 1, "拒收后不得再发一轮审计/修复"
    with pytest.raises(QualityGateError):
        verify_step01_commit_manifest(tmp_path, _on_disk)


# ---------------------------------------------------------------------------
# E02 反例④：**缺旁白声音**（计划声明了图外旁白，稿件里没念出来）
# ---------------------------------------------------------------------------
def _script_with_narration(plan, *, narration=True):
    """按旁白计划构造：N1 落在承载它的那一镜；`narration=False` 时该镜旁白为空."""
    script = _script_for(plan)
    if narration:
        script["shots"][2]["narration_segment"] = _LINE2
        script["shots"][2]["subtitle"] = _LINE2
    return script


def test_e02_a_missing_narration_voice_cannot_commit(tmp_path, monkeypatch):
    """旁白**没被念出来** ⇒ `plan_refs_narration_missing` ⇒ BLOCK；念了就提交（两段式对照）."""
    import step01_script_gen.main as m

    plan = _plan_with_narration()
    assert o.plan_acceptance_problems(plan, _SRC) == [], "前提：这份计划本身是可行的"

    # ① 对照：旁白逐字落在承载它的那一镜 ⇒ COMMITTED
    _chapter(monkeypatch, tmp_path / "a")
    _fake_audit(monkeypatch)
    _s0, s0 = m._commit_step01_outputs(_script_with_narration(plan), _SRC, plan=plan)
    assert s0["status"] == "COMMITTED", s0

    # ② 缺这条旁白 ⇒ 契约门 BLOCK（零审计调用）
    _chapter(monkeypatch, tmp_path / "b")
    calls = _fake_audit(monkeypatch)
    _s1, s1 = m._commit_step01_outputs(
        _script_with_narration(plan, narration=False), _SRC, plan=plan)
    assert s1["status"] == "BLOCK", s1
    assert s1["repair_outcome"] == "event_contract_broken", s1
    assert "plan_refs_narration_missing" in s1["detail"], s1["detail"]
    assert calls["n"] == 0, "契约门在审计之前 ⇒ 一次审计都不该发"
    with pytest.raises(QualityGateError):
        verify_step01_commit_manifest(tmp_path / "b", _script_with_narration(plan))


# ---------------------------------------------------------------------------
# E02 反例⑤：**缺钩子声音**（那句必须说出口的台词不在它声明的镜里）
# ---------------------------------------------------------------------------
def test_e02_a_hook_line_missing_from_its_declaring_shot_cannot_commit(tmp_path, monkeypatch):
    """钩子那句在**别的镜**里说了、声明镜里没有 ⇒ `plan_refs_line_not_in_shot` ⇒ BLOCK.

    台词完整性门**过得去**（这句确实在成片里），拦下它的是契约门——两个门各管一段。
    """
    import step01_script_gen.main as m

    plan = _plan_with_hook()
    assert o.plan_acceptance_problems(plan, _SRC) == []
    _chapter(monkeypatch, tmp_path)
    calls = _fake_audit(monkeypatch)
    script = _script_for(plan)                       # shot1 声明 D1 但旁白为空
    _line = pf.plan_dialogue_lines(plan)[0]
    script["shots"][0]["narration_segment"] = ""     # 声明镜里没有这句
    script["shots"][0]["subtitle"] = ""
    script["shots"][1]["narration_segment"] = _line  # 声音挪到 shot2（台词门因此能过）
    script["shots"][1]["subtitle"] = _line
    _ok, _broken = pf.check_script_dialogue_integrity(script, plan)
    assert _ok, "前提：这句确实在成片里（台词门不是拦它的那道门）"
    _on_disk, summary = m._commit_step01_outputs(script, _SRC, plan=plan)
    assert summary["status"] == "BLOCK", summary
    assert summary["repair_outcome"] == "event_contract_broken", summary
    assert "plan_refs_line_not_in_shot" in summary["detail"], summary["detail"]
    assert calls["n"] == 0


# ---------------------------------------------------------------------------
# E02 反例⑥：BLOCK **不许动**正式三件套（盘上摘要对照）
# ---------------------------------------------------------------------------
def test_e02_a_block_leaves_the_three_official_artifacts_byte_identical(tmp_path, monkeypatch):
    """同一目录先 COMMITTED 再 BLOCK：三份正式产物的**盘上 sha1 一个都不许变**.

    判别性：让 BLOCK 分支顺手写一次 `step01_audit.json` ⇒ 转红。
    """
    import step01_script_gen.main as m
    from shared.quality_gate import STEP01_ARTIFACT_FILES

    _svc, plan = _chain(monkeypatch, _FIX_DECL)
    _chapter(monkeypatch, tmp_path)
    _fake_audit(monkeypatch)
    _s0, s0 = m._commit_step01_outputs(_script_for(plan), _SRC, plan=plan)
    assert s0["status"] == "COMMITTED", s0
    _before = {n: hashlib.sha1((tmp_path / n).read_bytes()).hexdigest()
               for n in STEP01_ARTIFACT_FILES}

    _stale = _script_for(plan, plan_id="deadbeef1234")     # 旧身份 ⇒ 契约门 BLOCK
    _s1, s1 = m._commit_step01_outputs(_stale, _SRC, plan=plan)
    assert s1["status"] == "BLOCK", s1

    _after = {n: hashlib.sha1((tmp_path / n).read_bytes()).hexdigest()
              for n in STEP01_ARTIFACT_FILES}
    assert _after == _before, "BLOCK 不许改动正式三件套：%s" % {
        n: (_before[n], _after[n]) for n in _before if _before[n] != _after[n]}
    assert json.loads((tmp_path / "step01_commit.json").read_text(
        encoding="utf-8"))["status"] == "BLOCK", "manifest 应显式记为 BLOCK"
    assert not (tmp_path / "_staged").exists(), "BLOCK 后不许留下 staged 目录"
    with pytest.raises(QualityGateError):
        verify_step01_commit_manifest(tmp_path, _stale)


# ---------------------------------------------------------------------------
# E02 反例⑦：**终稿归一之后**才破损的契约（判据看的是 finalize 之后那一份）
# ---------------------------------------------------------------------------
def test_e02_a_contract_broken_by_post_finalize_changes_still_blocks(tmp_path, monkeypatch):
    """提交前那份稿**过门**；`finalize` 之后同一份稿破损 ⇒ 提交点仍 BLOCK.

    判别性：把提交点的契约门挪到 `_finalize` **之前**（或删掉它）⇒ 转红。
    """
    import step01_script_gen.main as m

    _svc, plan = _chain(monkeypatch, _FIX_DECL)
    _chapter(monkeypatch, tmp_path)
    calls = _fake_audit(monkeypatch)
    _real_fin = m.finalize_step01_script

    def _fin(script, *, novel_text="", label="落盘前", repair_effects=None):
        # §14 P3：提交路径会给 finalize 带一个可选的**只读效果视图**（修复轮通过
        # 验收的布局增删）。本用例要钉的是「finalize 之后才破损 ⇒ 不许烧审计」，
        # 与效果视图无关——照原样透传即可（本用例的效果视图恒为 None）。
        _s, _errs = _real_fin(script, novel_text=novel_text, label=label,
                              repair_effects=repair_effects)
        for _sh in _s.get("shots") or []:             # 注入：归一之后声明没了
            _sh.pop("plan_refs", None)
        return _s, _errs

    monkeypatch.setattr(m, "finalize_step01_script", _fin)
    script = _script_for(plan)
    _ok, _viol, _decl = pf.check_event_contract(
        copy.deepcopy(script), plan, require_declaration=True)
    assert _ok and _decl is True and _viol == [], ("前提：提交前这份稿是过门的", _viol)
    _on_disk, summary = m._commit_step01_outputs(script, _SRC, plan=plan)
    assert summary["status"] == "BLOCK", summary
    assert summary["repair_outcome"] == "event_contract_broken", summary
    assert "plan_refs_missing" in summary["detail"], summary["detail"]
    assert calls["n"] == 0, "finalize 之后才破损 ⇒ 同样不许烧审计"


# ---------------------------------------------------------------------------
# ④ 联合承载贯通到**提交**：hybrid 稿的 `presentation` 关系必须进落盘稿
# ---------------------------------------------------------------------------
def _hybrid_joint_plan():
    """合法 hybrid 计划：`f01` 由**画面 + 文字卡**联合承载（复核 ④ 要贯通的形态）."""
    from shared import presentation_contract as pc
    p = _plan(narration_lines=[], dialogue_lines=[])
    p["shot_beats"] = [
        "节拍：甲板上有风｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _LINE,
        "节拍：乙｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _LINE2,
        "节拍：丙｜本镜信息目的：推进｜原文依据：“%s”｜载体：画面" % _LINE2]
    p["required_facts"] = ["甲板上有风，帆布猎猎作响｜原文证据：“%s”｜承载：B1/画面" % _LINE]
    p["plan_contract_version"] = pc.PLAN_CONTRACT_VERSION_HYBRID
    p["presentation"] = {
        "schema": pc.PRESENTATION_SCHEMA, "mode": pc.MODE_HYBRID,
        "fact_uids": ["f01"], "beat_uids": ["b01", "b02", "b03"],
        "dialogue_uids": [], "narration_uids": [],
        "beat_events": [["甲"], ["乙"], ["丙"]],
        "screen_cards": [{"card_id": "c01", "beat_uid": "b01",
                          "template": "background", "text": _LINE,
                          "placement": "info_panel"}],
        "fact_bindings": [{"fact_uid": "f01",
                           "carriers": [{"kind": "visual", "beat_uid": "b01"},
                                        {"kind": "screen_card", "card_id": "c01"}]}],
        "hook_bindings": {"spoken_uids": [], "carriers": []}}
    return p


def test_the_joint_carrier_reaches_the_committed_script(tmp_path, monkeypatch):
    """复核 2026-10-06（第八轮）④：联合引用要经**提交**仍然完整.

    此前只到"应用/投影/读回"，没有证明提交稿把多载体关系带下去。这条走真实提交入口，
    断言落盘稿的 `presentation.fact_bindings` **两个载体都在**、卡片定义在、且镜头绑定
    指向承载它的那一镜。
    """
    import copy as _copy
    import step01_script_gen.main as m
    from shared import presentation_contract as pc

    _hp = _hybrid_joint_plan()
    # **先经真实投影往返**（计划 → 视图 → 计划）：联合承载必须挺过 `plan_from_view`。
    # 少了这一步，这条用例只证明"提交层照抄我手写的字典"，证明不了投影保真。
    _view, _ = pc.view_from_plan(_hp)
    assert len(_view["fact_bindings"][0]["carriers"]) == 2, "视图侧就不是联合承载"
    _hp = pc.plan_from_view(json.loads(json.dumps(_view)))
    assert isinstance(_hp, dict), "投影失败：%r" % (_hp,)
    assert len(_hp["presentation"]["fact_bindings"][0]["carriers"]) == 2, \
        "投影把联合承载压成了单载体：%s" % _hp["presentation"]["fact_bindings"]
    # R4：新模式缺核验记录不能判通过——这里按同一份身份造一条**完整支持**的记录
    from shared import content_verification as cv
    _view, _ = pc.view_from_plan(_hp)
    from step01_script_gen.content_verify import novel_identity as _novel_id
    _rec = {"schema": cv.SCHEMA_VERIFICATION, "status": "ok",
            "rule_version": cv.DEFAULT_RULE_VERSION,
            "plan_digest": cv.plan_digest_of(_hp),
            # 记录必须绑**本次**原文身份（复核 2026-10-07 三轮 ②）
            "novel_digest": _novel_id(_SRC)["digest"],
            "source_support": [], "facts": [], "voice_units": []}
    vh.stamp_basis(_rec, _view)
    for _f in _view["frozen_facts"]:
        _span = str(_f.get("evidence") or "")
        assert _span in _SRC, "夹具引文不在原文里：%r" % _span
        _rec["source_support"].append({"fact_uid": _f["uid"], "verdict": cv.VERDICT_SUPPORTED,
                                       "evidence_spans": [_span] if _span else [], "reason": ""})
        _txts = [t for t in cv.all_carrier_texts(_view, _f["uid"]) if t]
        _rec["facts"].append({"fact_uid": _f["uid"], "verdict": cv.VERDICT_SUPPORTED,
                              "support_spans": [max(_txts, key=len)] if _txts else [],
                              "missing": "", "suggested_carriers": [],
                              "obligations": vh.obligations_for_result(
                                  _view, _f["uid"], cv.VERDICT_SUPPORTED, _txts)})
    _hp["plan_verification"] = _rec
    assert o.plan_acceptance_problems(_hp, _SRC) == [], \
        o.plan_acceptance_problems(_hp, _SRC)

    _chapter(monkeypatch, tmp_path)
    _fake_audit(monkeypatch)
    script = _script_for(_hp)
    # 生产稿本来就带 `chapter_outline`（提交层与下游都按 `script["chapter_outline"]` 读计划，
    # 见 `main.py` 的 `plan=script.get("chapter_outline")`）
    script["chapter_outline"] = _copy.deepcopy(_hp)
    _disk, summary = m._commit_step01_outputs(script, _SRC, plan=_copy.deepcopy(_hp))
    assert summary["status"] == "COMMITTED", summary
    assert not summary["blocking"], summary["blocking"]

    _p = json.loads((tmp_path / "script_result.json").read_text(encoding="utf-8"))
    _plan = _p["chapter_outline"]
    assert str(_plan.get("plan_contract_version")) == str(pc.PLAN_CONTRACT_VERSION_HYBRID)
    _pres = _plan["presentation"]
    _b = {x["fact_uid"]: x["carriers"] for x in _pres["fact_bindings"]}
    assert len(_b["f01"]) == 2, "提交稿把联合承载压成了单载体：%s" % _b
    assert {c["kind"] for c in _b["f01"]} == {"visual", "screen_card"}
    assert [c["card_id"] for c in _pres["screen_cards"]] == ["c01"]
    assert _pres["screen_cards"][0]["text"] == _LINE, "卡片正文被改写"
    assert _pres["narration_uids"] == [] and _plan.get("narration_lines") == []
