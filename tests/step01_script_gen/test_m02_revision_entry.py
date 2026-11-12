# -*- coding: utf-8 -*-
"""M02：合法修订经**实际修订入口**的五条路径.

对应统一收口计划 M02 的完成条件：

1. 单字段修复成功；
2. 必要关联字段联合修复成功；
3. 整镜无关修改被拒（且原因完整）；
4. 授权扩大后非目标仍受保护；
5. 投影与恢复不会撤销已接受修复。

样本是匿名最小情节（甲 / 木杖），不含任何作品专名。桩响应按**当轮真实请求**的
schema 构造：`shot_replacements[]` 的窄字段补丁形态（`updates[]`），这是
`apply_scoped_field_updates` 的输入契约，也是 S06 实跑里模型实际返回的形态。

背景（S06 归因，见 `docs/step01-layered-execution/M01-evidence-and-attribution.md`）：
`PROP_CONTINUITY` 只点名 `lf_desc` 时，同一事实在 `frame_layouts.lf` 的对应条目里
还有一份；只改描述会让布局留下矛盾，模型据此补布局却被判「越权字段补丁」→ 整条
拒收 → 该轮零进展。修法是**同一镜、同一帧、同一实体**的精确互授
（`repair_fields.frame_layout_companions`），而不是放开整镜。
"""
from __future__ import annotations

import json

import pytest

from step01_script_gen.quality_issues import RULE_VERSION

_ISSUE_EVIDENCE = "木杖离地悬空无支撑"


def _shot(index: int = 1, **over) -> dict:
    shot = {
        "index": index,
        "duration": 5.0,
        "duration_sec": 5.0,
        "main_subject": "甲",
        "scene": "院中",
        "location": "院中",
        "clip_transition": "硬切",
        "context_bridge": "",
        "ff_desc": "甲站在院中，右手握着木杖",
        "lf_desc": "甲右手提着木杖，木杖离地悬空无支撑",
        "motion_desc": "前2秒：甲抬手握杖；后3秒：甲提着木杖转身",
        "prompt": "[中景] 院中，甲提杖而立",
        "narration_segment": "",
        "subtitle": "",
        "frame_layouts": {
            "ff": [{"name": "甲", "on_screen": True, "props": ["木杖"]}],
            "lf": [{"name": "甲", "on_screen": True, "props": []}],
            "kfs": [],
        },
        "keyframes": [],
    }
    shot.update(over)
    return shot


def _script() -> dict:
    """两镜：镜 1 有道具连续性问题，镜 2 是干净的非目标镜头."""
    return {
        "shots": [
            _shot(1),
            _shot(2, scene="院中", location="院中",
                  ff_desc="甲立于院门内侧，木杖拄地",
                  lf_desc="甲抬手推门，木杖倚在门框",
                  motion_desc="前2秒：甲抬手；后3秒：甲推门",
                  prompt="[中景] 院门内侧，甲推门",
                  frame_layouts={
                      "ff": [{"name": "甲", "on_screen": True, "props": ["木杖"]}],
                      "lf": [{"name": "甲", "on_screen": True, "props": ["木杖"]}],
                      "kfs": [],
                  }),
        ],
        "hook": None,
        "total_duration": 10.0,
    }


def _issue(*, code="model_audit_PROP_CONTINUITY", field_path="lf_desc",
           frame_id="lf", entity_id="甲", shot_id="1") -> dict:
    return {
        "code": code,
        "severity": "visual_risk",
        "message": "尾帧描述让道具重新回到手里，与布局里的脱手状态矛盾。",
        "shot_id": shot_id,
        "frame_id": frame_id,
        "entity_id": entity_id,
        "field_path": field_path,
        "evidence": _ISSUE_EVIDENCE,
        "repair_targets": [{"shot_id": shot_id, "field_path": field_path}],
        "repairable": True,
    }


class _FrozenService:
    """桩 provider：返回**预置响应**，并记下真实请求（用于核对允许字段）."""

    def __init__(self, payload):
        self.text = json.dumps(payload, ensure_ascii=False)
        self.requests: list = []
        self.n = 0

    def generate(self, req):
        self.n += 1
        self.requests.append(req)

        class _R:
            success = True
            error = ""

        _R.text = self.text
        return _R()


def _env(monkeypatch, tmp_path, payload):
    import step01_script_gen.main as m

    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    monkeypatch.setattr(m, "OUTPUT_FILE", tmp_path / "script_result.json")
    monkeypatch.setattr(m, "SCRIPT_FILE", tmp_path / "script_result.json")
    monkeypatch.setattr(m, "collect_structured_issues",
                        lambda *a, **k: ([], False, RULE_VERSION))
    for name in ("_metrics_inc", "_metrics_set", "_metrics_update"):
        monkeypatch.setattr(m, name, lambda *a, **k: None)
    monkeypatch.setattr(m, "resolve_model_prompt_profile",
                        lambda *a, **k: (lambda *a, **k: "PROMPT", "SYS"))
    svc = _FrozenService(payload)
    monkeypatch.setattr(m, "create_service", lambda *a, **k: svc)
    return m, svc


