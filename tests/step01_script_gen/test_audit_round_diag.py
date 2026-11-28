# -*- coding: utf-8 -*-
"""每轮审计诊断台账 + 按 run 增量日志快照（v6.112）.

**为什么需要这个台账**：正式产物 `step01_audit.json` 只保留**最后一轮**结论，
修复轮之间发生了什么无从区分——同一个 shot 从 blocking 变 advisory，可能是
「关联镜头改好了」，也可能是「同一份内容两次审计 severity 波动」。审计 payload
此前不落盘，日志只打计数与 (shot, code)，没有 issue 文本，两种解释无法区分。

断言边界（与总计划 §1.3 一致）：

  - 台账记全 轮次 / 输入 revision / 完整 issue / 模型 / 实际 provider；
  - 台账**不覆盖**三份正式产物，也**不参与**任何准入或复用判定；
  - 文件损坏 / 追加失败只是告警，不影响结论；
  - 日志快照只写本次 run 的增量（占用不再随 run 数平方增长）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_NL = bytes([10])


def _payload(script=None, *, status="ok", issues=None, revision=None, **extra):
    rec = {
        "status": status,
        "model": "fake-model",
        "error": "" if status == "ok" else "boom",
        "issues": list(issues or []),
        "audit_scope": "final_after_deterministic_fixes",
        "audit_prompt_version": "pv1",
        "audit_focus_version": "fv1",
        "audit_temperature": 0.0,
        "run_id": "run-under-test",
    }
    if revision is not None:
        rec["input_revision"] = revision
    rec.update(extra)
    return rec


def _issue(shot, severity, message="x"):
    return {"code": "model_audit_issue", "severity": severity, "shot_id": shot,
            "message": message, "field_path": "ff_desc", "frame_id": ""}


@pytest.fixture
def chapter(tmp_path, monkeypatch):
    import step01_script_gen.main as m

    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    return tmp_path


def _ledger(chapter):
    return json.loads((chapter / "step01_audit_rounds.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 台账内容
# ---------------------------------------------------------------------------
def test_ledger_records_round_revision_issues_model_and_provider(chapter):
    import step01_script_gen.main as m

    m._record_audit_diag(
        _payload(revision="rev-aaa",
                 issues=[_issue(2, "visual_risk", "首帧状态与动作冲突"),
                         _issue(5, "advisory", "建议补运镜")]),
        phase="commit_round", round_index=1, reused=False, run_id="run-1")

    rows = _ledger(chapter)
    assert len(rows) == 1
    rec = rows[0]
    assert rec["run_id"] == "run-1"
    assert rec["phase"] == "commit_round"
    assert rec["round"] == 1
    assert rec["reused"] is False
    assert rec["status"] == "ok"
    assert rec["model"] == "fake-model"
    assert rec["input_revision"] == "rev-aaa"
    assert rec["audit_prompt_version"] == "pv1"
    assert rec["audit_focus_version"] == "fv1"
    assert rec["audit_temperature"] == 0.0
    # 实际 provider 必须落盘（通道级 schema 降级正是审计 parse_failed 的根因）
    assert isinstance(rec["provider"], str)
    # 完整 issue 文本保留（判据：不是只留计数）
    assert [i["shot_id"] for i in rec["issues"]] == [2, 5]
    assert rec["issues"][0]["message"] == "首帧状态与动作冲突"
    assert rec["issue_count"] == 2
    assert rec["blocking_count"] == 1
    # JSON 往返后 tuple 变成 list
    assert rec["blocking"] == [["2", "model_audit_issue"]]
    # 两个指纹都记（判定版本 + 生成/修复提示词版本）
    from step01_script_gen.quality_issues import (PROMPT_SOURCE_FINGERPRINT,
                                                  RULE_SOURCE_FINGERPRINT)
    assert rec["rule_source_fingerprint"] == RULE_SOURCE_FINGERPRINT
    assert rec["prompt_source_fingerprint"] == PROMPT_SOURCE_FINGERPRINT


def test_ledger_appends_rounds_in_order_and_marks_reuse(chapter):
    import step01_script_gen.main as m

    m._record_audit_diag(_payload(revision="r1", issues=[_issue(2, "visual_risk")]),
                         phase="commit_round", round_index=1, run_id="run-1")
    m._record_audit_diag(_payload(revision="r1"), phase="commit_round",
                         round_index=2, reused=True, run_id="run-1")
    rows = _ledger(chapter)
    assert [r["round"] for r in rows] == [1, 2]
    assert [r["reused"] for r in rows] == [False, True]
    # 复用轮也要留痕：否则「这一轮审计说了什么」在复用路径上是空白
    assert rows[1]["input_revision"] == "r1"


def test_ledger_trims_to_the_keep_limit(chapter, monkeypatch):
    import step01_script_gen.main as m

    monkeypatch.setattr(m, "_AUDIT_DIAG_KEEP", 3)
    for i in range(1, 7):
        m._record_audit_diag(_payload(revision="r%d" % i),
                             phase="commit_round", round_index=i, run_id="run-1")
    rows = _ledger(chapter)
    assert [r["round"] for r in rows] == [4, 5, 6], "只保留最近 N 条"


def test_ledger_revision_is_computed_when_payload_lacks_it(chapter):
    import step01_script_gen.main as m
    from shared.quality_gate import compute_script_revision

    script = {"title": "t", "shots": [], "characters": [], "hook": None}
    m._record_audit_diag(_payload(status="parse_failed", error="not json"),
                         phase="mid_pipeline", round_index=0, script=script)
    rows = _ledger(chapter)
    assert rows[0]["input_revision"] == compute_script_revision(script)
    assert rows[0]["status"] == "parse_failed"
    assert rows[0]["error"] == "not json"


# ---------------------------------------------------------------------------
# v6.117（方案 A）：审计走**专用**通道时，台账必须记审计实际的 provider
# ---------------------------------------------------------------------------
def test_ledger_prefers_the_audit_channels_own_provider(chapter, monkeypatch):
    """审计专用通道 ≠ 全局 `TEXT_PROVIDER` —— 记全局值等于把归属记错."""
    import step01_script_gen.main as m

    monkeypatch.setattr(m, "_active_text_provider", lambda: "agent_plan")
    m._record_audit_diag(
        _payload(revision="r1", channel_provider="deepseek",
                 channel_model="deepseek-flash",
                 schema_deviations=["20/20 条缺 `code`（schema required）"]),
        phase="commit_round", round_index=1, run_id="run-1")

    rec = _ledger(chapter)[0]
    assert rec["provider"] == "deepseek"
    assert rec["model"] == "fake-model"      # 上游 payload 已换成实际提交模型
    assert rec["schema_deviations"] == ["20/20 条缺 `code`（schema required）"]


def test_ledger_falls_back_to_global_provider_without_a_channel(chapter, monkeypatch):
    """未配置专用通道时行为不变：退回全局 provider，偏离清单为空表."""
    import step01_script_gen.main as m

    monkeypatch.setattr(m, "_active_text_provider", lambda: "agent_plan")
    m._record_audit_diag(_payload(revision="r1"), phase="commit_round",
                         round_index=1, run_id="run-1")

    rec = _ledger(chapter)[0]
    assert rec["provider"] == "agent_plan"
    assert rec["schema_deviations"] == []


def test_ledger_records_token_usage_or_none(chapter):
    """v6.118：provider 自报用量进台账；**取不到记 None，不填估算**."""
    import step01_script_gen.main as m

    m._record_audit_diag(
        _payload(revision="r1",
                 usage={"input_tokens": 16803, "output_tokens": 9440,
                        "reasoning_tokens": 7173, "total_tokens": 26243}),
        phase="commit_round", round_index=1, run_id="run-1")
    m._record_audit_diag(_payload(revision="r2"), phase="commit_round",
                         round_index=2, run_id="run-1")

    rows = _ledger(chapter)
    assert rows[0]["usage"] == {"input_tokens": 16803, "output_tokens": 9440,
                                "reasoning_tokens": 7173, "total_tokens": 26243}
    assert rows[1]["usage"] is None, "取不到必须是 None，不得填空 dict 或估算值"


def _fake_audit_result(**kw):
    class _Fake:
        status = "ok"
        error = ""
        issues = []
        raw_text = ""
        channel_provider = "deepseek"
        channel_model = "deepseek-flash"
        schema_deviations = []
        usage = None

    for _k, _v in kw.items():
        setattr(_Fake, _k, _v)
    return _Fake()


def test_parse_failed_audit_keeps_the_raw_response_head(chapter, monkeypatch):
    """审计解析失败时**必须**留下原始响应头（2026-09-26 实跑 BLOCK 的诊断缺口）.

    那次 final audit `parse_failed` 只留下 "not valid JSON or lacks issues[]"——
    分不清"根本不是 JSON"（要改通道/提示词）与"是 JSON 但形状不符"（要改 shape 判据），
    而 `AuditResult.raw_text` 本来带着原文。成功轮不带这两个键（产物形状不变）。
    """
    import step01_script_gen.audit as audit_mod
    import step01_script_gen.main as m

    _prose = "审计结论如下：本集整体可用。" + "x" * 5000
    monkeypatch.setattr(audit_mod, "run_same_model_audit_with_status",
                        lambda *a, **k: _fake_audit_result(
                            status="parse_failed", error="not json", raw_text=_prose))
    script = {"title": "t", "shots": [], "characters": [], "hook": None}
    payload = m._run_final_semantic_audit(script, "原文", {})

    assert payload["status"] == "parse_failed"
    assert payload["raw_text_head"].startswith("审计结论如下"), payload["raw_text_head"][:40]
    assert len(payload["raw_text_head"]) == m._DIAG_RAW_HEAD_MAX, "头部要封顶，避免台账膨胀"
    assert payload["raw_text_len"] == len(_prose), "总长必须是完整长度，不是截断后的"

    monkeypatch.setattr(audit_mod, "run_same_model_audit_with_status",
                        lambda *a, **k: _fake_audit_result(status="ok", raw_text=""))
    _ok = m._run_final_semantic_audit(script, "原文", {})
    assert "raw_text_head" not in _ok and "raw_text_len" not in _ok, (
        "成功轮不得新增字段（正式产物形状不变）")


def test_ledger_carries_raw_head_only_when_the_audit_failed(chapter):
    """台账：失败轮带 `raw_text_head` / `raw_text_len`；成功轮没有这两个键."""
    import step01_script_gen.main as m

    m._record_audit_diag(_payload(status="parse_failed", error="not json",
                                  raw_text_head="不是 JSON 的开头", raw_text_len=12345),
                         phase="commit_round", round_index=1, run_id="run-1")
    _fail = _ledger(chapter)[0]
    assert _fail["raw_text_head"] == "不是 JSON 的开头" and _fail["raw_text_len"] == 12345, _fail

    m._record_audit_diag(_payload(revision="r1"), phase="commit_round",
                         round_index=2, run_id="run-1")
    _ok = _ledger(chapter)[1]
    assert "raw_text_head" not in _ok and "raw_text_len" not in _ok, _ok


# ---------------------------------------------------------------------------
# v6.134：parse_failed 有界重试的调用次数（attempts）要进产物与台账
# ---------------------------------------------------------------------------
def test_payload_carries_attempts_only_when_retried(chapter, monkeypatch):
    """重试发生 → payload 带 `attempts`；单次成功/单次失败都不带（形状不变）."""
    import step01_script_gen.audit as audit_mod
    import step01_script_gen.main as m

    script = {"title": "t", "shots": [], "characters": [], "hook": None}

    monkeypatch.setattr(audit_mod, "run_same_model_audit_with_status",
                        lambda *a, **k: _fake_audit_result(status="ok", attempts=2))
    _retried = m._run_final_semantic_audit(script, "原文", {})
    assert _retried["attempts"] == 2, "重试后成功也必须留痕（否则采样噪声隐形）"

    monkeypatch.setattr(audit_mod, "run_same_model_audit_with_status",
                        lambda *a, **k: _fake_audit_result(status="ok"))
    _ok = m._run_final_semantic_audit(script, "原文", {})
    assert "attempts" not in _ok, "单次调用不得新增字段（产物形状不变）"

    monkeypatch.setattr(audit_mod, "run_same_model_audit_with_status",
                        lambda *a, **k: _fake_audit_result(
                            status="parse_failed", error="not json", attempts=2))
    _failed = m._run_final_semantic_audit(script, "原文", {})
    assert _failed["attempts"] == 2


def test_ledger_carries_attempts_when_retried(chapter):
    """台账透传 `attempts`（payload 仅 ≠1 时携带，台账同口径）."""
    import step01_script_gen.main as m

    m._record_audit_diag(_payload(revision="r1", attempts=2),
                         phase="commit_round", round_index=1, run_id="run-1")
    m._record_audit_diag(_payload(revision="r1"),
                         phase="commit_round", round_index=2, run_id="run-1")
    rows = _ledger(chapter)
    assert rows[0]["attempts"] == 2
    assert "attempts" not in rows[1]


# ---------------------------------------------------------------------------
# v6.134.1（审核）：被重试覆盖的失败尝试留档（failed_attempts）进产物与台账
# ---------------------------------------------------------------------------
_BAD_ATTEMPT = {"attempt": 1, "raw_text_head": "# 分镜审查报告", "raw_text_len": 8,
                "usage": {"input_tokens": 1}}


def test_payload_carries_failed_attempts_only_when_present(chapter, monkeypatch):
    """重试覆盖过失败尝试 → payload 带 `failed_attempts`；否则不带（形状不变）."""
    import step01_script_gen.audit as audit_mod
    import step01_script_gen.main as m

    script = {"title": "t", "shots": [], "characters": [], "hook": None}

    # 「重试后成功」——正是此前丢证据的路径
    monkeypatch.setattr(audit_mod, "run_same_model_audit_with_status",
                        lambda *a, **k: _fake_audit_result(
                            status="ok", attempts=2, failed_attempts=[dict(_BAD_ATTEMPT)]))
    _retried = m._run_final_semantic_audit(script, "原文", {})
    assert _retried["failed_attempts"] == [_BAD_ATTEMPT]

    monkeypatch.setattr(audit_mod, "run_same_model_audit_with_status",
                        lambda *a, **k: _fake_audit_result(status="ok", attempts=1))
    _ok = m._run_final_semantic_audit(script, "原文", {})
    assert "failed_attempts" not in _ok, "无重试不得新增字段"


def test_ledger_carries_failed_attempts_when_present(chapter):
    """台账透传 `failed_attempts`（payload 仅非空时携带，台账同口径）."""
    import step01_script_gen.main as m

    m._record_audit_diag(_payload(revision="r1", attempts=2,
                                  failed_attempts=[dict(_BAD_ATTEMPT)]),
                         phase="commit_round", round_index=1, run_id="run-1")
    m._record_audit_diag(_payload(revision="r1"),
                         phase="commit_round", round_index=2, run_id="run-1")
    rows = _ledger(chapter)
    assert rows[0]["failed_attempts"] == [_BAD_ATTEMPT]
    assert "failed_attempts" not in rows[1]


def test_final_audit_payload_records_the_actual_channel_and_model(chapter, monkeypatch):
    """方案 A：产物里的 `model` 必须是**实际提交**的模型.

    调用方传的 `model` 是生成侧的名字（`deepseek-v4-flash`，plan 通道的），专用通道下
    会被覆盖成该通道的模型 ID——产物仍记旧名字就等于把归属记错。
    """
    import step01_script_gen.main as m
    import step01_script_gen.audit as audit_mod

    class _Fake:
        status = "ok"
        error = ""
        issues = []
        channel_provider = "deepseek"
        channel_model = "deepseek-flash"
        schema_deviations = ["20/20 条缺 `code`（schema required）"]

    monkeypatch.setattr(audit_mod, "run_same_model_audit_with_status",
                        lambda *a, **k: _Fake())
    script = {"title": "t", "shots": [], "characters": [], "hook": None}
    payload = m._run_final_semantic_audit(script, "原文", {},
                                          model="deepseek-v4-flash")

    assert payload["model"] == "deepseek-flash", "不得记 plan 通道的模型名"
    assert payload["channel_provider"] == "deepseek"
    assert payload["schema_deviations"] == ["20/20 条缺 `code`（schema required）"]


# ---------------------------------------------------------------------------
# v6.117（方案 A）：切换审计通道后，旧通道的结论不得被复用
# ---------------------------------------------------------------------------
def _mk_reusable_audit(script, **extra):
    """造一份「按既有判据可复用」的审计产物；extra 用于改通道字段."""
    import step01_script_gen.main as m
    from shared.config import duration_band as _db
    from step01_script_gen.audit import audit_prompt_version
    from shared.quality_gate import compute_script_revision

    payload = {
        "status": "ok",
        "input_revision": compute_script_revision(script),
        "rule_source_fingerprint": m.RULE_SOURCE_FINGERPRINT,
        "audit_scope": m.STEP01_AUDIT_SCOPE,
        "duration_band": _db(),
        "audit_prompt_version": audit_prompt_version(),
        "audit_focus_version": m._audit_focus_version(),
        "issues": [],
    }
    payload.update(extra)
    return payload


def test_reuse_refuses_an_audit_from_another_channel(chapter, monkeypatch):
    """配了专用通道 → 旧通道（无 `channel_provider`）的结论不得复用.

    否则「稿子没变 → revision 相同 → 直接拿旧通道结论盖章」，切换通道在缓存命中
    路径上完全不生效。
    """
    import step01_script_gen.main as m

    script = {"title": "t", "shots": [], "characters": [], "hook": None}
    (chapter / "step01_audit.json").write_text(
        json.dumps(_mk_reusable_audit(script)), encoding="utf-8")
    monkeypatch.setenv("AUDIT_TEXT_PROVIDER", "deepseek")
    monkeypatch.setenv("AUDIT_TEXT_MODEL", "deepseek-flash")

    assert m._reusable_final_audit(script) is None


def test_reuse_refuses_an_audit_from_the_same_provider_but_other_model(chapter, monkeypatch):
    """同名 provider、换模型 ID 同样不复用（模型 ID 也是归属的一部分）."""
    import step01_script_gen.main as m

    script = {"title": "t", "shots": [], "characters": [], "hook": None}
    (chapter / "step01_audit.json").write_text(
        json.dumps(_mk_reusable_audit(script, channel_provider="deepseek",
                                      model="deepseek-v4-flash")),
        encoding="utf-8")
    monkeypatch.setenv("AUDIT_TEXT_PROVIDER", "deepseek")
    monkeypatch.setenv("AUDIT_TEXT_MODEL", "deepseek-flash")

    assert m._reusable_final_audit(script) is None


def test_reuse_accepts_an_audit_from_the_configured_channel(chapter, monkeypatch):
    import step01_script_gen.main as m

    script = {"title": "t", "shots": [], "characters": [], "hook": None}
    (chapter / "step01_audit.json").write_text(
        json.dumps(_mk_reusable_audit(script, channel_provider="deepseek",
                                      model="deepseek-flash")),
        encoding="utf-8")
    monkeypatch.setenv("AUDIT_TEXT_PROVIDER", "deepseek")
    monkeypatch.setenv("AUDIT_TEXT_MODEL", "deepseek-flash")

    assert m._reusable_final_audit(script) is not None


def test_reuse_unchanged_when_no_dedicated_channel(chapter, monkeypatch):
    """未配专用通道 → 不加通道判据，既有复用规则逐字不变（零行为变化）."""
    import step01_script_gen.main as m

    script = {"title": "t", "shots": [], "characters": [], "hook": None}
    (chapter / "step01_audit.json").write_text(
        json.dumps(_mk_reusable_audit(script)), encoding="utf-8")
    monkeypatch.delenv("AUDIT_TEXT_PROVIDER", raising=False)
    monkeypatch.delenv("AUDIT_TEXT_MODEL", raising=False)

    assert m._reusable_final_audit(script) is not None


# ---------------------------------------------------------------------------
# 台账的边界：不碰正式产物、损坏可恢复、写失败不炸
# ---------------------------------------------------------------------------
def test_ledger_never_touches_the_official_audit_artifact(chapter):
    import step01_script_gen.main as m

    official = chapter / "step01_audit.json"
    sentinel = {"status": "ok", "issues": [], "sentinel": "do-not-touch"}
    official.write_text(json.dumps(sentinel), encoding="utf-8")

    m._record_audit_diag(_payload(revision="r1", issues=[_issue(9, "visual_risk")]),
                         phase="commit_round", round_index=1, run_id="run-1")

    assert json.loads(official.read_text(encoding="utf-8")) == sentinel
    assert (chapter / "step01_audit_rounds.json").exists()


def test_ledger_recovers_from_a_corrupt_file(chapter):
    import step01_script_gen.main as m

    (chapter / "step01_audit_rounds.json").write_text("{ not json", encoding="utf-8")
    m._record_audit_diag(_payload(revision="r1"), phase="commit_round",
                         round_index=1, run_id="run-1")
    rows = _ledger(chapter)
    assert len(rows) == 1
    assert rows[0]["round"] == 1


def test_ledger_write_failure_only_warns(chapter, monkeypatch):
    import step01_script_gen.main as m

    def _boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(m, "_read_json_list_quiet", _boom)
    m._record_audit_diag(_payload(revision="r1"), phase="commit_round",
                         round_index=1, run_id="run-1")   # 不得抛出


# ---------------------------------------------------------------------------
# 日志快照：按 run 增量
# ---------------------------------------------------------------------------
def test_log_snapshot_writes_only_the_new_bytes(tmp_path):
    import step01_script_gen.main as m

    log = tmp_path / "step01.log"
    snap = tmp_path / "snapshots"
    snap.mkdir()
    log.write_bytes(b"old-run-line" + _NL)
    m._snapshot_log_delta(log, snap / "step01_t1.log", snap)

    first = (snap / "step01_t1.log").read_bytes()
    assert b"old-run-line" in first
    assert (snap / "_log_offset").read_text(encoding="utf-8").strip() == str(log.stat().st_size)

    with log.open("ab") as fh:
        fh.write(b"this-run-line")
    m._snapshot_log_delta(log, snap / "step01_t2.log", snap)

    second = (snap / "step01_t2.log").read_bytes()
    assert b"this-run-line" in second
    assert b"old-run-line" not in second, "第二次快照只含本次 run 的增量"
    # 文件头标出字节区间——避免被误读成完整日志
    assert second.startswith(b"# step01.log delta bytes=")


def test_log_snapshot_falls_back_to_full_copy_when_log_shrank(tmp_path):
    import step01_script_gen.main as m

    log = tmp_path / "step01.log"
    snap = tmp_path / "snapshots"
    snap.mkdir()
    log.write_bytes(b"A" * 500)
    m._snapshot_log_delta(log, snap / "step01_t1.log", snap)

    log.write_bytes(b"rebuilt")          # 日志被重建 → 字节数小于记录的偏移
    m._snapshot_log_delta(log, snap / "step01_t2.log", snap)
    assert b"rebuilt" in (snap / "step01_t2.log").read_bytes()
