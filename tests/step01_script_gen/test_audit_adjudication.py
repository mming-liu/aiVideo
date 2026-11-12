"""S03：影子裁决（原始发现 / 确定性核验 / 建议处置分离）.

本文件验证的是**代码能证明的部分**：
  · 引用不存在 → 证据不足（不得当成已解决，也不得进入修订）；
  · 逐字证据 + 确定性规则 → 确认缺陷；
  · 枚举/重复/转场类确定性反证 → 合法表达；
  · 语义类 → 待裁决（blocking）或改进建议（advisory），**严重度只决定去向**，
    不决定缺陷是否成立；
  · 报告是影子：不修改 issue、不参与准入。

语义裁决本身（按冻结原文判断争议）不在离线测试里冒充已证。
"""
from __future__ import annotations

from step01_script_gen import audit_adjudication as aj


def _shot(**kw) -> dict:
    base = {"index": 1, "duration_sec": 5.0, "ff_desc": "甲站立", "lf_desc": "甲转身",
            "motion_desc": "甲转身", "prompt": "p", "narration_segment": "",
            "scene": "院内", "location": "院中", "clip_transition": "硬切",
            "context_bridge": "", "frame_layouts": {}, "keyframes": []}
    base.update(kw)
    return base


def _script(*shots) -> dict:
    return {"shots": list(shots), "hook": None, "total_duration": 5.0 * len(shots)}


def _issue(**kw) -> dict:
    base = {"code": "model_audit_TEST", "severity": "visual_risk", "shot_id": "1",
            "frame_id": "ff", "field_path": "ff_desc", "evidence": "", "message": "",
            "repair_targets": [{"shot_id": "1", "field_path": "ff_desc"}]}
    base.update(kw)
    return base


def test_reference_to_missing_field_is_insufficient_evidence():
    script = _script(_shot())
    issue = _issue(field_path="ff_desc.props", evidence="甲站立")
    rec = aj.adjudicate_issue(issue, script)
    assert rec["deterministic"]["field_exists"] is False
    assert rec["disposition"] == "insufficient_evidence"
    assert rec["needs_semantic_review"] is False
    assert any("引用不成立" in r for r in rec["reasons"])


def test_reference_to_missing_shot_is_insufficient_evidence():
    rec = aj.adjudicate_issue(_issue(shot_id="99"), _script(_shot()))
    assert rec["deterministic"]["shot_exists"] is False
    assert rec["disposition"] == "insufficient_evidence"


def test_out_of_range_repair_target_is_rejected():
    script = _script(_shot(keyframes=[{"desc": "d", "stage": "pre", "t_sec": 1.0}]))
    issue = _issue(field_path="keyframes[0].desc", frame_id="kf1", evidence="d",
                   repair_targets=[{"shot_id": "1", "field_path": "keyframes[3].desc"}])
    rec = aj.adjudicate_issue(issue, script)
    assert rec["deterministic"]["targets_rejected"]
    assert rec["disposition"] == "insufficient_evidence"
    assert any("修复目标越界" in r for r in rec["reasons"])


def test_frame_field_mismatch_is_insufficient_evidence():
    script = _script(_shot())
    rec = aj.adjudicate_issue(_issue(field_path="lf_desc", frame_id="ff", evidence="甲转身"),
                              script)
    assert rec["deterministic"]["frame_consistent"] is False
    assert rec["disposition"] == "insufficient_evidence"


def test_duplicate_evidence_confirms_defect():
    text = "甲推门（雨声很大）（雨声很大）走进屋内"
    script = _script(_shot(prompt=text))
    issue = _issue(code="model_audit_PROMPT_DUPLICATE_TEXT", severity="advisory",
                   field_path="prompt", frame_id="",
                   evidence="（雨声很大）（雨声很大）",
                   repair_targets=[{"shot_id": "1", "field_path": "prompt"}])
    rec = aj.adjudicate_issue(issue, script)
    assert rec["verdict"] == "confirmed"
    assert rec["disposition"] == "confirmed_defect"


def test_duplicate_claim_without_repetition_is_not_confirmed():
    script = _script(_shot(prompt="甲推门走进屋内"))
    issue = _issue(code="model_audit_PROMPT_DUPLICATE_TEXT", field_path="prompt",
                   frame_id="", evidence="走进屋内",
                   repair_targets=[{"shot_id": "1", "field_path": "prompt"}])
    rec = aj.adjudicate_issue(issue, script)
    # 逐字证据存在但只出现一次 ⇒ 不足以支持「重复」，但也不能反向证明没问题
    assert rec["verdict"] == "undecidable"
    assert rec["disposition"] == "insufficient_evidence"