def _run(m, *, issues=None, script=None):
    diag: list = []
    blocks: list = []
    out = m._repair_shot_warnings(
        json.loads(json.dumps(script or _script())),
        "原文切片：甲提着木杖站在院中，随后推门进院。",
        model=None,
        audit_issues=issues if issues is not None else
        m._audit_payload_to_issues({"issues": [_issue()]}),
        max_rounds=1,
        repair_diag=diag,
        block_reasons=blocks,
        retry_state={},
        audit_attempted=set(),
        defer_targets=set(),
    )
    return out, diag, blocks


def _decision(diag):
    rows = [r for r in diag if isinstance(r, dict) and "accepted" in r]
    assert rows, "台账里必须有一条决策记录，实际=%r" % (diag,)
    return rows[-1]


# ---------------------------------------------------------------------------
# 路径 1：单字段修复
# ---------------------------------------------------------------------------
def test_single_field_fix_is_accepted(monkeypatch, tmp_path):
    payload = {"shot_replacements": [{"shot_id": "1", "updates": [
        {"field_path": "lf_desc", "value": "甲右手提着木杖拄地"}]}]}
    m, svc = _env(monkeypatch, tmp_path, payload)
    out, diag, blocks = _run(m)

    rec = _decision(diag)
    assert svc.n == 1
    assert rec["accepted"] is True, rec
    assert rec["audit_field_touched"] == 1
    assert out["shots"][0]["lf_desc"] == "甲右手提着木杖拄地"
    assert not blocks


# ---------------------------------------------------------------------------
# 路径 2：必要关联字段联合修复
# ---------------------------------------------------------------------------
def test_joint_layout_fix_is_accepted_and_authorization_is_recorded(
        monkeypatch, tmp_path):
    """同一事实在描述与布局各一份：两处一起改才是一个完整修复."""
    payload = {"shot_replacements": [{"shot_id": "1", "updates": [
        {"field_path": "lf_desc", "value": "甲右手提着木杖拄地"},
        {"field_path": "frame_layouts.lf.0.props", "value": ["木杖"]}]}]}
    m, svc = _env(monkeypatch, tmp_path, payload)
    out, diag, blocks = _run(m)

    rec = _decision(diag)
    assert rec["accepted"] is True, rec
    assert out["shots"][0]["frame_layouts"]["lf"][0]["props"] == ["木杖"]
    assert not blocks

    # 台账必须能回答「为什么允许改布局道具」：声明只有 lf_desc，
    # 布局叶子是**扩出来**的，且扩出来的路径被逐条记下。
    auth = rec["authorization"]["shot_1"]
    assert auth["declared"] == ["lf_desc"]
    assert "frame_layouts.lf.0.props" in auth["allowed"]
    assert "frame_layouts.lf.0.props" in auth["beyond_declared"]
    # 收窄边界：只到同帧该实体的叶子，不放开整镜、不放开别的帧。
    assert not any(p.startswith("frame_layouts.ff") for p in auth["allowed"])
    assert "prompt" not in auth["allowed"]
    assert "motion_desc" not in auth["allowed"]


# ---------------------------------------------------------------------------
# 路径 3：整镜无关修改被拒
# ---------------------------------------------------------------------------
def test_unrelated_whole_shot_change_is_rejected_with_full_reason(
        monkeypatch, tmp_path):
    """窄字段补丁夹带无关字段 → 拒收，且台账给出**完整**原因.

    这是 S06 实跑里模型实际返回的形态（`updates[]`）：一条补丁里既改被点名字段、
    又顺带改 `prompt`。窄补丁是 fail-closed 的——任一路径越权即整条拒收，
    不做半条修复。
    """
    payload = {"shot_replacements": [{"shot_id": "1", "updates": [
        {"field_path": "lf_desc", "value": "甲右手提着木杖拄地"},
        {"field_path": "prompt", "value": "[特写] 院中，甲横杖而立"}]}]}
    m, svc = _env(monkeypatch, tmp_path, payload)
    out, diag, blocks = _run(m)

    rows = [r for r in diag if isinstance(r, dict) and r.get("kind") == "patch_unusable"]
    assert rows, diag
    rec = rows[-1]
    assert rec["reason"] == "rejected_by_validation"
    # 拒绝原因不能只剩一句「未通过校验」：要指名是哪条路径越权。
    assert rec["rejected"], rec
    assert any("prompt" in r and "越权" in r for r in rec["rejected"]), rec["rejected"]
    # 被拒的轮次不得改动原稿
    assert out["shots"][0]["lf_desc"] == _script()["shots"][0]["lf_desc"]
    assert out["shots"][0]["prompt"] == _script()["shots"][0]["prompt"]


