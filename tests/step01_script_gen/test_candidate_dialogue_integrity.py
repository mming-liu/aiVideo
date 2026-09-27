# -*- coding: utf-8 -*-
"""候选层/提交层**台词完整性硬门**的贯通行为（2026-09-26，工作包 sunny-kindling-wilkinson）.

覆盖三条断言面（审核约束⑤预算语义 + 决策点 1/2）：
  1. 候选层：破损候选不进池（消耗尝试名额、不补取不扩预算）；
     全部候选破损 → `DialogueIncompleteError`（带 broken 明细）+ 诊断落
     `step01_plan_infeasible.json`（`conclusion=dialogue_incomplete`，独立于
     `replan_incomplete`，消费者不得触发计划重选）。
  2. 提交层：选中稿破损 → BLOCK `repair_outcome=dialogue_truncated`（与
     `locked_changed`/零改善区分），**零审计/修复调用**（修复按锁定契约不可修）。
  3. 提交层放行：完整稿照常进入审计（门不误伤）。

门判据本身的单测在 `test_plan_feasibility.py`（含 09-26 实损形态反例）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_LONG_LINE = ("且慢，我刚刚收到城里传来的消息，有些生意上的事情急需公子爷赶回去处理，"
              "公子如今酩酊大醉，难以起身，你们来的正好，去把公子的马车赶到水边来，"
              "我和听香姑娘要扶公子马上回城。")
_PLAN = {"dialogue_lines": [_LONG_LINE], "shot_beats": ["节拍一", "节拍二"]}


def _mk_script(*narrations):
    """最小稿：narrations 逐镜给旁白（与 test_plan_feasibility 同构）."""
    return {"shots": [{"index": i + 1, "narration_segment": n,
                       "ff_desc": "f", "lf_desc": "l", "prompt": "p",
                       "duration_sec": 3.0}
                      for i, n in enumerate(narrations)],
            "total_duration": 3.0 * len(narrations)}


def _broken_script() -> dict:
    """09-26 02:30 实损形态：长台词整句可拼回，但首镜句中截断（"…急需"戛然而止）.

    三镜覆盖全句（shot1 碎片 + shot2 重复前缀续接 + shot3 尾句）——这正是
    `cross_shot_split` 分类对应的形态：内容没丢、碎片必然被审计报 truncated_text。
    """
    return {"shots": [
        {"index": 1, "narration_segment": "张十三道：且慢，我刚刚收到城里传来的消息，"
                                          "有些生意上的事情急需",
         "ff_desc": "f", "lf_desc": "l", "prompt": "p", "duration_sec": 3.0},
        {"index": 2, "narration_segment": "张十三道：公子爷赶回去处理，公子如今酩酊大醉，"
                                          "难以起身，你们来的正好",
         "ff_desc": "f", "lf_desc": "l", "prompt": "p", "duration_sec": 3.0},
        {"index": 3, "narration_segment": "张十三道：去把公子的马车赶到水边来，"
                                          "我和听香姑娘要扶公子马上回城",
         "ff_desc": "f", "lf_desc": "l", "prompt": "p", "duration_sec": 3.0},
    ], "total_duration": 9.0}


def _good_script() -> dict:
    """完整稿：长台词逐字在单镜内（09-25 22:10 那轮 5/5 的形态）."""
    return {"shots": [
        {"index": 1, "narration_segment": "张十三道：" + _LONG_LINE,
         "ff_desc": "f", "lf_desc": "l", "prompt": "p", "duration_sec": 12.0},
    ], "total_duration": 12.0}


def _patch_candidate_pipeline(monkeypatch, scripts: list, tmp_path):
    """把候选管线的外部依赖钉到可控桩：LLM 调用/解析/评分全部替掉，门走真实现."""
    import step01_script_gen.main as m
    import step01_script_gen.plan_feasibility as pf
    import shared.config as cfg

    # 计划门（扇出前）已经在上游测过——这里直接放行
    monkeypatch.setattr(pf, "check_plan_before_generation", lambda *a, **k: None)
    # 单模型、两次尝试预算；重试 sleep 归零
    monkeypatch.setattr(cfg, "SCRIPT_CANDIDATE_TOPK", 1)
    monkeypatch.setattr(cfg, "SCRIPT_CANDIDATE_MODELS", ["test-model"])
    monkeypatch.setattr(m, "SCRIPT_PARSE_RETRIES", len(scripts))
    monkeypatch.setattr(m, "SCRIPT_PARSE_RETRY_SLEEP_SEC", 0)
    # LLM 调用与解析桩：按次序吐出预置稿
    monkeypatch.setattr(m, "gen_spy_script", lambda *a, **k: "RAW")
    monkeypatch.setattr(m, "parse_response",
                        lambda raw, novel_text=None: scripts.pop(0))
    # 评分桩：破损候选在评分**之前**就被门拦下，评分只服务于合格候选
    monkeypatch.setattr(m, "_score_script",
                        lambda s, n: {"total": 90.0, "detail": "测试", "final_score": 90.0})
    monkeypatch.setattr(m, "_candidate_quality_summary", lambda s, n: {})
    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    return m, pf


def test_candidate_pool_all_broken_fails_with_broken_details(tmp_path, monkeypatch):
    """全部候选破损 → `DialogueIncompleteError` + 诊断落盘（决策点 1/2）.

    不回退"最少破损"候选、不静默落回历史快照；异常携带每轮 broken 明细；
    诊断用独立 `conclusion=dialogue_incomplete`。
    """
    m, pf = _patch_candidate_pipeline(
        monkeypatch, [_broken_script(), _broken_script()], tmp_path)

    with pytest.raises(pf.DialogueIncompleteError) as ei:
        m._generate_script_candidates("原文", None, None, outline=dict(_PLAN))

    assert len(ei.value.broken_batches) == 2, "每个破损候选的明细都要带上"
    first = ei.value.broken_batches[0][0]
    assert first["reason"] == "cross_shot_split" and "且慢" in first["line"]

    diag = json.loads((tmp_path / "step01_plan_infeasible.json").read_text(encoding="utf-8"))
    assert diag["conclusion"] == "dialogue_incomplete"
    assert diag["conclusion"] != "replan_incomplete", "不得触发计划重选语义"
    assert diag["total_lines"] == 1


def test_broken_candidate_excluded_good_one_enters_pool(tmp_path, monkeypatch):
    """第 1 轮破损、第 2 轮完整 → 完整稿进池（破损候选只消耗当轮名额）."""
    m, pf = _patch_candidate_pipeline(
        monkeypatch, [_broken_script(), _good_script()], tmp_path)

    result = m._generate_script_candidates("原文", None, None, outline=dict(_PLAN))

    assert len(result) == 1
    score, script = result[0]
    assert script["shots"][0]["narration_segment"].startswith("张十三道：且慢")
    assert "急需公子爷赶回去处理" in script["shots"][0]["narration_segment"], \
        "进池的必须是完整稿"


def test_commit_layer_blocks_broken_script_without_any_audit_call(tmp_path, monkeypatch):
    """提交层辅门：选中稿破损 → BLOCK `dialogue_truncated`，零审计/修复调用.

    这正是 09-26 死锁的预防面：破损稿不再有机会走进 `locked_changed`。
    """
    import step01_script_gen.main as m

    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    monkeypatch.setattr(m, "OUTPUT_FILE", tmp_path / "script_result.json")
    audit_calls: list = []

    def _no_audit(*a, **k):
        audit_calls.append(1)
        raise AssertionError("台词破损时不得进入审计/修复轮")

    monkeypatch.setattr(m, "_run_final_semantic_audit", _no_audit)
    script, summary = m._commit_step01_outputs(_broken_script(), "原文", plan=dict(_PLAN))

    assert summary["status"] == "BLOCK"
    assert summary["repair_outcome"] == "dialogue_truncated", summary.get("detail")
    assert "台词完整性硬门" in summary["detail"]
    assert "1/1 句破损" in summary["detail"]
    assert not audit_calls, "零审计调用（破损不是修复可解，不烧审计额度）"
    assert summary["blocking"][0]["code"] == "dialogue_truncated"


def test_commit_layer_passes_complete_script_to_audit(tmp_path, monkeypatch):
    """放行面：完整稿通过台词门、照常进入审计（门不误伤）.

    用哨兵异常在"进入审计"处截断——提交后续流程（quality/staged/manifest）不在
    本用例范围（它们有自己的测试面）。
    """
    import step01_script_gen.main as m

    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    monkeypatch.setattr(m, "OUTPUT_FILE", tmp_path / "script_result.json")

    class _ReachedAudit(Exception):
        pass

    def _audit_marker(*a, **k):
        raise _ReachedAudit()

    monkeypatch.setattr(m, "_run_final_semantic_audit", _audit_marker)
    with pytest.raises(_ReachedAudit):
        m._commit_step01_outputs(_good_script(), "原文", plan=dict(_PLAN))


def test_best_guard_excludes_broken_snapshot_and_keeps_complete_one(tmp_path, monkeypatch):
    """二审 H2①：快照池 1 破损 + 1 完整 → 仅完整快照参与排名（破损排除留痕）.

    分数构造：破损快照 99 > 完整快照 90 > 新稿 50——若门失效，破损快照会以最高分
    被选中；门生效时它被排除（meta 留痕），完整快照胜出。
    """
    import step01_script_gen.main as m

    out_file = tmp_path / "script_result.json"
    monkeypatch.setattr(m, "OUTPUT_FILE", out_file)
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)

    _broken = _broken_script()
    _broken["_snap"] = "broken"
    _complete = _good_script()
    _complete["shots"] = _complete["shots"] + [
        {"index": 2, "prompt": "p", "duration_sec": 2.0}]  # 追加纯画面镜：revision 与新稿不同
    _complete["_snap"] = "complete"
    (snap_dir / "script_result_20260101_000001.json").write_text(
        json.dumps(_broken, ensure_ascii=False), encoding="utf-8")
    (snap_dir / "script_result_20260101_000002.json").write_text(
        json.dumps(_complete, ensure_ascii=False), encoding="utf-8")

    def _fake_score(script, novel_text=""):
        _n = len(script.get("shots") or [])
        return {"total": {3: 99.0, 2: 90.0}.get(_n, 50.0), "detail": "mock"}

    monkeypatch.setattr(m, "_score_script", _fake_score)
    monkeypatch.setattr(m, "_candidate_quality_summary", lambda s, n: {
        "quality_checked": True, "blocking_risk_count": 0, "integrity_ok": True,
        "structural_ok": True, "structural_issues": []})

    selected, meta = m._cross_run_best_guard(_good_script(), "原文", plan=dict(_PLAN))

    assert len(meta.get("dialogue_broken_snapshots") or []) == 1, meta
    assert "script_result_20260101_000001.json" in meta["dialogue_broken_snapshots"][0]
    assert selected is not None and len(selected["shots"]) == 2, \
        "破损快照（99 分）必须被门排除；完整快照（90 分）胜出"
    assert selected.get("_snap") == "complete"


def test_dialogue_integrity_chunyao_and_zhuayao_coexist():
    """三审 P1-2（审核原例）：计划含原词"春药"、稿件被合规净化成"拿药"——同句共存.

    还原方向（safe→orig）对此本质歧义（整段还原会把"抓药"一并改成"抓春药"）；
    门匹配已改为**净化方向**：计划句与稿件同过 `sanitize_narration` 再比对，
    替换永不错位。净化方向即产出稿件文本的那个函数，本例必须放行。
    """
    import step01_script_gen.plan_feasibility as pf

    plan = {"dialogue_lines": ["拿春药，再去抓药。"], "shot_beats": ["b"]}
    ok, broken = pf.check_script_dialogue_integrity(
        _mk_script("张十三道：拿药，再去抓药。"), plan)
    assert ok, broken


def test_commit_gate_exception_fails_closed(tmp_path, monkeypatch):
    """三审 P1-1：门自身异常 = 无法证明台词完整 → **fail-closed BLOCK**，不放行.

    缓存路径只靠提交层复核；门失效时放行 = 裸奔进审计/提交（与硬门"失败即停"
    冲突）。异常必须产出明确 BLOCK 与诊断（detail 带 fail-closed 与异常原文）。
    """
    import step01_script_gen.main as m
    import step01_script_gen.plan_feasibility as pf

    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    monkeypatch.setattr(m, "OUTPUT_FILE", tmp_path / "script_result.json")

    def _boom(*a, **k):
        raise RuntimeError("门内部构造异常")

    monkeypatch.setattr(pf, "check_script_dialogue_integrity", _boom)
    audit_calls: list = []

    def _no_audit(*a, **k):
        audit_calls.append(1)
        raise AssertionError("门异常时不得进入审计/修复轮")

    monkeypatch.setattr(m, "_run_final_semantic_audit", _no_audit)
    script, summary = m._commit_step01_outputs(_good_script(), "原文", plan=dict(_PLAN))

    assert summary["status"] == "BLOCK"
    assert summary["repair_outcome"] == "dialogue_gate_error", summary.get("detail")
    assert summary["blocking"][0]["code"] == "dialogue_gate_error", \
        "四审 P2：门自身异常的 blocking 代码不得与稿件截断共用"
    assert "fail-closed" in summary["detail"] and "门内部构造异常" in summary["detail"]
    assert not audit_calls


def test_mixed_failures_counted_separately(tmp_path, monkeypatch):
    """三审 P2-4：破损与生成/解析失败**分开计数**，混合失败不得写成"全部破损"."""
    import step01_script_gen.main as m
    import step01_script_gen.plan_feasibility as pf
    import shared.config as cfg

    monkeypatch.setattr(pf, "check_plan_before_generation", lambda *a, **k: None)
    monkeypatch.setattr(cfg, "SCRIPT_CANDIDATE_TOPK", 1)
    monkeypatch.setattr(cfg, "SCRIPT_CANDIDATE_MODELS", ["test-model"])
    monkeypatch.setattr(m, "SCRIPT_PARSE_RETRIES", 2)
    monkeypatch.setattr(m, "SCRIPT_PARSE_RETRY_SLEEP_SEC", 0)
    # 第 1 次尝试：LLM 调用失败（RuntimeError）；第 2 次：台词破损
    _gen_state = {"n": 0}

    def _gen(novel_text, *a, **k):
        _gen_state["n"] += 1
        if _gen_state["n"] == 1:
            raise RuntimeError("mock LLM 失败")
        return "RAW"

    monkeypatch.setattr(m, "gen_spy_script", _gen)
    monkeypatch.setattr(m, "parse_response",
                        lambda raw, novel_text=None: _broken_script())
    monkeypatch.setattr(m, "_score_script",
                        lambda s, n: {"total": 90.0, "detail": "测试", "final_score": 90.0})
    monkeypatch.setattr(m, "_candidate_quality_summary", lambda s, n: {})
    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)

    with pytest.raises(pf.DialogueIncompleteError) as ei:
        m._generate_script_candidates("原文", None, None, outline=dict(_PLAN))

    assert "1 次生成/解析失败" in str(ei.value), "混合失败必须分开计数"
    assert len(ei.value.broken_batches) == 1
    diag = json.loads((tmp_path / "step01_plan_infeasible.json").read_text(encoding="utf-8"))
    assert diag["conclusion"] == "dialogue_incomplete", \
        "五审：混合失败含真实破损 → 生成稿完整性结论，门异常计数留在 attempt_stats"
    assert diag["attempt_stats"] == {"total": 2, "dialogue_broken": 1,
                                     "gate_errors": 0, "contract_broken": 0,
                                     "other_failed": 1}


# ---------------------------------------------------------------------------
# v6.135 四审（2026-09-26 第三轮复核）：净化异常传播 + 门故障独立归因
# ---------------------------------------------------------------------------

def test_gate_raises_when_sanitize_fails(monkeypatch):
    """四审 P1：净化器抛错时门必须**向外传播**，不得退回原样匹配静默放行.

    泄漏形态（旧实现）：两侧原样恰好相同 → `except` 退回原样形态照样命中①
    → 门返回通过，提交层 fail-closed 分支收不到任何异常。修复后 sanitize 异常
    必须穿透 `check_script_dialogue_integrity`，由各调用方 fail-closed 接住。
    """
    import step01_script_gen.plan_feasibility as pf
    import shared.content_safety as cs

    plan = {"dialogue_lines": ["拿药，再去抓药。"], "shot_beats": ["b"]}
    script = _mk_script("张十三道：拿药，再去抓药。")  # 两侧原样相同=旧回退路径必放行

    def _boom(text):
        raise RuntimeError("sanitize 故障")

    monkeypatch.setattr(cs, "sanitize_narration", _boom)
    with pytest.raises(RuntimeError, match="sanitize 故障"):
        pf.check_script_dialogue_integrity(script, plan)


def test_commit_gate_sanitize_failure_blocks_as_gate_error(tmp_path, monkeypatch):
    """四审 P1 回归（端到端）：净化函数抛错 → 提交层 fail-closed BLOCK，独立代码.

    不 monkeypatch 门本身——从 `sanitize_narration` 抛错一路穿透门到提交层；
    断言 repair_outcome / blocking[0].code = dialogue_gate_error（四审 P2：
    检查故障不得统计成 dialogue_truncated 稿件截断），且零审计/修复调用。
    """
    import step01_script_gen.main as m
    import shared.content_safety as cs

    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    monkeypatch.setattr(m, "OUTPUT_FILE", tmp_path / "script_result.json")

    def _boom(*a, **k):
        raise RuntimeError("净化器故障")

    monkeypatch.setattr(cs, "sanitize_narration", _boom)
    audit_calls: list = []

    def _no_audit(*a, **k):
        audit_calls.append(1)
        raise AssertionError("门异常时不得进入审计/修复轮")

    monkeypatch.setattr(m, "_run_final_semantic_audit", _no_audit)
    script, summary = m._commit_step01_outputs(_good_script(), "原文", plan=dict(_PLAN))

    assert summary["status"] == "BLOCK"
    assert summary["repair_outcome"] == "dialogue_gate_error", summary.get("detail")
    assert summary["blocking"][0]["code"] == "dialogue_gate_error"
    assert "fail-closed" in summary["detail"] and "净化器故障" in summary["detail"]
    assert not audit_calls


def test_candidate_gate_error_counted_separately(tmp_path, monkeypatch):
    """四审 P1 + 五审：候选层门异常 fail-closed 分账——不进池、消耗名额、独立归因.

    净化器故障 × 全部尝试 → 无合格候选：显式失败；attempt_stats 记
    gate_errors=1 / dialogue_broken=0（检查故障不得混入台词破损计数）；
    五审：异常类型 `DialogueGateError`（父类仍可捕获）、诊断 conclusion=
    dialogue_gate_error——顶层结论不得把检查故障归为稿件不完整。
    """
    m, pf = _patch_candidate_pipeline(monkeypatch, [_good_script()], tmp_path)
    import shared.content_safety as cs

    def _boom(*a, **k):
        raise RuntimeError("净化器故障")

    monkeypatch.setattr(cs, "sanitize_narration", _boom)

    with pytest.raises(pf.DialogueGateError) as ei:
        m._generate_script_candidates("原文", None, None, outline=dict(_PLAN))
    assert isinstance(ei.value, pf.DialogueIncompleteError), \
        "子类必须保持父类可捕获（既有 except DialogueIncompleteError 不受影响）"
    assert "台词门异常" in str(ei.value)
    diag = json.loads((tmp_path / "step01_plan_infeasible.json").read_text(encoding="utf-8"))
    assert diag["conclusion"] == "dialogue_gate_error", \
        "五审：纯门故障的诊断结论不得写成 dialogue_incomplete"
    assert diag["attempt_stats"] == {"total": 1, "dialogue_broken": 0,
                                     "gate_errors": 1, "contract_broken": 0,
                                     "other_failed": 0}


def test_best_guard_gate_error_excludes_snapshot(tmp_path, monkeypatch):
    """四审 P1：BEST-GUARD 快照过滤遇门异常 → 该快照无法证明台词完整 → 排除留痕.

    快照 99 分（会赢过任何对手）但门故障——fail-closed：不选中，不静默当通过。
    """
    import step01_script_gen.main as m
    import step01_script_gen.plan_feasibility as pf

    monkeypatch.setattr(m, "OUTPUT_FILE", tmp_path / "script_result.json")
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)

    _snap = _good_script()
    _snap["shots"] = _snap["shots"] + [{"index": 2, "prompt": "p", "duration_sec": 2.0}]
    _snap["_snap"] = "snap"
    (snap_dir / "script_result_20260101_000001.json").write_text(
        json.dumps(_snap, ensure_ascii=False), encoding="utf-8")

    def _boom(*a, **k):
        raise RuntimeError("净化器故障")

    monkeypatch.setattr(pf, "check_script_dialogue_integrity", _boom)
    monkeypatch.setattr(m, "_score_script", lambda s, n: {"total": 99.0, "detail": "mock"})
    monkeypatch.setattr(m, "_candidate_quality_summary", lambda s, n: {
        "quality_checked": True, "blocking_risk_count": 0, "integrity_ok": True,
        "structural_ok": True, "structural_issues": []})

    selected, meta = m._cross_run_best_guard(_good_script(), "原文", plan=dict(_PLAN))

    assert selected is None, "唯一快照因门异常被排除 → 不得选中"
    reasons = " ".join(meta.get("dialogue_broken_snapshots") or [])
    assert "gate_error" in reasons and "净化器故障" in reasons


def test_dump_conclusion_follows_failure_type(tmp_path):
    """五审：诊断结论随实际失败类型区分——纯门故障 ≠ 稿件不完整.

    自动归类：批次全部 gate_error → `dialogue_gate_error`（检查故障，稿件完整性
    未被判定）；含真实破损（含混合）→ `dialogue_incomplete`，混合计数留在
    attempt_stats；两种结论都不得触发计划重选语义。
    """
    import step01_script_gen.plan_feasibility as pf

    def _read():
        return json.loads(
            (tmp_path / "step01_plan_infeasible.json").read_text(encoding="utf-8"))

    # 纯门故障
    pf.dump_dialogue_incomplete(tmp_path, [[{"line": "", "reason": "gate_error: 净化器故障"}]],
                                total_lines=7,
                                attempt_stats={"total": 1, "dialogue_broken": 0,
                                               "gate_errors": 1, "other_failed": 0})
    d = _read()
    assert d["conclusion"] == d["verdict"] == "dialogue_gate_error"
    assert "检查故障" in d["note"] and "未被判定" in d["note"]
    assert d["attempt_stats"]["gate_errors"] == 1

    # 真实破损
    pf.dump_dialogue_incomplete(tmp_path, [[{"line": "且慢…", "reason": "cross_shot_split"}]],
                                total_lines=7)
    assert _read()["conclusion"] == "dialogue_incomplete"

    # 混合失败（破损 + 门异常）→ 仍 dialogue_incomplete，计数分账保留
    pf.dump_dialogue_incomplete(
        tmp_path,
        [[{"line": "且慢…", "reason": "cross_shot_split"}],
         [{"line": "", "reason": "gate_error: 净化器故障"}]],
        total_lines=7,
        attempt_stats={"total": 3, "dialogue_broken": 1, "gate_errors": 1,
                       "other_failed": 1})
    d = _read()
    assert d["conclusion"] == "dialogue_incomplete"
    assert d["attempt_stats"] == {"total": 3, "dialogue_broken": 1,
                                  "gate_errors": 1, "other_failed": 1}

    # layer 只影响 note 文案（终稿层共用本函数）
    pf.dump_dialogue_incomplete(tmp_path, [[{"line": "", "reason": "gate_error: x"}]],
                                layer="终稿层")
    assert "终稿层" in _read()["note"]


def test_best_guard_excludes_snapshot_declared_against_another_plan(tmp_path, monkeypatch):
    """P0-2（2026-09-27 复核）：带**另一版计划身份**的快照不得参加排名，并留原因.

    构造：旧计划身份快照分数最高。只按分数/台词门筛，它会被选中——而它的 `plan_refs`
    是对**另一版计划**作的声明（`insert_after`/`split` 会重排 B 编号，同号不代表同一条
    节拍）。身份门必须把它排除，让无身份的老快照（按兼容政策可参与）胜出。
    """
    import step01_script_gen.main as m

    out_file = tmp_path / "script_result.json"
    monkeypatch.setattr(m, "OUTPUT_FILE", out_file)
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)

    _stale = _good_script()
    _stale["shots"] = _stale["shots"] + [
        {"index": 2, "prompt": "p", "duration_sec": 2.0},
        {"index": 3, "prompt": "p", "duration_sec": 2.0}]
    _stale["plan_id"] = "00000000dead"          # 不是本轮计划的身份
    _legacy = _good_script()
    _legacy["shots"] = _legacy["shots"] + [
        {"index": 2, "prompt": "p", "duration_sec": 2.0}]   # 无 plan_id（老快照）
    (snap_dir / "script_result_20260101_000001.json").write_text(
        json.dumps(_stale, ensure_ascii=False), encoding="utf-8")
    (snap_dir / "script_result_20260101_000002.json").write_text(
        json.dumps(_legacy, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(m, "_score_script", lambda script, novel_text="": {
        "total": {3: 99.0, 2: 90.0}.get(len(script.get("shots") or []), 50.0),
        "detail": "mock"})
    monkeypatch.setattr(m, "_candidate_quality_summary", lambda s, n: {
        "quality_checked": True, "blocking_risk_count": 0, "integrity_ok": True,
        "structural_ok": True, "structural_issues": []})

    selected, meta = m._cross_run_best_guard(_good_script(), "原文", plan=dict(_PLAN))

    _stale_log = meta.get("stale_plan_snapshots") or []
    assert len(_stale_log) == 1, meta
    assert "script_result_20260101_000001.json" in _stale_log[0]
    assert "另一版计划" in _stale_log[0]
    assert selected is not None and len(selected["shots"]) == 2, "无身份的老快照胜出"
