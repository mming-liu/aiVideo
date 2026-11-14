# -*- coding: utf-8 -*-
"""提交循环内的 final audit 复用（2026-10-03 复核之十九 P1）.

**缺陷**（复核定位，真实运行可复现）：修复事务被**整轮回退**时稿件一个字没改，
下一轮的审计请求与上一轮**逐字相同**；但循环只查盘上的正式产物 / BLOCK sidecar
（`_reusable_final_audit`），看不到**刚刚这一轮**已经拿到的结论 ⇒ 又 POST 一次。
实测代价（第 19 轮 ch2）：同一次运行内**同一请求指纹**两次调用，白花 133.5 秒，
并换回一份**不同**的问题清单（blocking 2 条 vs 5 条），把「零改善」这个结论也搅浑。
既定规则（总计划 §1.3）本就禁止「同一 revision 反复调用 full audit 博一次偶然 PASS」。

**修法**：请求身份（`audit.final_audit_request_identity`，覆盖**进请求的每一个字节**）
→ 本轮已成功的结论。身份相同才复用；稿件 / 定向项 / 规则 / 档位 / 通道任一变化，
身份自然改变 ⇒ 照常重审；**失败**的结论不进 memo。

**验收**（复核给定）：同稿回退 + 反馈重试仅 1 次审计；采纳新稿重新审计；
请求变化不复用；失败审计不复用为成功；无审计阻塞但确定性阻塞仍 BLOCK；
合法无阻塞仍 COMMITTED。
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

# 与 `test_step01_commit` **共用同一套**夹具（同源）：脚本构造、审计桩、章节隔离。
from tests.step01_script_gen.test_step01_commit import (      # noqa: E402
    _audit_payload, _fake_audit, _script, chapter)

_BLOCKING = {
    "code": "model_audit_x", "shot_id": "1", "severity": "visual_risk",
    "message": "m", "field_path": "prompt",
    # 2026-11-14 P1/G1：**必须带可判的判据证据**。
    # G1 收紧后「证据缺失/过短」不再被跳过，而是**保留未关闭**（待复核）——
    # 没有证据的夹具等于要求「沉默即通过」，与门禁语义相反。这里的证据取自
    # `_shot(1)` 的 prompt 原文，修好（改掉该措辞）后 `_audit_issue_resolved`
    # 才会返回 True，用例才是在测「确定性关闭」而不是「模型沉默」。
    "evidence": "测试镜头1，画面各不相同",
    # v6.119：blocking 审计条目必须给可执行的定向修复目标，否则提交点契约门
    # 会直接 BLOCK、根本不进修复轮。
    "repair_targets": [{"shot_id": "1", "field_path": "prompt"}],
}


def _records(chapter_dir) -> list:
    return json.loads(
        (chapter_dir / "step01_audit_rounds.json").read_text(encoding="utf-8"))


def _one_blocking(script, n):
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT
    return _audit_payload(script, issues=[dict(_BLOCKING)],
                          scope=m.STEP01_AUDIT_SCOPE,
                          fingerprint=RULE_SOURCE_FINGERPRINT)


# --------------------------------------------------------------------------
# 1. 同稿回退 + 反馈重试：只审计一次
# --------------------------------------------------------------------------

def test_same_script_rollback_retry_reuses_the_completed_audit(chapter, monkeypatch):
    """整轮回退 ⇒ 稿件与请求逐字未变 ⇒ 第二轮**复用**，不得再 POST."""
    import step01_script_gen.main as m

    calls = _fake_audit(monkeypatch, _one_blocking)
    repairs = []

    def repair(script, novel_text, **kw):
        repairs.append(1)
        st = kw["retry_state"]
        st.clear()
        if len(repairs) == 1:
            # 第一轮：事务被拒但导出「带反馈重试」；稿件原样返回（整轮回退）
            st.update(feedback="retry feedback", excluded=["shot_1"])
        return script

    monkeypatch.setattr(m, "_repair_shot_warnings", repair)
    _, summary = m._commit_step01_outputs(_script(), "")

    assert len(repairs) == 2, "反馈重试这一轮必须真的跑到"
    assert calls["n"] == 1, "同稿回退 + 反馈重试不得再发一次审计（复核之十九 P1）"

    rows = _records(chapter)
    assert len(rows) == 2, "原调用与复用事件都要进台账"
    assert rows[0]["reused"] is False and not rows[0].get("reuse_source")
    assert rows[1]["reused"] is True
    assert rows[1]["reuse_source"] == "in_loop"
    assert rows[1]["input_revision"] == rows[0]["input_revision"]
    # 复用**不降低内容门**：清单原样沿用，终局仍是 BLOCK
    assert rows[1]["blocking_count"] == rows[0]["blocking_count"] == 1
    assert summary["status"] == "BLOCK"
    assert summary["reused_audit"] is True


# --------------------------------------------------------------------------
# 2. 采纳新稿 ⇒ 必然重新审计
# --------------------------------------------------------------------------

def test_adopted_repair_is_audited_again_and_can_commit(chapter, monkeypatch):
    """修复被**采纳**（稿件真的变了）⇒ 请求身份改变 ⇒ 重新审计，不得复用旧结论."""
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    def factory(script, n):
        _fixed = "修正后" in script["shots"][0]["prompt"]
        return _audit_payload(
            script, issues=[] if _fixed else [dict(_BLOCKING)],
            scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT)

    calls = _fake_audit(monkeypatch, factory)

    def repair(script, novel_text, **kw):
        out = copy.deepcopy(script)
        # 2026-11-14 P1/G1：修法必须**消除判据证据**（旧夹具只是追加「（修）」，
        # 证据原样留在 prompt 里 —— 那正是「改过字段但证据仍在」，G1 收紧后
        # 不再算关闭，会正确判 BLOCK）。这里换成不含证据的措辞。
        out["shots"][0]["prompt"] = "[中景] 测试镜头1，修正后画面"
        return out

    monkeypatch.setattr(m, "_repair_shot_warnings", repair)
    _, summary = m._commit_step01_outputs(_script(), "")

    assert calls["n"] == 2, "稿件变了就必须重审"
    rows = _records(chapter)
    assert [r["reused"] for r in rows] == [False, False]
    revs = {r["input_revision"] for r in rows}
    assert len(revs) == 2, "两次审计的输入 revision 必须不同"
    assert summary["status"] == "COMMITTED"


# --------------------------------------------------------------------------
# 3. 请求变了（稿件没变）⇒ 不复用
# --------------------------------------------------------------------------

def test_changed_audit_request_is_not_reused(chapter, monkeypatch):
    """稿件逐字未变、但**定向审查项**变了 ⇒ 是另一份请求 ⇒ 必须重新审计."""
    import step01_script_gen.main as m

    calls = _fake_audit(monkeypatch, _one_blocking)
    repairs = []
    _focus_seq = iter([["定向项 A"], ["定向项 B"]])

    monkeypatch.setattr(m, "_final_audit_focus",
                        lambda script, quality: next(_focus_seq, ["定向项 B"]))

    def repair(script, novel_text, **kw):
        repairs.append(1)
        st = kw["retry_state"]
        st.clear()
        if len(repairs) == 1:
            st.update(feedback="retry feedback", excluded=["shot_1"])
        return script

    monkeypatch.setattr(m, "_repair_shot_warnings", repair)
    _, summary = m._commit_step01_outputs(_script(), "")

    assert calls["n"] == 2, "请求变了就不得复用（否则等于拿旧结论给新请求盖章）"
    rows = _records(chapter)
    assert [r["reused"] for r in rows] == [False, False]
    assert summary["status"] == "BLOCK"


# --------------------------------------------------------------------------
# 4. 失败结论不复用
# --------------------------------------------------------------------------

def test_failed_audit_is_block_not_reuse(chapter, monkeypatch):
    """`request_failed` 直接 BLOCK；台账里**没有任何**复用记录，不得冒充成功结论."""
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    calls = _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, status="request_failed", scope=m.STEP01_AUDIT_SCOPE,
        fingerprint=RULE_SOURCE_FINGERPRINT))
    _, summary = m._commit_step01_outputs(_script(), "")

    assert summary["status"] == "BLOCK"
    assert calls["n"] == 1
    rows = _records(chapter)
    assert len(rows) == 1
    assert not any(r.get("reused") for r in rows), "失败的审计不许进 memo"


# --------------------------------------------------------------------------
# 5. 无审计阻塞但有确定性阻塞 ⇒ 仍 BLOCK（复用不得把它放行）
# --------------------------------------------------------------------------

def test_deterministic_blocking_with_clean_audit_still_blocks(chapter, monkeypatch):
    """审计干净、确定性质量门有阻塞 ⇒ 仍 BLOCK；且复用不会让它少跑一轮."""
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import (Issue, RULE_SOURCE_FINGERPRINT,
                                                  RULE_VERSION)

    calls = _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, issues=[], scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))
    monkeypatch.setattr(m, "collect_structured_issues", lambda *a, **k: (
        [Issue(code="shot_content_over_cap", severity="hard",
               message="镜头内容超过单镜上限", shot_id="1",
               field_path="prompt", repairable=True)], False, RULE_VERSION))

    repairs = []

    def repair(script, novel_text, **kw):
        repairs.append(1)
        st = kw["retry_state"]
        st.clear()
        if len(repairs) == 1:
            st.update(feedback="retry feedback", excluded=["shot_1"])
        return script

    monkeypatch.setattr(m, "_repair_shot_warnings", repair)
    _, summary = m._commit_step01_outputs(_script(), "")

    assert summary["status"] == "BLOCK"
    assert calls["n"] == 1
    rows = _records(chapter)
    assert rows[1]["reuse_source"] == "in_loop"
    # 审计自身 0 条阻塞 —— 终局阻塞**全部**来自确定性质量门
    assert rows[1]["blocking_count"] == 0
    manifest = json.loads((chapter / "step01_commit.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "BLOCK"
    assert {b["code"] for b in manifest["blocking"]} == {"shot_content_over_cap"}


# --------------------------------------------------------------------------
# 6. 合法无阻塞 ⇒ COMMITTED（复用不改变成功出口）
# --------------------------------------------------------------------------

def test_clean_script_commits_with_a_single_audit(chapter, monkeypatch):
    """干净稿：一次审计、一次提交，复用机制不进入路径也不改变结果."""
    import step01_script_gen.main as m
    from step01_script_gen.quality_issues import RULE_SOURCE_FINGERPRINT

    calls = _fake_audit(monkeypatch, lambda s, n: _audit_payload(
        s, issues=[], scope=m.STEP01_AUDIT_SCOPE, fingerprint=RULE_SOURCE_FINGERPRINT))
    _, summary = m._commit_step01_outputs(_script(), "")

    assert summary["status"] == "COMMITTED"
    assert calls["n"] == 1
    rows = _records(chapter)
    assert len(rows) == 1 and rows[0]["reused"] is False
    assert (chapter / "script_result.json").exists()


# --------------------------------------------------------------------------
# 7. 身份 ⇔ 真实请求：把"复用判据"钉在线上请求上，而不是钉在注释里
# --------------------------------------------------------------------------

class _NoNet:
    """把审计的传输边界钉死在进程内（用例不得发出真实请求）."""

    def generate(self, req):
        raise RuntimeError("stub: 不联网")


def _captured_request(monkeypatch, *, script, novel_text="原文", focus=None,
                      metadata=None, model="m1", timeout_sec=180):
    """跑一次真实 `run_same_model_audit_with_status`（传输层是桩），取回它构造的请求."""
    import step01_script_gen.audit as a

    box = {}
    real_req = a.TextGenerationRequest

    def _rec(**kw):
        r = real_req(**kw)
        box["req"] = r
        return r

    monkeypatch.setattr(a, "TextGenerationRequest", _rec)
    monkeypatch.setattr(a, "create_service", lambda *x, **k: _NoNet())
    monkeypatch.delenv(a.AUDIT_PROVIDER_ENV, raising=False)
    monkeypatch.delenv(a.AUDIT_MODEL_ENV, raising=False)
    a.run_same_model_audit_with_status(novel_text, None, script, model=model,
                                       timeout_sec=timeout_sec,
                                       audit_focus=focus, metadata=metadata or {})
    return box["req"]


def _same_request_ignoring_timeout(a, b) -> bool:
    """除 `timeout_sec` 外逐字段相同 —— timeout 是**本次调用**的本地余量，不是请求语义."""
    import dataclasses
    fa = {f.name: getattr(a, f.name) for f in dataclasses.fields(a)
          if f.name != "timeout_sec"}
    fb = {f.name: getattr(b, f.name) for f in dataclasses.fields(b)
          if f.name != "timeout_sec"}
    return fa == fb


@pytest.mark.parametrize("case", [
    "同输入不同 timeout（预算越跑越少）",
    "稿件变了",
    "定向项变了",
    "元数据变了",
    "模型变了",
    "档位变了",
    "输出上限变了",
])
def test_identity_matches_the_real_request_bytes(monkeypatch, case):
    """**身份相同 ⇔ 线上请求逐字段相同（timeout 除外）**.

    这是复用判据的全部依据：身份必须覆盖进请求的每一个字节。锚在**真实构造出来的
    `TextGenerationRequest`** 上，而不是锚在注释或常量表上。
    """
    import step01_script_gen.audit as a
    from tests.step01_script_gen.test_step01_commit import _script

    monkeypatch.delenv("AUDIT_REASONING_EFFORT", raising=False)
    monkeypatch.delenv("AUDIT_MAX_OUTPUT_TOKENS", raising=False)
    base = dict(script=_script(), novel_text="原文", focus=["A"],
                metadata={"k": "v"}, model="m1", timeout_sec=180)
    other = copy.deepcopy(base)

    if case.startswith("同输入不同 timeout"):
        other["timeout_sec"] = 90
    elif case == "稿件变了":
        other["script"] = _script(4)
    elif case == "定向项变了":
        other["focus"] = ["B"]
    elif case == "元数据变了":
        other["metadata"] = {"k": "v2"}
    elif case == "模型变了":
        other["model"] = "m2"

    # A 臂：先取请求，再取身份（**同一份环境**）
    req_a = _captured_request(monkeypatch, **base)
    ident_a = a.final_audit_request_identity(
        base["novel_text"], None, base["script"], audit_focus=base["focus"],
        metadata=base["metadata"], model=base["model"])

    # 只在两臂之间**改变有效审计参数**（档位 / 输出上限）——它们读 env
    if case == "档位变了":
        monkeypatch.setenv("AUDIT_REASONING_EFFORT", "low")
    elif case == "输出上限变了":
        monkeypatch.setenv("AUDIT_MAX_OUTPUT_TOKENS", "32000")

    req_b = _captured_request(monkeypatch, **other)
    ident_b = a.final_audit_request_identity(
        other["novel_text"], None, other["script"], audit_focus=other["focus"],
        metadata=other["metadata"], model=other["model"])

    assert (ident_a == ident_b) == _same_request_ignoring_timeout(req_a, req_b), (
        "身份与真实请求必须同进同退：identity_equal=%s request_equal=%s"
        % (ident_a == ident_b, _same_request_ignoring_timeout(req_a, req_b)))

    if case.startswith("同输入不同 timeout"):
        assert ident_a == ident_b and req_a.timeout_sec != req_b.timeout_sec, (
            "timeout 是本地余量：它变了不得改变请求身份，也不得算成另一份请求")
    else:
        assert ident_a != ident_b, "这一项变了，身份必须跟着变"


@pytest.mark.parametrize("status", ["ok", "request_failed", "parse_failed",
                                    "budget_exhausted", "truncated"])
def test_only_a_conclusive_audit_is_memoizable(status):
    """**判据本身**直接断言：只有 `ok` 能进 memo，失败一律不得冒充可复用结论.

    写在循环里就只能靠"构造一个走不到的路径"来测（审计失败会立刻 break），
    所以这条判据抽成了 `_memoizable_final_audit`。
    """
    import step01_script_gen.main as m

    payload = {"status": status, "issues": []}
    assert (m._memoizable_final_audit(payload) is not None) == (status == "ok")
    assert m._memoizable_final_audit(None) is None
    assert m._memoizable_final_audit("not-a-dict") is None
    assert m._memoizable_final_audit({"issues": []}) is None, "缺 status 不得当成功"