def test_whole_shot_echo_keeps_unrelated_change_out_of_the_script(
        monkeypatch, tmp_path):
    """整镜回显里的无关字段被**回填**（不是静默采纳），并在四栏台账里可查.

    整镜形态走 `merge_authorized` 投影：未授权字段从原稿回填，接受判定只看
    授权面。与窄补丁的「整条拒收」是两种不同的保护方式，结果都是原稿那一处不变。
    """
    changed = _shot(1, lf_desc="甲右手提着木杖拄地",
                    prompt="[特写] 院中，甲横杖而立")
    payload = {"shot_replacements": [{"shot_id": "1", "shot": changed}]}
    m, svc = _env(monkeypatch, tmp_path, payload)
    out, diag, blocks = _run(m)

    rec = _decision(diag)
    assert out["shots"][0]["lf_desc"] == "甲右手提着木杖拄地"
    # 未授权的那句**不得落到稿子上**（后处理可能改写 prompt 的其它部分，
    # 所以按「越权内容不在」判，而不是按整串相等判）。
    assert "[特写]" not in str(out["shots"][0].get("prompt") or "")
    assert "横杖" not in str(out["shots"][0].get("prompt") or "")
    # 授权面逐条可查：prompt 不在其中，所以它只能被回填
    assert "prompt" not in rec["authorization"]["shot_1"]["allowed"]


# ---------------------------------------------------------------------------
# 路径 4：授权扩大后非目标仍受保护
# ---------------------------------------------------------------------------
def test_non_target_shot_stays_protected_after_widening(monkeypatch, tmp_path):
    """同一响应里夹带未点名镜头 → 该条目拒收；被点名镜头照常提交."""
    payload = {"shot_replacements": [
        {"shot_id": "1", "updates": [
            {"field_path": "lf_desc", "value": "甲右手提着木杖拄地"}]},
        {"shot_id": "2", "updates": [
            {"field_path": "prompt", "value": "[特写] 院门内侧，甲推门"}]},
    ]}
    m, svc = _env(monkeypatch, tmp_path, payload)
    out, diag, blocks = _run(m)

    rec = _decision(diag)
    assert rec["accepted"] is True, rec
    assert out["shots"][0]["lf_desc"] == "甲右手提着木杖拄地"
    # 非目标镜头的**语义**字段不变（后处理可能补派生字段，不算改动）
    assert (m._shot_semantic_digest(out["shots"][1])
            == m._shot_semantic_digest(_script()["shots"][1]))
    # 未点名镜头的拒绝必须留在台账里（不能被「部分提交」吞掉）
    assert rec["rejected"], rec
    assert any("shot 2" in str(r) for r in rec["rejected"]), rec["rejected"]


# ---------------------------------------------------------------------------
# 路径 5：投影不撤销已接受修复
# ---------------------------------------------------------------------------
def test_projection_does_not_undo_accepted_fix(monkeypatch, tmp_path):
    payload = {"shot_replacements": [{"shot_id": "1", "updates": [
        {"field_path": "lf_desc", "value": "甲右手提着木杖拄地"},
        {"field_path": "frame_layouts.lf.0.props", "value": ["木杖"]}]}]}
    m, svc = _env(monkeypatch, tmp_path, payload)
    out, diag, _blocks = _run(m)
    assert _decision(diag)["accepted"] is True

    projected, errs = m.project_for_commit(
        out, "原文切片：甲提着木杖站在院中，随后推门进院。")
    assert errs == [], errs
    assert projected["shots"][0]["lf_desc"] == "甲右手提着木杖拄地"
    assert projected["shots"][0]["frame_layouts"]["lf"][0]["props"] == ["木杖"]
    # 投影后稿件的身份由 `script_revision` 承载（不是 `revision`）
    assert projected.get("script_revision")


# ---------------------------------------------------------------------------
# 收窄边界：互授不越过帧
# ---------------------------------------------------------------------------
def test_companion_does_not_cross_frames(monkeypatch, tmp_path):
    """点名 `ff_desc` 时只授首帧条目；夹带尾帧条目 → 整条拒收（fail-closed）."""
    payload = {"shot_replacements": [{"shot_id": "1", "updates": [
        {"field_path": "ff_desc", "value": "甲站在院中，木杖拄地"},
        {"field_path": "frame_layouts.ff.0.props", "value": ["木杖"]},
        {"field_path": "frame_layouts.lf.0.props", "value": ["木杖"]}]}]}
    m, svc = _env(monkeypatch, tmp_path, payload)
    out, diag, _blocks = _run(
        m, issues=m._audit_payload_to_issues({"issues": [
            _issue(field_path="ff_desc", frame_id="ff")]}))
    rows = [r for r in diag if isinstance(r, dict) and r.get("kind") == "patch_unusable"]
    assert rows, diag
    rec = rows[-1]
    # 授权面：首帧条目在、尾帧条目不在
    auth = rec["authorization"]["shot_1"]
    assert "frame_layouts.ff.0.props" in auth["allowed"]
    assert "frame_layouts.lf.0.props" not in auth["allowed"]
    # 夹带越权路径 → 整条拒收（不做半条修复），原稿逐字节不变
    assert rec["reason"] == "rejected_by_validation"
    assert any("frame_layouts.lf.0.props" in str(r) for r in rec["rejected"]), rec
    assert out["shots"][0] == _script()["shots"][0]