def test_legal_stage_enum_alone_is_not_a_refutation():
    """枚举合法只证明「值合法」，不证明「阶段与动作内容匹配」（S03 收窄）."""
    script = _script(_shot(keyframes=[{"desc": "d", "stage": "approach", "t_sec": 1.0}]))
    base = dict(code="model_audit_KF_STAGE_MISMATCH", field_path="keyframes[0].stage",
                frame_id="kf1", evidence="approach",
                repair_targets=[{"shot_id": "1", "field_path": "keyframes[0].stage"}])

    # 只说「阶段与动作不符」→ 枚举回答不了，保留待裁决
    rec = aj.adjudicate_issue(_issue(message="approach 与动作内容不匹配", **base), script)
    assert rec["verdict"] == "undecidable"
    assert rec["disposition"] != "legal_expression"

    # 明确要求改成非枚举词 → 枚举才能准确反驳
    rec2 = aj.adjudicate_issue(
        _issue(message="建议把 stage 改成 process", **base), script)
    assert rec2["verdict"] == "refuted"
    assert rec2["disposition"] == "legal_expression"


def test_declared_transition_is_not_a_refutation():
    """字段非空只证明「已声明」，不证明「转场语义正确」（S03 收窄）."""
    script = _script(_shot(clip_transition="淡入淡出，0.5s"))
    issue = _issue(code="model_audit_TRANSITION_CONFLICT", field_path="clip_transition",
                   frame_id="", evidence="淡入淡出",
                   message="转场与场景转换语义冲突",
                   repair_targets=[{"shot_id": "1", "field_path": "clip_transition"}])
    rec = aj.adjudicate_issue(issue, script)
    assert rec["verdict"] == "undecidable"
    assert rec["disposition"] != "legal_expression"

    # 明确声称「缺少转场」而字段非空 → 这条主张被事实反驳
    rec2 = aj.adjudicate_issue(dict(issue, message="本镜缺少转场"), script)
    assert rec2["verdict"] == "refuted"
    assert rec2["disposition"] == "legal_expression"


def test_illegal_stage_is_confirmed():
    script = _script(_shot(keyframes=[{"desc": "d", "stage": "process", "t_sec": 1.0}]))
    issue = _issue(code="model_audit_KF_STAGE_MISMATCH", field_path="keyframes[0].stage",
                   frame_id="kf1", evidence="process",
                   repair_targets=[{"shot_id": "1", "field_path": "keyframes[0].stage"}])
    rec = aj.adjudicate_issue(issue, script)
    assert rec["verdict"] == "confirmed"
    assert rec["disposition"] == "confirmed_defect"


def test_severity_only_routes_undecidable_items():
    """语义类：blocking 进「待裁决」，advisory 进「内容评阅」；两者都不是「确认缺陷」."""
    script = _script(_shot(ff_desc="甲把壶放在案上"))
    base = dict(code="model_audit_PROP_CONTINUITY", field_path="ff_desc", frame_id="ff",
                evidence="甲把壶放在案上",
                repair_targets=[{"shot_id": "1", "field_path": "ff_desc"}])
    hard = aj.adjudicate_issue(_issue(severity="visual_risk", **base), script)
    soft = aj.adjudicate_issue(_issue(severity="advisory", **base), script)
    assert hard["verdict"] == soft["verdict"] == "undecidable"
    assert hard["disposition"] == "insufficient_evidence"
    assert hard["needs_semantic_review"] is True
    assert soft["disposition"] == "improvement_suggestion"
    assert soft["needs_semantic_review"] is False


def test_issue_is_preserved_verbatim():
    script = _script(_shot())
    issue = _issue(evidence="甲站立", message="m", extra_key="keep-me")
    rec = aj.adjudicate_issue(issue, script)
    assert rec["issue"]["extra_key"] == "keep-me"
    assert rec["issue"]["severity"] == "visual_risk"
    assert rec["severity_original"] == "visual_risk"


def test_report_is_shadow_only_and_counts_are_consistent():
    script = _script(_shot())
    audit = {
        "status": "ok", "input_revision": "rev", "rule_source_fingerprint": "fp",
        "audit_scope": "final", "audit_prompt_version": "v", "request_identity": "rid",
        "run_id": "r1",
        "issues": [
            _issue(evidence="甲站立"),
            _issue(code="model_audit_PROMPT_DUPLICATE_TEXT", severity="advisory",
                   field_path="ff_desc", evidence="甲站立"),
            _issue(shot_id="99"),
        ],
    }
    report = aj.build_shadow_report(audit, script)
    assert report["gate_effect"] == "shadow_only"
    assert report["summary"]["issues"] == 3
    assert sum(report["summary"]["by_disposition"].values()) == 3
    assert report["summary"]["broken_references"] == 1
    assert report["audit_identity"]["request_identity"] == "rid"
    # 原始 issue 不被改写
    assert audit["issues"][0]["severity"] == "visual_risk"
    assert "ADJUDICATION" in aj.render_report_text(report)


