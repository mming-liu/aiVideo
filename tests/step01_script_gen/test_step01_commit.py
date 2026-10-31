# -*- coding: utf-8 -*-
"""唯一最终提交点 + 生产者—消费者集成（总计划阶段 4/5）.

**真实性要求**（总计划 §阶段 5 第 4 条）：producer 写的是**真实文件**，consumer
调的是**真实** `require_semantic_gate()` / `verify_step01_commit_manifest()`，
两边不各自 mock。只有文本模型调用被替换（测试不打 LLM）。

覆盖的关键断言（总计划 §阶段 5）：

  - audit 之后脚本 revision 不变；
  - staged 三件套通过后才生成 commit manifest；
  - manifest 缺失 / 旧 revision / 摘要不符 → 下游一律拒绝；
  - `quality=PASS` 但 audit 有 blocking → 整体 BLOCK 且不写三份产物；
  - audit 调用失败（超时/解析失败）≠ 0 issue → 直接 BLOCK；
  - `step01_commit.json` 状态非 COMMITTED（BLOCK）→ 下游拒绝；
  - audit 复用只在 revision + 规则指纹 + scope 三者全同且为 ok 时发生。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.quality_gate import (  # noqa: E402
    QualityGateError, compute_script_revision, require_semantic_gate,
    verify_step01_commit_manifest,
)


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
def _shot(i: int) -> dict:
    return {
        "index": i, "duration_sec": 5.0, "duration": 5.0,
        "prompt": f"[中景] 测试镜头{i}，画面各不相同",
        "ff_desc": f"第{i}镜起始画面，人物立于画左",
        "lf_desc": f"第{i}镜结束画面，人物立于画右",
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


def _script(n: int = 3) -> dict:
    return {"title": "t", "shots": [_shot(i) for i in range(1, n + 1)],
            "characters": [], "hook": None}


def _audit_payload(script: dict, *, status: str = "ok", issues=None,
                   scope: str, fingerprint: str, prompt_version: str = "",
                   focus_version: str = "") -> dict:
    import step01_script_gen.main as _m
    from shared.quality_gate import compute_script_revision as _rev
    from step01_script_gen.audit import audit_prompt_version as _apv
    return {
        "status": status,
        "model": "fake-model",
        "error": "" if status == "ok" else "boom",
        "issues": list(issues or []),
        "input_revision": _rev(script),
        "rule_source_fingerprint": fingerprint,
        "audit_scope": scope,
        # v6.111（审核 P2-5）：审计提示词/focus 版本也参与复用判定
        "audit_prompt_version": prompt_version or _apv(),
        "audit_focus_version": focus_version or _m._audit_focus_version(),
        "created_at": "2026-09-23T00:00:00",
        "run_id": "test-run",
    }


@pytest.fixture
def chapter(tmp_path, monkeypatch):
    """把 main 的 CHAPTER_DIR / OUTPUT_FILE 指到临时目录，并屏蔽外部依赖."""
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_VERSION

    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    monkeypatch.setattr(m, "OUTPUT_FILE", tmp_path / "script_result.json")
    monkeypatch.setattr(m, "SCRIPT_FILE", tmp_path / "script_result.json")
    # 确定性质量门：本文件测的是**提交时序**，逐条 checklist 有自己的测试
    monkeypatch.setattr(m, "collect_structured_issues",
                        lambda *a, **k: ([], False, RULE_VERSION))
    # 修复轮会调 LLM —— 提交时序的测试里让它「原样返回」（零改善 → 停止）
    monkeypatch.setattr(m, "_repair_shot_warnings",
                        lambda script, novel_text, **kw: script)
    monkeypatch.setattr(m, "_snapshot_run_artifacts", lambda script=None: None)
    return tmp_path


def _fake_audit(monkeypatch, payload_factory):
    """替换文本模型调用；返回调用计数容器."""
    import step01_script_gen.audit as audit_mod

    calls = {"n": 0}

    class _R:
        def __init__(self, payload):
            self._p = payload

    # v6.152：提交点会传 `deadline_ts`（整次审计的截止时刻）——桩要跟上签名，
    # 否则 29 条提交时序用例全部 TypeError。
    def fake(script_arg_novel, plan, script, *, model=None, timeout_sec=180,
             audit_focus=None, metadata=None, **kwargs):
        calls["n"] += 1
        payload = payload_factory(script, calls["n"])
        result = audit_mod.AuditResult(str(payload.get("status") or "ok"), [],
                                       str(payload.get("error") or ""))
        result.issues = []
        result.raw_text = ""
        # 把 issues 直接挂在返回对象上（`_run_final_semantic_audit` 会 to_dict）
        from step01_script_gen.quality_issues import Issue
        result.issues = [
            Issue(code=str(i.get("code")), severity="visual_risk",
                  message=str(i.get("message") or ""), shot_id=i.get("shot_id"),
                  frame_id=str(i.get("frame_id") or ""),
                  field_path=str(i.get("field_path") or ""),
                  evidence=str(i.get("evidence") or ""),
                  # v6.119：成对目标必须跟着走——生产转换器
                  # （`_audit_payload_to_issues`）也一样，漏了就会触发提交点契约门
                  repair_targets=i.get("repair_targets"), repairable=True)
            for i in (payload.get("issues") or [])
        ]
        return result

    monkeypatch.setattr(audit_mod, "run_same_model_audit_with_status", fake)
    return calls


# ---------------------------------------------------------------------------
# 提交成功路径
# ---------------------------------------------------------------------------
def test_commit_writes_three_artifacts_and_manifest(chapter, monkeypatch):
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    script = _script()
    _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))

    committed, summary = m._commit_step01_outputs(script, "")
    assert summary["status"] == "COMMITTED"

    for name in ("script_result.json", "step01_quality.json",
                 "step01_audit.json", "step01_commit.json"):
        assert (chapter / name).exists(), name

    on_disk = json.loads((chapter / "script_result.json").read_text(encoding="utf-8"))
    quality = json.loads((chapter / "step01_quality.json").read_text(encoding="utf-8"))
    audit = json.loads((chapter / "step01_audit.json").read_text(encoding="utf-8"))
    manifest = json.loads((chapter / "step01_commit.json").read_text(encoding="utf-8"))

    # 三份产物同 revision，且 manifest 也指向同一个
    rev = summary["revision"]
    assert quality["script_revision"] == rev
    assert audit["input_revision"] == rev
    assert on_disk["script_revision"] == rev
    assert manifest["script_revision"] == rev
    assert manifest["status"] == "COMMITTED"
    assert manifest["audit_scope"] == m.STEP01_AUDIT_SCOPE
    assert set(manifest["artifacts"]) == {
        "script_result.json", "step01_quality.json", "step01_audit.json"}

    # 真实 consumer：不 mock，直接读真实文件
    require_semantic_gate(chapter / "step01_quality.json",
                          chapter / "step01_audit.json", on_disk, stage="step01")
    verify_step01_commit_manifest(chapter, on_disk, stage="step01")


def test_audit_contract_fields_are_present(chapter, monkeypatch):
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))
    m._commit_step01_outputs(_script(), "")
    audit = json.loads((chapter / "step01_audit.json").read_text(encoding="utf-8"))
    for field in ("status", "model", "error", "issues", "input_revision",
                  "rule_source_fingerprint", "audit_scope"):
        assert field in audit, field
    # 诊断字段（不参与准入语义）
    assert audit.get("run_id")
    assert audit.get("created_at")


def test_revision_is_unchanged_after_the_audit(chapter, monkeypatch):
    """final audit 之后不得有任何语义写入——audit 的 revision 就是落盘的 revision."""
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    seen = {}

    def factory(s, n):
        seen["rev_at_audit"] = compute_script_revision(s)
        return _audit_payload(s, scope=m.STEP01_AUDIT_SCOPE,
                              fingerprint=RULE_SOURCE_FINGERPRINT)

    _fake_audit(monkeypatch, factory)
    _, summary = m._commit_step01_outputs(_script(), "")
    on_disk = json.loads((chapter / "script_result.json").read_text(encoding="utf-8"))
    assert seen["rev_at_audit"] == compute_script_revision(on_disk)
    assert summary["revision"] == seen["rev_at_audit"]


# ---------------------------------------------------------------------------
# 阻断路径
# ---------------------------------------------------------------------------
def test_quality_pass_but_audit_blocking_is_block(chapter, monkeypatch):
    """`step01_quality=PASS` 不足以通过——audit 有 blocking 时整体 BLOCK."""
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    blocking = [{"code": "model_audit_issue", "shot_id": "1", "frame_id": "lf",
                 "entity_id": "群演", "field_path": "frame_layouts.lf",
                 "evidence": "老农已走远", "message": "在场矛盾"}]
    _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, issues=blocking, scope=m.STEP01_AUDIT_SCOPE,
        fingerprint=RULE_SOURCE_FINGERPRINT))

    _, summary = m._commit_step01_outputs(_script(), "")
    assert summary["status"] == "BLOCK"
    # 三份产物**未被替换**
    assert not (chapter / "script_result.json").exists()
    assert not (chapter / "step01_quality.json").exists()
    # BLOCK manifest 存在且被 consumer 拒绝
    manifest = json.loads((chapter / "step01_commit.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "BLOCK"
    assert manifest["blocking_count"] == 1
    with pytest.raises(QualityGateError, match="not COMMITTED"):
        verify_step01_commit_manifest(chapter, _script(), stage="step01")


def test_audit_failure_is_block_not_zero_issues(chapter, monkeypatch):
    """调用失败 ≠ 0 issue：`status=request_failed` 必须直接 BLOCK.

    ⚠️ 这条同时钉住一个 fail-open：审计 payload 在失败时**依然是 dict**
    （只有 status 是 request_failed），所以 `if blocking or not audit` 这种写法
    会放行到提交、靠 staged 自验兜底。判据必须是**显式的 `audit_ok`**，
    且 BLOCK 原因要指向审计失败本身（而不是下游才发现的 staged 自验失败）。
    """
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, status="request_failed", scope=m.STEP01_AUDIT_SCOPE,
        fingerprint=RULE_SOURCE_FINGERPRINT))
    _, summary = m._commit_step01_outputs(_script(), "")
    assert summary["status"] == "BLOCK"
    assert "request_failed" in summary["detail"]
    assert "staged" not in summary["detail"], "失败点必须指向审计本身"
    assert not (chapter / "script_result.json").exists()
    assert not (chapter / "step01_quality.json").exists()


def test_parse_failure_is_block(chapter, monkeypatch):
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, status="parse_failed", scope=m.STEP01_AUDIT_SCOPE,
        fingerprint=RULE_SOURCE_FINGERPRINT))
    _, summary = m._commit_step01_outputs(_script(), "")
    assert summary["status"] == "BLOCK"
    assert "parse_failed" in summary["detail"]


def test_blocking_repair_loops_are_bounded(chapter, monkeypatch):
    """有 blocking 时最多走 `STEP01_AUDIT_REPAIR_MAX_ROUNDS` 个修复轮再 BLOCK.

    这里的假修复轮**每轮真的改动脚本**（否则零改善判据会提前收工——那是另一条
    用例），从而把「每轮重新审计」这条时序走满。
    """
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    rounds = {"n": 0}

    def fake_repair(script, novel_text, **kw):
        rounds["n"] += 1
        script["shots"][0]["prompt"] = f"[中景] 修复轮{rounds['n']}的改动"
        return script

    monkeypatch.setattr(m, "_repair_shot_warnings", fake_repair)
    calls = _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, issues=[{"code": "model_audit_x", "shot_id": "1",
                    "severity": "visual_risk", "message": "m",
                    "field_path": "prompt",
                    # v6.119：blocking 审计条目必须给可执行的定向修复目标，
                    # 否则提交点契约门会直接 BLOCK、根本不进修复轮（另有用例覆盖）
                    "repair_targets": [{"shot_id": "1", "field_path": "prompt"}]}],
        scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))
    _, summary = m._commit_step01_outputs(_script(), "")
    assert summary["status"] == "BLOCK"
    assert summary["audit_repair_rounds"] == m.STEP01_AUDIT_REPAIR_MAX_ROUNDS
    # 首次审计 + 每轮修复后各一次
    assert calls["n"] == 1 + m.STEP01_AUDIT_REPAIR_MAX_ROUNDS
    assert len(summary["blocking"]) == 1


def test_deterministic_quality_blocking_enters_the_repair_loop(chapter, monkeypatch):
    """确定性质量门的 blocking 也必须进修复轮、并如实写进 manifest.

    实测（第二十一次重跑）：提交循环此前只看**审计** blocking，一份带 9 条确定性
    阻塞的稿子既不进修复轮，manifest 还报 `blocking_count: 0`，失败原因写成
    「staged 自验失败」——日志与账本都在误导。
    """
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import Issue, RULE_VERSION

    # 确定性质量门永远报一条 visual_risk（模拟 kf_time_window_alignment 之类）
    def fake_collect(*a, **k):
        return ([Issue(code="kf_time_window_alignment", severity="visual_risk",
                       message="motion 时段缺关键帧", shot_id="1",
                       field_path="keyframes.*.t_sec", repairable=True)],
                False, RULE_VERSION)
    monkeypatch.setattr(m, "collect_structured_issues", fake_collect)

    rounds = {"n": 0}

    def fake_repair(script, novel_text, **kw):
        rounds["n"] += 1
        script["shots"][0]["prompt"] = f"[中景] 修复轮{rounds['n']}"
        return script

    monkeypatch.setattr(m, "_repair_shot_warnings", fake_repair)
    # 审计本身干净 → 阻塞完全来自确定性质量门
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT as _FP
    _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, scope=m.STEP01_AUDIT_SCOPE, fingerprint=_FP))
    _, summary = m._commit_step01_outputs(_script(), "")

    assert summary["status"] == "BLOCK"
    # 真的尝试过修复（不是一路走到 staged 才发现）
    assert rounds["n"] == m.STEP01_AUDIT_REPAIR_MAX_ROUNDS
    assert "staged" not in summary["detail"]
    assert "确定性质量门" in summary["detail"]
    manifest = json.loads((chapter / "step01_commit.json").read_text(encoding="utf-8"))
    assert manifest["blocking_count"] >= 1, "确定性阻塞必须计入 manifest"
    assert any(x.get("code") == "kf_time_window_alignment"
               for x in manifest["blocking"])


def test_non_executable_repair_targets_degrade_to_warning_not_early_block(
        chapter, monkeypatch):
    """v6.142（09-26 用户决定，P1-3）：坏目标条目降级为告警，不再就地掐掉整轮.

    **本用例取代 v6.119 的 `test_commit_blocks_when_an_audit_blocker_has_no_
    executable_repair_targets`**。旧口径「有一 blocking 条目没有可执行目标 → 提交点
    直接 BLOCK、修复轮一轮都不发」被 20:25 run 证伪：shot 3 的 12 条越界目标把同轮
    **完全可修的其它目标**一起掐掉（一条坏目标连坐整轮）。

    新口径：坏条目**保留 blocking**（下一轮审计照常重报），本轮不授权它、其余目标
    照常进修复轮；改不动的终局由「提交前修复轮零改善即停」给出——**终局仍是
    BLOCK**，只是不再提前连坐。判别性：旧口径下 `audit_repair_rounds == 0`。
    """
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    logs: list = []

    class _Log:
        def info(self, msg, *a, **k):
            logs.append(str(msg))

        def warn(self, msg, *a, **k):
            logs.append(str(msg))

        def error(self, msg, *a, **k):
            logs.append(str(msg))

        def verbose(self, *a, **k):
            pass

        def console(self, *a, **k):
            pass

    monkeypatch.setattr(m, "_logger", _Log())
    calls = _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, issues=[{"code": "model_audit_x", "shot_id": "1",
                    "severity": "visual_risk", "message": "m",
                    "field_path": "prompt", "repair_targets": []}],
        scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))
    _, summary = m._commit_step01_outputs(_script(), "")

    # 坏目标必须**显式**留痕（准则 7），但不再就地 BLOCK
    assert any("缺少可执行的定向修复目标" in x for x in logs), logs[:6]
    assert summary["audit_repair_rounds"] >= 1, "坏目标不得再掐掉修复轮"
    # 终局仍是 BLOCK：条目一直没被修好，零改善判据收口（失败照常显式）
    assert summary["status"] == "BLOCK", summary
    assert calls["n"] >= 1


def test_oversize_group_blocks_without_burning_a_repair_round(chapter, monkeypatch):
    """v6.122：一次改不完的联动组 → **立即 BLOCK**（0 修复轮、不再发审计）.

    旧行为是「整组跳过 + 交给最终审计」，代价是同一份稿的审计每轮重复声明同一组、
    直到轮数与审计额度耗尽——那不是显式失败，是必然失败。
    """
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    def fake_repair(script, novel_text, **kw):
        _b = kw.get("block_reasons")
        if _b is not None:
            _b.append(
                "联动组 ['shot_4', 'shot_5', 'shot_7', 'shot_8', 'shot_9', "
                "'shot_10', 'shot_11'] 共 7 镜，超过联动组上限 6 → **无法整组修复**"
                "（拆组=半条修复、删目标=漏声明）——请检查审计契约或稿件本身")
        return script

    monkeypatch.setattr(m, "_repair_shot_warnings", fake_repair)
    calls = _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, issues=[{"code": "model_audit_x", "shot_id": "1",
                    "severity": "visual_risk", "message": "m",
                    "field_path": "prompt",
                    "repair_targets": [{"shot_id": "1", "field_path": "prompt"}]}],
        scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))

    _, summary = m._commit_step01_outputs(_script(), "")

    assert summary["status"] == "BLOCK"
    assert summary["audit_repair_rounds"] == 0, "没跑修复轮就不该计一轮"
    assert calls["n"] == 1, "不得自动重发审计"
    assert "超出单轮可修上限" in summary["detail"], summary["detail"]
    assert "重跑" not in summary["detail"], "文案不得暗示原样重跑能解决"


def test_block_does_not_leave_a_stale_staged_dir(chapter, monkeypatch):
    """BLOCK 时清掉 `_staged/`——上一轮留下的 staged 副本会看起来像「权威副本」.

    实测：run 2 在 staged 自验失败时留下 `_staged/step01_quality.json`，run 3
    BLOCK 之后它仍在章节目录里。诊断证据由 BLOCK manifest + snapshots/ 承担。
    """
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    real_stage = Path(chapter) / "_staged"
    real_stage.mkdir(parents=True, exist_ok=True)
    (real_stage / "step01_quality.json").write_text("{}", encoding="utf-8")

    _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, status="request_failed", scope=m.STEP01_AUDIT_SCOPE,
        fingerprint=RULE_SOURCE_FINGERPRINT))
    _, summary = m._commit_step01_outputs(_script(), "")
    assert summary["status"] == "BLOCK"
    assert not real_stage.exists(), "BLOCK 后不得留下 staged 副本"


def test_zero_improvement_repair_stops_immediately(chapter, monkeypatch):
    """修复轮零改善 → 不再发下一轮（继续发只会重复同一批目标）."""
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    calls = _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, issues=[{"code": "model_audit_x", "shot_id": "1",
                    "severity": "visual_risk", "message": "m",
                    "field_path": "prompt",
                    # v6.119：blocking 审计条目必须给可执行的定向修复目标，
                    # 否则提交点契约门会直接 BLOCK、根本不进修复轮（另有用例覆盖）
                    "repair_targets": [{"shot_id": "1", "field_path": "prompt"}]}],
        scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))
    _, summary = m._commit_step01_outputs(_script(), "")
    assert summary["status"] == "BLOCK"
    assert summary["audit_repair_rounds"] == 1
    assert "零改善" in summary["detail"]
    assert calls["n"] == 1


def test_repair_call_failure_is_not_reported_as_no_progress(chapter, monkeypatch):
    """**修复调用失败/响应被截断**不得报成"零改善"（用户 2026-09-25）.

    09-25 实跑的真实 BLOCK 直接原因：定向修复响应 `incomplete_details.reason=max_output_tokens`
    （16000 上限**含思维链 token**）→ 整份丢弃 → 稿件一个字没变 → 提交层只看到 revision 相同，
    于是把调用失败报成"修复无进展"。这里把两类结论分开，并要求原因进**结构化台账**。
    """
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, issues=[{"code": "model_audit_x", "shot_id": "1",
                    "severity": "visual_risk", "message": "m",
                    "field_path": "prompt",
                    "repair_targets": [{"shot_id": "1", "field_path": "prompt"}]}],
        scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))

    def _fake_repair(script, novel_text, **kw):
        m._record_repair_call_failure(
            kw.get("repair_diag"), 1,
            error=("DeepSeek Responses 响应不完整 "
                   "(incomplete_details.reason=max_output_tokens)——输出 JSON 不完整，"
                   "已整份丢弃。"),
            truncated=True,
            usage={"output_tokens": 16000, "reasoning_tokens": 12000})
        return script                       # 原样返回 ⇒ revision 不变

    monkeypatch.setattr(m, "_repair_shot_warnings", _fake_repair)
    _, summary = m._commit_step01_outputs(_script(), "")
    assert summary["status"] == "BLOCK"
    assert summary["repair_outcome"] == "call_failed", summary
    assert "调用失败/响应不完整" in summary["detail"], summary["detail"]
    assert "零改善" not in summary["detail"], (
        "调用失败不得报成内容结论：%s" % summary["detail"])
    assert "max_output_tokens" in summary["detail"], "要带出具体原因"
    # BLOCK 的直接原因必须进**结构化诊断**，不能只在 step01.log 里
    rows = json.loads((Path(chapter) / "step01_repair_rounds.json").read_text(encoding="utf-8"))
    _f = [r for r in rows if r.get("kind") == "call_failed"]
    assert _f, rows
    assert _f[-1]["truncated"] is True and _f[-1]["reasoning_tokens"] == 12000, _f[-1]


def test_record_repair_call_failure_keeps_usage_and_caps_the_error():
    """调用失败记录：带 usage（含思维链 token，用于判断上限该多大）、错误文本封顶."""
    import step01_script_gen.main as m

    diag: list = []
    m._record_repair_call_failure(diag, 2, error="x" * 999, truncated=True,
                                  usage={"output_tokens": 16000, "reasoning_tokens": 11000})
    r = diag[0]
    assert r["kind"] == "call_failed" and r["repair_round"] == 2 and r["truncated"] is True
    assert r["reasoning_tokens"] == 11000 and r["output_tokens"] == 16000
    assert len(r["error"]) == 400, "长错误要截断，避免台账膨胀"
    assert m._record_repair_call_failure(None, 1, error="e") is None, "没给台账时不得报错"


def test_record_repair_call_usage_enables_the_cap_decision():
    """成功调用也要留用量——"上限够不够"要靠数字（含思维链 token），不是"这次有没有踩线"."""
    import step01_script_gen.main as m

    diag: list = []
    m._record_repair_call_usage(diag, 1, usage={"output_tokens": 15400, "reasoning_tokens": 9000,
                                               "total_tokens": 30000}, max_tokens=16000)
    r = diag[0]
    assert r["kind"] == "call_ok" and r["repair_round"] == 1
    assert r["reasoning_tokens"] == 9000 and r["output_tokens"] == 15400
    # 余量口径：output_tokens 已含思维链，只减它一次（16000-15400=600，不是 -8400）
    assert r["max_tokens"] == 16000 and m._repair_cap_margin(r) == 600, r
    assert m._record_repair_call_usage(None, 1, usage={}) is None


def test_unusable_patch_response_is_not_reported_as_no_progress(chapter, monkeypatch):
    """**调用成功但补丁不可用**不得报成"零改善"（用户 2026-09-26 复核）.

    复核指出的归类缺口：API 返回成功、但 JSON 解析失败 / 结构无效 / 没返回替换 /
    替换全被校验拒——这四条早退只打日志，提交层看不到记录 → 报 `no_progress`，
    把"收到不可用响应"说成了"修复没有进展"。这里要求它独立成态并把 reason 写进 detail。
    """
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, issues=[{"code": "model_audit_x", "shot_id": "1",
                    "severity": "visual_risk", "message": "m",
                    "field_path": "prompt",
                    "repair_targets": [{"shot_id": "1", "field_path": "prompt"}]}],
        scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))

    def _fake_repair(script, novel_text, **kw):
        _diag = kw.get("repair_diag")
        m._record_repair_call_usage(_diag, 1, max_tokens=None,
                                    usage={"output_tokens": 900, "reasoning_tokens": 400})
        m._record_repair_unusable(_diag, 1, reason="parse_failed",
                                  detail="Expecting value: line 1 column 1 (char 0)")
        return script                       # 原样返回 ⇒ revision 不变

    monkeypatch.setattr(m, "_repair_shot_warnings", _fake_repair)
    _, summary = m._commit_step01_outputs(_script(), "")
    assert summary["status"] == "BLOCK"
    assert summary["repair_outcome"] == "patch_unusable", summary
    assert "响应不可用" in summary["detail"], summary["detail"]
    assert "parse_failed" in summary["detail"], "要带出具体原因"
    assert "零改善" not in summary["detail"], (
        "响应不可用不得报成内容结论：%s" % summary["detail"])
    rows = json.loads((Path(chapter) / "step01_repair_rounds.json").read_text(encoding="utf-8"))
    _u = [r for r in rows if r.get("kind") == "patch_unusable"]
    assert _u and _u[-1]["reason"] == "parse_failed", rows


def test_call_failure_outranks_unusable_patch(chapter, monkeypatch):
    """两类记录同时存在时按 `call_failed` 报（"根本没拿到完整响应"更靠前）."""
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, issues=[{"code": "model_audit_x", "shot_id": "1",
                    "severity": "visual_risk", "message": "m",
                    "field_path": "prompt",
                    "repair_targets": [{"shot_id": "1", "field_path": "prompt"}]}],
        scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))

    def _fake_repair(script, novel_text, **kw):
        _diag = kw.get("repair_diag")
        m._record_repair_call_failure(_diag, 1, error="超时", max_tokens=None)
        m._record_repair_unusable(_diag, 1, reason="empty_patch")
        return script

    monkeypatch.setattr(m, "_repair_shot_warnings", _fake_repair)
    _, summary = m._commit_step01_outputs(_script(), "")
    assert summary["repair_outcome"] == "call_failed", summary
    assert "调用失败/响应不完整" in summary["detail"], summary["detail"]


def test_shot_id_notation_alias_does_not_trip_the_contract_gate(chapter, monkeypatch):
    """真实 BLOCK 的输入形态（v6.120）：blocking 审计把 `shot_id` 写成 `"shot1"` 必须放行.

    2026-09-25 实测：同一份提示词、temperature=0.0，第 1 轮审计写 `"1"`、第 2 轮写
    `"shot1"`（`step01_audit_rounds.json` 逐轮实拍）。旧实现只会拼出 `shot_shot1` →
    5 条 blocking **整条拒绝** → 契约门 BLOCK，两条修复轮一次没花。归一后必须正常进
    修复轮，且归一要能在日志里看见（静默归一等于没人知道输入形态变了）。
    """
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    logs: list = []

    class _Log:
        def info(self, msg, *a, **k):
            logs.append(str(msg))

        def warn(self, msg, *a, **k):
            logs.append(str(msg))

        def error(self, msg, *a, **k):
            logs.append(str(msg))

        def verbose(self, msg, *a, **k):
            pass

        def console(self, msg, *a, **k):
            pass

    monkeypatch.setattr(m, "_logger", _Log())
    _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, issues=[{"code": "model_audit_x", "shot_id": "shot1",
                    "severity": "visual_risk", "message": "m",
                    "field_path": "prompt",
                    "repair_targets": [{"shot_id": "shot1",
                                        "field_path": "prompt"}]}],
        scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))

    _, summary = m._commit_step01_outputs(_script(), "")

    assert "定向修复" not in summary["detail"], (
        "记法差异（`shot1` vs `1`）不得被当成「契约不可执行」：%s" % summary["detail"])
    assert summary["audit_repair_rounds"] == 1, "必须真的进修复轮（而不是一步没走就停）"
    assert any("镜头号记法已归一" in t and "shot1→shot_1" in t for t in logs), (
        "归一必须可见：%s" % logs)


def test_final_audit_sees_the_regenerated_hook(chapter, monkeypatch):
    """BEST-GUARD 选中 + 钩子重生时，final audit 必须针对**重生后**的钩子.

    旧实现的失守点：钩子门跑在 finalize **之后**、且 BEST-GUARD 恢复路径自己还能
    重生 hook —— 最终落盘的 hook 从未被审计覆盖。v6.103 把 hook QC 放进提交点的
    最前面，审计看到的是钩子定稿。
    """
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT
    monkeypatch.setattr(m, "HOOK_ENABLED", True)

    script = _script()
    script["hook"] = {
        "index": 0, "duration_sec": 4.0, "duration": 4.0,
        "prompt": "[中景] 旧钩子", "ff_desc": "旧钩子起始",
        "lf_desc": "旧钩子结束", "motion_desc": "0-2秒：甲；2-4秒：乙",
        "narration_segment": "", "subtitle": "",
    }

    def fake_hook_qc(s, *, novel_text, **kw):
        s["hook"]["prompt"] = "[中景] 重生后的钩子"
        return []

    monkeypatch.setattr(m, "_ensure_hook_quality", fake_hook_qc)
    seen = {}

    def factory(s, n):
        seen["hook_prompt"] = (s.get("hook") or {}).get("prompt")
        return _audit_payload(s, scope=m.STEP01_AUDIT_SCOPE,
                              fingerprint=RULE_SOURCE_FINGERPRINT)

    _fake_audit(monkeypatch, factory)
    _, summary = m._commit_step01_outputs(script, "", hook_ctx={"bank": None})
    assert summary["status"] == "COMMITTED"
    assert seen["hook_prompt"] == "[中景] 重生后的钩子"
    on_disk = json.loads((chapter / "script_result.json").read_text(encoding="utf-8"))
    assert on_disk["hook"]["prompt"] == "[中景] 重生后的钩子"


def test_disabled_hook_is_removed_from_cached_or_restored_script(chapter, monkeypatch):
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    monkeypatch.setattr(m, "HOOK_ENABLED", False)
    script = _script()
    script["hook"] = {"index": 0, "prompt": "旧钩子"}
    _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))

    _, summary = m._commit_step01_outputs(script, "")

    assert summary["status"] == "COMMITTED"
    on_disk = json.loads((chapter / "script_result.json").read_text(encoding="utf-8"))
    assert "hook" not in on_disk


# ---------------------------------------------------------------------------
# consumer 侧：manifest 的三道拒绝
# ---------------------------------------------------------------------------
def _commit_ok(chapter, monkeypatch):
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT
    _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))
    return m._commit_step01_outputs(_script(), "")


def test_consumer_rejects_missing_manifest(chapter, monkeypatch):
    _commit_ok(chapter, monkeypatch)
    (chapter / "step01_commit.json").unlink()
    on_disk = json.loads((chapter / "script_result.json").read_text(encoding="utf-8"))
    with pytest.raises(QualityGateError, match="commit manifest missing"):
        verify_step01_commit_manifest(chapter, on_disk, stage="step01")


def test_consumer_rejects_changed_artifact(chapter, monkeypatch):
    """三份产物任一在提交后被改写 → 摘要不符 → 拒绝."""
    _commit_ok(chapter, monkeypatch)
    q = json.loads((chapter / "step01_quality.json").read_text(encoding="utf-8"))
    q["advisory_count"] = 999
    (chapter / "step01_quality.json").write_text(
        json.dumps(q, ensure_ascii=False), encoding="utf-8")
    on_disk = json.loads((chapter / "script_result.json").read_text(encoding="utf-8"))
    with pytest.raises(QualityGateError, match="changed after commit"):
        verify_step01_commit_manifest(chapter, on_disk, stage="step01")


def test_consumer_rejects_stale_script_revision(chapter, monkeypatch):
    """拿着**旧稿**来消费 → manifest revision 不符 → 拒绝."""
    _commit_ok(chapter, monkeypatch)
    stale = _script()
    stale["shots"][0]["prompt"] = "被手改过的提示词"
    with pytest.raises(QualityGateError, match="revision mismatch"):
        verify_step01_commit_manifest(chapter, stale, stage="step01")


def test_consumer_rejects_stale_quality_report(chapter, monkeypatch):
    """三份产物任一旧 revision → `require_semantic_gate` 拒绝."""
    _commit_ok(chapter, monkeypatch)
    stale = _script()
    stale["shots"][0]["prompt"] = "被手改过的提示词"
    with pytest.raises(QualityGateError, match="stale"):
        require_semantic_gate(chapter / "step01_quality.json",
                              chapter / "step01_audit.json", stale,
                              stage="step01")


# ---------------------------------------------------------------------------
# audit 复用判据
# ---------------------------------------------------------------------------
def test_audit_is_reused_only_when_everything_matches(chapter, monkeypatch):
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    calls = _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))
    script = _script()
    m._commit_step01_outputs(script, "")
    assert calls["n"] == 1

    # 同一对象再提交一次：revision + 指纹 + scope 全同且 ok → 复用，不再调模型
    same = json.loads((chapter / "script_result.json").read_text(encoding="utf-8"))
    m._commit_step01_outputs(same, "")
    assert calls["n"] == 1, "三者全同且 ok 时必须复用审计"

    # scope 变了 → 必须重新审计
    audit_path = chapter / "step01_audit.json"
    payload = json.loads(audit_path.read_text(encoding="utf-8"))
    payload["audit_scope"] = "something_else"
    audit_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    m._commit_step01_outputs(same, "")
    assert calls["n"] == 2, "scope 不同必须重新审计"

    # 指纹变了 → 必须重新审计
    payload["audit_scope"] = m.STEP01_AUDIT_SCOPE
    payload["rule_source_fingerprint"] = "deadbeefdeadbeef"
    audit_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    m._commit_step01_outputs(same, "")
    assert calls["n"] == 3, "规则指纹不同必须重新审计"


def test_audit_reuse_ignores_non_ok_status(chapter, monkeypatch):
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    calls = _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))
    script = _script()
    m._commit_step01_outputs(script, "")
    assert calls["n"] == 1
    payload = json.loads((chapter / "step01_audit.json").read_text(encoding="utf-8"))
    payload["status"] = "request_failed"
    (chapter / "step01_audit.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    same = json.loads((chapter / "script_result.json").read_text(encoding="utf-8"))
    m._commit_step01_outputs(same, "")
    assert calls["n"] == 2, "失败的审计不得被复用"


# ---------------------------------------------------------------------------
# 唯一入口：三条路径共用一个提交函数
# ---------------------------------------------------------------------------
def test_all_entry_points_route_through_one_commit_function():
    """正常 / 缓存 / BEST-GUARD 三条入口都只调用 `_commit_step01_outputs`，
    且 BEST-GUARD 自己不再写盘。"""
    src = (Path(_PROJECT_ROOT) / "step01_script_gen" / "main.py").read_text(
        encoding="utf-8")
    assert src.count("_commit_step01_outputs(") >= 3, "三条入口都要调它"
    # BEST-GUARD 体内不得出现落盘调用
    start = src.index("def _cross_run_best_guard(")
    end = src.index("def main() -> None:")
    body = src[start:end]
    for forbidden in ("atomic_dump_json", "_adj(", "finalize_step01_script",
                      "_ensure_hook_quality", "_write_step01_quality_gate"):
        assert forbidden not in body, f"BEST-GUARD 不得调用 {forbidden}"
    # 正式工件只在提交点里写
    commit_start = src.index("def _commit_step01_outputs(")
    commit_end = src.index("def _prepare_hook_for_qc(")
    commit_body = src[commit_start:commit_end]
    assert '"step01_audit.json"' in commit_body
    assert '"step01_quality.json"' in commit_body


# ---------------------------------------------------------------------------
# 静态守卫：缓存路径不得引用「在它之后才赋值」的 main() 局部名
# ---------------------------------------------------------------------------
def test_cache_hit_branch_defines_every_name_it_reads():
    """缓存命中分支在 main() 里**提前返回**，不能引用后面才赋值的局部名.

    实测踩过一次：钩子禁止台词清单 `_forbidden_lines` 由钩子分支采集（位于缓存
    分支**之后**），缓存命中路径引用它 → 只要真的命中缓存就 `UnboundLocalError`。
    这里用 AST 按**行号**把这类「引用晚赋值局部名」的漏洞钉住——只比较 `main()`
    自己的局部名（模块级帮助函数永远安全），且要求赋值行严格早于读取行。
    """
    import ast

    src = (Path(_PROJECT_ROOT) / "step01_script_gen" / "main.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    main_fn = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "main")

    def _stores(node):
        """node 内所有赋值目标名 → 最小赋值行号（**不含**嵌套函数/类内部）."""
        out = {}

        def _walk(n):
            for child in ast.iter_child_nodes(n):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                      ast.ClassDef, ast.Lambda)):
                    continue
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                    out[child.id] = min(out.get(child.id, 10 ** 9), child.lineno)
                _walk(child)

        _walk(node)
        return out

    main_locals = _stores(main_fn)

    branch = None
    for node in ast.walk(main_fn):          # 分支可能嵌在 try / 其他 if 里
        if (isinstance(node, ast.If) and isinstance(node.test, ast.UnaryOp)
                and isinstance(node.test.op, ast.Not)
                and isinstance(node.test.operand, ast.Name)
                and node.test.operand.id == "prompt_changed"):
            branch = node
            break
    assert branch is not None, "未找到缓存命中分支"

    # 分支内每个 main() 局部名的**最早读取行**
    first_read = {}
    for n in ast.walk(branch):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)                 and n.id in main_locals:
            first_read[n.id] = min(first_read.get(n.id, 10 ** 9), n.lineno)

    suspicious = sorted(
        name for name, read_line in first_read.items()
        # 赋值必须**严格早于**读取；分支内自己赋值的名字不算（那也是 early assign）
        if main_locals[name] > read_line
    )
    assert not suspicious, (
        "缓存命中分支引用了 main() 里更晚才赋值的局部名（会 UnboundLocalError）："
        f"{suspicious}")


# ---------------------------------------------------------------------------
# 未决清单：BLOCK 审计 sidecar（v6.109，总计划 §1.3）
# ---------------------------------------------------------------------------
def test_blocked_audit_sidecar_is_reused_instead_of_rerolling(chapter, monkeypatch):
    """同一 revision 已有 BLOCK 审计结论时，必须复用为未决清单，不得重掷一次.

    总计划 §1.3：**同一 revision 不通过反复调用 full audit 获取一次偶然 PASS**。
    BLOCK 时正式产物不能替换（必须与另外两份同 revision），但审计确实跑过了；
    sidecar 就是留给下一次运行的未决清单。
    """
    import step01_script_gen.main as m
    from shared.json_io import atomic_dump_json

    script = _script(3)
    payload = _audit_payload(script, scope=m.STEP01_AUDIT_SCOPE,
                             fingerprint=m.RULE_SOURCE_FINGERPRINT,
                             issues=[{"code": "model_audit_issue", "shot_id": "2",
                                      "frame_id": "ff", "field_path": "ff_desc"}])
    atomic_dump_json(chapter / "step01_audit.blocked.json", payload,
                     ensure_ascii=False, indent=2)
    reused = m._reusable_final_audit(script)
    assert reused is not None and reused["input_revision"] == payload["input_revision"]
    assert len(reused["issues"]) == 1

    # 稿子改过（revision 变了）→ 不再是同一对象，必须重新审计
    changed = _script(3)
    changed["shots"][0]["ff_desc"] = "改过之后的描述"
    assert m._reusable_final_audit(changed) is None

    # 调用失败的审计不得被冻结复用（失败 != 0 issue）
    bad = dict(payload, status="request_failed", error="boom")
    atomic_dump_json(chapter / "step01_audit.blocked.json", bad,
                     ensure_ascii=False, indent=2)
    assert m._reusable_final_audit(script) is None


def test_audit_reuse_rejects_other_scope_or_fingerprint(chapter):
    import step01_script_gen.main as m
    from shared.json_io import atomic_dump_json

    script = _script(3)
    for kw in ({"scope": "other_scope", "fingerprint": m.RULE_SOURCE_FINGERPRINT},
               {"scope": m.STEP01_AUDIT_SCOPE, "fingerprint": "deadbeef"}):
        payload = _audit_payload(script, **kw)
        atomic_dump_json(chapter / "step01_audit.blocked.json", payload,
                         ensure_ascii=False, indent=2)
        assert m._reusable_final_audit(script) is None


def test_audit_reuse_requires_matching_prompt_and_focus_version(chapter):
    """v6.111（审核 P2-5）：改了审计提示词/focus 之后，旧审计不得被复用."""
    import step01_script_gen.main as m
    from shared.json_io import atomic_dump_json

    script = _script(3)
    base = _audit_payload(script, scope=m.STEP01_AUDIT_SCOPE,
                          fingerprint=m.RULE_SOURCE_FINGERPRINT)
    atomic_dump_json(chapter / "step01_audit.blocked.json", base,
                     ensure_ascii=False, indent=2)
    assert m._reusable_final_audit(script) is not None

    for field in ("audit_prompt_version", "audit_focus_version"):
        stale = dict(base, **{field: "deadbeefdead"})
        atomic_dump_json(chapter / "step01_audit.blocked.json", stale,
                         ensure_ascii=False, indent=2)
        assert m._reusable_final_audit(script) is None, field


# ---------------------------------------------------------------------------
# v6.112：每轮审计诊断台账 + 指纹分层
# ---------------------------------------------------------------------------
def test_commit_records_every_audit_round_in_the_ledger(chapter, monkeypatch):
    """提交点的**每一轮**审计都进台账，且台账不碰三份正式产物.

    这是区分「跨镜顺带修好」与「同一内容 severity 波动」所缺的证据：日志只打
    计数与 (shot, code)，正式 `step01_audit.json` 只留最后一轮结论。
    """
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    blocking = [{"code": "model_audit_issue", "shot_id": "2", "frame_id": "ff",
                 "message": "首帧状态与动作冲突", "field_path": "ff_desc",
                 "evidence": "已倒地"}]
    _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, issues=blocking, scope=m.STEP01_AUDIT_SCOPE,
        fingerprint=RULE_SOURCE_FINGERPRINT))

    _, summary = m._commit_step01_outputs(_script(), "")
    assert summary["status"] == "BLOCK"

    ledger = json.loads((chapter / "step01_audit_rounds.json").read_text(encoding="utf-8"))
    assert len(ledger) == 1
    rec = ledger[0]
    assert rec["phase"] == "commit_round"
    assert rec["round"] == 1
    assert rec["reused"] is False
    assert rec["status"] == "ok"
    assert rec["issue_count"] == 1
    assert rec["blocking_count"] == 1
    assert rec["issues"][0]["message"] == "首帧状态与动作冲突"
    assert rec["input_revision"], "台账必须能对上是哪一版稿子"
    assert rec["provider"] is not None
    # 台账是诊断文件：BLOCK 路径**不得**因此写出正式产物
    assert not (chapter / "step01_audit.json").exists()


def test_prompt_source_fingerprint_is_recorded_in_artifacts(chapter, monkeypatch):
    """生成/修复提示词版本记进产物与 manifest（诊断字段，人可回溯）."""
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import (PROMPT_SOURCE_FINGERPRINT,
                                                  RULE_SOURCE_FINGERPRINT)

    _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))
    _, summary = m._commit_step01_outputs(_script(), "")
    assert summary["status"] == "COMMITTED"

    audit = json.loads((chapter / "step01_audit.json").read_text(encoding="utf-8"))
    manifest = json.loads((chapter / "step01_commit.json").read_text(encoding="utf-8"))
    assert audit["prompt_source_fingerprint"] == PROMPT_SOURCE_FINGERPRINT
    assert manifest["prompt_source_fingerprint"] == PROMPT_SOURCE_FINGERPRINT
    assert audit["rule_source_fingerprint"] == RULE_SOURCE_FINGERPRINT


def test_audit_reuse_ignores_the_prompt_source_fingerprint(chapter):
    """边界：提示词版本**不参与**审计复用判定（改提示词改的是内容，不是判据）.

    只有 `rule_source_fingerprint` 决定旧审计是否仍然有效——否则每次改一句修复
    提示词都要重掷一次审计（总计划 §1.3 反对的「反复抽样博一次偶然 PASS」的反面）。
    """
    import step01_script_gen.main as m
    from shared.json_io import atomic_dump_json

    script = _script(3)
    payload = dict(_audit_payload(script, scope=m.STEP01_AUDIT_SCOPE,
                                  fingerprint=m.RULE_SOURCE_FINGERPRINT),
                   prompt_source_fingerprint="deadbeefdeadbeef")
    atomic_dump_json(chapter / "step01_audit.blocked.json", payload,
                     ensure_ascii=False, indent=2)
    assert m._reusable_final_audit(script) is not None


def test_block_records_both_entry_and_repaired_revision(chapter, monkeypatch):
    """BLOCK 时台账必须同时登记**进入提交点的原稿**与修后 revision.

    用户 2026-09-25 决定（第 1 项）。现场：run 193736 只登记了修后的 `1d1cb454`，
    而被 BEST-GUARD 选中的历史稿自身 revision（`f434e5090d7b`）**不在台账内** →
    下一轮选稿照样能再选它、再 BLOCK 一次。
    """
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    blocking = [{"code": "model_audit_issue", "shot_id": "1", "frame_id": "lf",
                 "entity_id": "群演", "field_path": "frame_layouts.lf",
                 "evidence": "老农已走远", "message": "在场矛盾"}]
    _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, issues=blocking, scope=m.STEP01_AUDIT_SCOPE,
        fingerprint=RULE_SOURCE_FINGERPRINT))

    # 让修复轮真的写一版（删掉判据证据 → 本轮算「已解决」→ 被接受 → revision 变）
    def _repair(script, novel_text, **kw):
        out = json.loads(json.dumps(script))
        out["shots"][0]["lf_desc"] = "修后：画面只有群演"
        return out

    monkeypatch.setattr(m, "_repair_shot_warnings", _repair)

    _src = _script()
    _, summary = m._commit_step01_outputs(_src, "")
    assert summary["status"] == "BLOCK"
    _entry = summary["entry_revision"]
    assert _entry, "summary 没带进入提交点的 revision"
    # BLOCK 分支不写 `summary["revision"]`，修后 revision 以 BLOCK manifest 为准
    _final = json.loads((chapter / "step01_commit.json").read_text(
        encoding="utf-8"))["script_revision"]
    assert _entry != _final, (
        "夹具没让修复轮改出差异（entry=%s final=%s），本用例失去判别性"
        % (_entry[:12], _final[:12]))
    _ledger = (chapter / "snapshots" / "_blocked_revisions.json")
    revs = set(json.loads(_ledger.read_text(encoding="utf-8"))["revisions"])
    assert _final in revs, "修后 revision 没进台账"
    assert _entry in revs, "**原稿** revision 没进台账——历史稿会因漏记反复入选"


def test_selection_ledger_covers_the_snapshot_identity_boundary(chapter, monkeypatch):
    """**贯通**（用户 2026-09-25 根因）：选中旧稿 → 覆盖本轮大纲 → BLOCK → 下次跳过.

    契约错位：BEST-GUARD 用**快照自身**的 revision 判欠账，而 `chapter_outline`
    参与 revision 哈希——主流程用本轮大纲覆盖后，进入提交点时 revision 已经变成
    另一个值（分镜一字未改）。台账若只记「提交入口 / 修后」，guard 下次仍拿快照
    自身 revision 查台账 → 漏拦，同一份欠账稿被反复选中。

    已有的两条回归分别只覆盖提交点与选稿，**没有覆盖中间这次大纲覆盖**。
    """
    import step01_script_gen.main as m
    from shared.quality_gate import compute_script_revision
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    # 分数用镜数驱动，质量档给"干净"——本用例测的是**身份边界**，不是打分
    monkeypatch.setattr(m, "_score_script",
                        lambda s, t="": {"total": float(len(s.get("shots") or []))})
    monkeypatch.setattr(m, "_candidate_quality_summary", lambda s, t="": {
        "quality_checked": True, "blocking_risk_count": 0, "integrity_ok": True,
        "integrity_issues": [], "structural_ok": True, "structural_issues": []})

    _snap_dir = chapter / "snapshots"
    _snap_dir.mkdir(parents=True, exist_ok=True)

    def _as_snapshot(obj: dict) -> dict:
        """落盘用形态：与 guard 载入后的形态一致（`HOOK_ENABLED=0` 时它会 pop hook）."""
        out = json.loads(json.dumps(obj))
        out.pop("hook", None)
        return out

    _old = _as_snapshot(_script(12))
    (_snap_dir / "script_result_20260924_215530.json").write_text(
        json.dumps(_old, ensure_ascii=False), encoding="utf-8")
    _second = _as_snapshot(_script(8))
    (_snap_dir / "script_result_20260924_220000.json").write_text(
        json.dumps(_second, ensure_ascii=False), encoding="utf-8")
    _old_rev = compute_script_revision(_old)

    # ① 选稿边界：本次新稿（1 镜）更差 → 选中 12 镜的历史快照，并带出它自身的 revision
    selected, meta = m._cross_run_best_guard(_script(1), "")
    assert meta.get("selected") is True, meta
    assert meta.get("selected_revision") == _old_rev, (
        "选稿边界没有带出**快照自身** revision：%s" % meta.get("selected_revision"))

    # ② 主流程用**本轮大纲**覆盖它——分镜没改，revision 却变了
    script = selected
    script["chapter_outline"] = {"main_events": ["本轮大纲"], "climax": "本轮高潮"}
    _entry_rev = compute_script_revision(script)
    assert _entry_rev != _old_rev, (
        "夹具没复现「大纲覆盖改 revision」这一步（都等于 %s）" % _old_rev[:12])

    # ③ 提交点 BLOCK（审计报 blocking + 修复轮改出一版 → 三个身份互不相同）
    blocking = [{"code": "model_audit_issue", "shot_id": "1", "frame_id": "lf",
                 "entity_id": "群演", "field_path": "frame_layouts.lf",
                 "evidence": "老农已走远", "message": "在场矛盾"}]
    _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, issues=blocking, scope=m.STEP01_AUDIT_SCOPE,
        fingerprint=RULE_SOURCE_FINGERPRINT))

    def _repair(s, novel_text, **kw):
        out = json.loads(json.dumps(s))
        out["shots"][0]["lf_desc"] = "修后：画面只有群演"
        return out

    monkeypatch.setattr(m, "_repair_shot_warnings", _repair)
    _, summary = m._commit_step01_outputs(
        script, "", source_revision=meta.get("selected_revision"))
    assert summary["status"] == "BLOCK"
    _final_rev = json.loads((chapter / "step01_commit.json").read_text(
        encoding="utf-8"))["script_revision"]
    _revs = set(json.loads((_snap_dir / "_blocked_revisions.json").read_text(
        encoding="utf-8"))["revisions"])
    assert _old_rev in _revs, (
        "**选稿边界**（快照自身）的 revision 没进台账 → 下次仍会选中这份欠账稿")
    assert _entry_rev in _revs, "提交入口 revision 没进台账"
    assert _final_rev in _revs, "修后 revision 没进台账"
    assert len({_old_rev, _entry_rev, _final_rev}) == 3, (
        "夹具没让三个身份互不相同，用例失去判别性")

    # ④ 下一次选稿：这份欠账快照被跳过，改选另一份
    _sel2, _meta2 = m._cross_run_best_guard(_script(1), "")
    assert _meta2.get("selected") is True, _meta2
    assert _meta2.get("selected_revision") != _old_rev, (
        "已知欠账的旧稿又被选中了：%s" % _meta2.get("selected_path"))
    assert _meta2.get("selected_revision") == compute_script_revision(_second)

# ---------------------------------------------------------------------------
# v6.152（C1/C2）：审计**调用失败**的终局记账
# ---------------------------------------------------------------------------
def test_terminal_blocking_drops_stale_list_on_audit_call_failure():
    """口径：调用失败 ⇒ 只记本次失败一条；审计 ok / 本 run 未调用审计 ⇒ 原样返回."""
    from step01_script_gen.main import _terminal_blocking_records as f

    stale = [{"code": "A", "shot_id": "1"}, {"code": "B", "shot_id": "2"}]

    recs, status, failed = f({"status": "ok"}, list(stale))
    assert recs == stale and status == "ok" and failed is False

    # 台词完整性门直接判定（本 run 根本没调用审计）：不得被当成「审计失败」
    recs, status, failed = f(None, list(stale))
    assert recs == stale and failed is False

    recs, status, failed = f({"status": "request_failed"}, list(stale))
    assert failed is True and status == "request_failed"
    assert [r["code"] for r in recs] == ["audit_call_failed"]
    assert recs[0]["transient"] is True

    # 首轮就失败：旧行为是 blocking=[]（manifest 报 0 条，读者以为「没有阻塞」）
    recs, _s, failed = f({"status": "truncated"}, [])
    assert failed is True and len(recs) == 1
    assert recs[0]["code"] == "audit_call_failed"


def test_audit_call_failure_is_recorded_and_registers_no_debt(chapter, monkeypatch):
    """集成：失败 ⇒ manifest/sidecar/逐轮台账都留痕；陈旧清单不进 manifest；
    **不登记欠账**（临时故障 ≠ 稿件语义欠账，否则污染 BEST-GUARD 台账）."""
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, status="request_failed", scope=m.STEP01_AUDIT_SCOPE,
        fingerprint=RULE_SOURCE_FINGERPRINT))

    _out, summary = m._commit_step01_outputs(_script(), "")

    assert summary["status"] == "BLOCK"
    manifest = json.loads((chapter / "step01_commit.json").read_text(encoding="utf-8"))
    # ① manifest：读取方据此区分「正式旧报告」与「本次失败」
    assert manifest["audit_status"] == "request_failed"
    assert [b["code"] for b in manifest["blocking"]] == ["audit_call_failed"]
    assert manifest["blocking_count"] == 1
    # ② blocked sidecar：失败也被留痕（正式三件套不动）
    side = json.loads((chapter / "step01_audit.blocked.json").read_text(encoding="utf-8"))
    assert side["status"] == "request_failed"
    assert not (chapter / "step01_audit.json").exists(), "BLOCK 不得替换正式产物"
    # ③ 逐轮台账
    rounds = json.loads((chapter / "step01_audit_rounds.json").read_text(encoding="utf-8"))
    _rows = rounds if isinstance(rounds, list) else (rounds.get("rounds") or [])
    assert any(str(r.get("status") or "") == "request_failed" for r in _rows),         "逐轮台账里必须能看到这次失败"
    # ④ 不登记欠账
    assert m._load_blocked_revisions() == {}
    assert not (chapter / "snapshots" / "_blocked_revisions.json").exists()


# ---------------------------------------------------------------------------
# 2026-09-30 复核第 4 轮 P1-1：提交层的契约门**执行级**复核（不是搜源码字符串）
# ---------------------------------------------------------------------------
_LINE = "张十三道：住嘴！"


def _plan_with_contract():
    """带**承载契约**的计划：B1 由台词1 承载 → 提交层的 `require_declaration` 生效."""
    return {"shot_beats": ["B1 甲把话说了｜载体：台词1", "B2 乙喝茶｜载体：画面",
                           "B3 丙离席｜载体：画面"],
            "dialogue_lines": [_LINE]}


def _script_with_contract(plan, *, declarations: bool = True):
    """与计划**身份相符**的稿件；`declarations=False` 时整稿丢掉 `plan_refs`."""
    from step01_script_gen.plan_feasibility import plan_identity
    script = _script()
    script["plan_id"] = plan_identity(plan)
    script["shots"][0]["narration_segment"] = _LINE
    script["shots"][0]["subtitle"] = _LINE
    if declarations:
        script["shots"][0]["plan_refs"] = {"beats": [1], "lines": [1]}
        script["shots"][1]["plan_refs"] = {"beats": [2], "lines": []}
        script["shots"][2]["plan_refs"] = {"beats": [3], "lines": []}
    return script


def test_commit_entry_blocks_a_script_that_dropped_its_declarations(chapter, monkeypatch):
    """**真跑提交入口**：整稿丢掉 `plan_refs` → 提交点显式 BLOCK，且**一次审计都没发**.

    判据顺序是这条用例的重点：契约门位于审计/修复轮**之前**（旧口径是搜源码字符串，
    证明不了顺序，也证明不了它真的会拦）。夹具的判别性是两段式的——带声明时稿件
    正常走完审计并提交，去掉声明后同一份稿立刻停在门前、审计调用数不变。
    """
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    plan = _plan_with_contract()
    calls = _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))

    # ① 带声明 → 过门、进审计、提交（证明这条路径本身不是「怎么都 BLOCK」）
    _s, summary = m._commit_step01_outputs(
        _script_with_contract(plan), "", plan=plan)
    assert summary["status"] == "COMMITTED", summary
    assert calls["n"] == 1

    # ② 同一份稿，只去掉声明 → 停在契约门；审计调用数**不变**
    _s2, summary2 = m._commit_step01_outputs(
        _script_with_contract(plan, declarations=False), "", plan=plan)
    assert summary2["status"] == "BLOCK", summary2
    assert summary2["repair_outcome"] == "event_contract_broken", summary2
    assert [b["code"] for b in summary2["blocking"]] == ["event_contract_broken"]
    assert calls["n"] == 1, "契约门必须在审计之前拦下（不得先烧一次审计）"


def test_repair_that_breaks_the_contract_is_not_adopted(chapter, monkeypatch):
    """P1-1：修复稿破坏契约 → **不采纳**（保留基线），本轮到此为止.

    提交层的契约门在修复循环**之前**；修复轮整份替换稿件，「改之前是对的」不等于
    「改之后还是对的」。这里让修复稿把承载台词改掉（声明仍在）——修复前过门、修复后
    破损，提交点必须拒收这份修复稿并保留基线。
    """
    import copy

    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    plan = _plan_with_contract()
    rounds = {"n": 0}

    def fake_repair(script, novel_text, **kw):
        rounds["n"] += 1
        cand = copy.deepcopy(script)
        # 台词没了、声明还在 → `plan_refs_line_not_in_shot`
        cand["shots"][0]["narration_segment"] = "张十三道：走开！"
        cand["shots"][0]["subtitle"] = "张十三道：走开！"
        return cand

    monkeypatch.setattr(m, "_repair_shot_warnings", fake_repair)
    calls = _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, issues=[{"code": "model_audit_x", "shot_id": "1",
                    "severity": "visual_risk", "message": "m",
                    "field_path": "prompt",
                    "repair_targets": [{"shot_id": "1", "field_path": "prompt"}]}],
        scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))

    script, summary = m._commit_step01_outputs(
        _script_with_contract(plan), "", plan=plan)

    assert summary["status"] == "BLOCK", summary
    assert summary["repair_outcome"] == "contract_broken", summary
    assert "不采纳" in summary["detail"] and "plan_refs_line_not_in_shot" in summary["detail"]
    assert script["shots"][0]["narration_segment"] == _LINE, "基线必须保留（未被修复稿替换）"
    assert rounds["n"] == 1 and calls["n"] == 1, "拒收后不得再发一轮审计/修复"

# ---------------------------------------------------------------------------
# 2026-10-28：提交入口对 finalize 失败的处理（新分支）
# ---------------------------------------------------------------------------
def test_initial_finalize_error_blocks_before_any_audit(chapter, monkeypatch):
    """finalize 返回错误 ⇒ BLOCK/finalize_failed，**零审计调用**，不写三份产物.

    同时核「不污染基线」：`finalize_step01_script` 是就地写，失败时调用方拿到的稿件
    必须仍是进入时的内容（`_finalize` 在副本上跑）。
    """
    import copy

    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    calls = _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))

    def bad_finalize(script, **kwargs):
        return script, ["timeline broken: 末段 end=5 ≠ duration 5.7"]

    monkeypatch.setattr(m, "finalize_step01_script", bad_finalize)
    source = _script()
    before = copy.deepcopy(source)

    out, summary = m._commit_step01_outputs(source, "")

    assert summary["status"] == "BLOCK", summary
    assert summary["repair_outcome"] == "finalize_failed"
    assert [b["code"] for b in summary["blocking"]] == ["finalize_failed"]
    assert "timeline broken" in summary["detail"]
    assert calls["n"] == 0, "finalize 未通过时不得发起审计"
    # 调用方传进来的对象必须原样（提交入口在入口处复制）。禁用状态的 `hook` 归一
    # 现在只发生在**投影**里（成功路径），失败路径原样交回入口副本、不落盘。
    assert source == before, "失败路径不得改写调用方的稿件（基线保留）"
    assert out["shots"] == before["shots"]
    assert out.get("chapter_outline") == before.get("chapter_outline")
    manifest = json.loads((chapter / "step01_commit.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "BLOCK"
    assert [b["code"] for b in manifest["blocking"]] == ["finalize_failed"]
    for name in ("script_result.json", "step01_quality.json", "step01_audit.json"):
        assert not (chapter / name).exists(), name
    assert not (chapter / "_staged").exists(), "不得留下看起来像权威副本的 staged"


def test_finalize_crash_is_not_reported_as_finalize_failed(chapter, monkeypatch):
    """finalize **自身抛异常** ⇒ 向上抛（明确失败），不得折算成内容结论."

    判别点：旧写法 `except ValueError` 会把任何 ValueError（包括 finalize 内部/被调
    代码的取值错误）都记成 `finalize_failed`，并写一份 BLOCK manifest。这里要求
    异常原样抛出、且不产生任何提交尝试的痕迹。
    """
    import pytest as _pytest

    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))

    def boom(script, **kwargs):
        raise ValueError("window math exploded")

    monkeypatch.setattr(m, "finalize_step01_script", boom)

    with _pytest.raises(ValueError, match="window math exploded"):
        m._commit_step01_outputs(_script(), "")

    assert not (chapter / "step01_commit.json").exists(), "崩溃不是 BLOCK 结论"
    assert not (chapter / "script_result.json").exists()


def test_repair_finalize_error_keeps_baseline(chapter, monkeypatch):
    """修复稿 finalize 失败 ⇒ 不采纳修复稿，保留基线，且不再多发调用."""
    import copy

    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    plan = _plan_with_contract()
    real_finalize = m.finalize_step01_script
    # 基线是**第一趟 finalize 之后**的稿子（finalize 会按规则改写景别前缀等），
    # 不是夹具原样；先算一遍，才能在修复稿被拒后核「保留的是哪一份」。
    _baseline, _errs = real_finalize(copy.deepcopy(_script_with_contract(plan)),
                                     novel_text="")
    assert not _errs
    baseline_prompt = _baseline["shots"][1]["prompt"]
    fin = {"n": 0}

    def flaky_finalize(script, **kwargs):
        fin["n"] += 1
        if fin["n"] == 1:
            return real_finalize(script, **kwargs)
        return script, ["repair pass broke the timeline"]

    monkeypatch.setattr(m, "finalize_step01_script", flaky_finalize)
    rounds = {"n": 0}

    def fake_repair(script, novel_text, **kwargs):
        rounds["n"] += 1
        cand = copy.deepcopy(script)
        cand["shots"][1]["prompt"] = cand["shots"][1]["prompt"] + " 补一句"
        return cand

    monkeypatch.setattr(m, "_repair_shot_warnings", fake_repair)
    calls = _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, issues=[{"code": "model_audit_x", "shot_id": "2",
                    "severity": "visual_risk", "message": "m",
                    "field_path": "prompt",
                    "repair_targets": [{"shot_id": "2", "field_path": "prompt"}]}],
        scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))

    script, summary = m._commit_step01_outputs(
        _script_with_contract(plan), "", plan=plan)

    assert summary["status"] == "BLOCK", summary
    assert summary["repair_outcome"] == "repair_finalize_failed", summary
    assert "repair pass broke the timeline" in summary["detail"]
    assert rounds["n"] == 1 and calls["n"] == 1, "拒收后不得再发修复/审计调用"
    assert script["shots"][1]["prompt"] == baseline_prompt, "修复前的基线必须保留"