def test_presence_claim_needs_same_frame_obligation():
    """「列表里没有某人」只证明缺席，不证明本帧要求他出镜（S03 收窄）.

    缺席在画外合法时判 confirmed 会把合法表达算成缺陷；只有同帧描述点名该实体、
    布局却没有它，才是可确定的两侧矛盾。
    """
    layout = {"ff": [{"name": "甲", "on_screen": True, "props": []}], "lf": [], "kfs": []}
    missing = _issue(code="model_audit_CHARACTER_PRESENCE_MISSING", entity_id="乙",
                     field_path="frame_layouts.ff", frame_id="ff",
                     evidence="出镜列表无乙条目",
                     repair_targets=[{"shot_id": "1", "field_path": "frame_layouts.ff"}])

    # ① 乙明确在画外（同帧描述没有乙）→ 不得判确认缺陷
    rec = aj.adjudicate_issue(missing, _script(_shot(ff_desc="甲独自站立", frame_layouts=layout)))
    assert rec["verdict"] == "undecidable"
    assert rec["disposition"] != "confirmed_defect"
    assert "没有「必须出镜」的依据" in rec["reasons"][0]

    # ② 同帧描述点名乙 → 两侧矛盾成立
    rec2 = aj.adjudicate_issue(
        missing, _script(_shot(ff_desc="甲与乙并肩站立", frame_layouts=layout)))
    assert rec2["verdict"] == "confirmed"
    assert rec2["disposition"] == "confirmed_defect"

    # ③ 列表里已有该实体 → 主张被事实反驳
    rec3 = aj.adjudicate_issue(dict(missing, entity_id="甲"),
                               _script(_shot(ff_desc="甲站立", frame_layouts=layout)))
    assert rec3["verdict"] == "refuted"
    assert rec3["disposition"] == "legal_expression"


def test_props_claim_is_checked_against_desc_and_props():
    layout = {"ff": [{"name": "甲", "on_screen": True, "props": []}], "lf": [], "kfs": []}
    script = _script(_shot(ff_desc="甲左手握着钓杆", frame_layouts=layout))
    issue = _issue(code="model_audit_PROP_LAYOUT_MISSING", entity_id="甲",
                   field_path="frame_layouts.ff[0].props", frame_id="ff", evidence="钓杆",
                   repair_targets=[{"shot_id": "1", "field_path": "frame_layouts.ff[0].props"}])
    rec = aj.adjudicate_issue(issue, script)
    assert rec["verdict"] == "confirmed"
    assert "props 未列" in rec["reasons"][0]

    ok = _script(_shot(ff_desc="甲左手握着钓杆",
                       frame_layouts={"ff": [{"name": "甲", "on_screen": True,
                                              "props": ["钓杆"]}], "lf": [], "kfs": []}))
    rec2 = aj.adjudicate_issue(issue, ok)
    assert rec2["verdict"] == "refuted"
    assert rec2["disposition"] == "legal_expression"


def test_commit_path_writes_shadow_report(tmp_path, monkeypatch):
    """提交路径的接线：报告真的落盘，且写入失败不影响结论."""
    import json

    import step01_script_gen.main as m

    monkeypatch.setattr(m, "CHAPTER_DIR", tmp_path)
    script = _script(_shot())
    audit = {"status": "ok", "issues": [_issue(evidence="甲站立")],
             "input_revision": "rev", "run_id": "r1"}
    m._write_audit_adjudication(audit, script)
    path = tmp_path / "step01_audit_adjudication.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["gate_effect"] == "shadow_only"
    assert data["summary"]["issues"] == 1
    # 原始 issue 未被改写（影子不参与准入）
    assert audit["issues"][0]["severity"] == "visual_risk"

    # 关掉开关 → 不落盘（运维可用它临时停用）
    monkeypatch.setenv("STEP01_AUDIT_ADJUDICATION", "0")
    (tmp_path / "step01_audit_adjudication.json").unlink()
    m._write_audit_adjudication(audit, script)
    assert not path.exists()


def test_downgraded_real_problem_is_counted_as_hidden():    # 确定性证实（stage 非法）+ 已被现行机制降级 ⇒ 必须算成「被掩盖的确定缺陷」
    shot = _shot(keyframes=[{"desc": "d", "stage": "process", "t_sec": 1.0}])
    script = _script(shot)
    issue = _issue(code="model_audit_KF_STAGE_MISMATCH", severity="warning",
                   field_path="keyframes[0].stage", frame_id="kf1", evidence="process",
                   repair_targets=[{"shot_id": "1", "field_path": "keyframes[0].stage"}])
    issue["_downgraded_locked_only"] = True
    report = aj.build_shadow_report({"status": "ok", "issues": [issue]}, script)
    assert report["summary"]["confirmed_defect_hidden_by_gate"] == 1
    assert report["summary"]["semantic_pending_and_downgraded"] == 0
