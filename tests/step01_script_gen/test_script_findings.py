"""S07：脚本级确定性检查（重复文本 / 景别矛盾 / 道具与状态链）.

本文件只验证**代码能证明的部分**：
  · 有逐字依据、且没有反证时出 finding；
  · 有运镜、取回动作、显式时间跳跃、已声明的状态过渡时**不**出 finding；
  · 模型 issue 被同一 finding 覆盖时判 confirmed，被确定性反证时判 legal_expression；
  · 报告仍是 shadow_only，不改变 issue、severity 或 blocking。

样本全部使用匿名角色与通用道具，不绑定任何具体作品。
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from step01_script_gen import audit_adjudication as aj
from step01_script_gen.script_findings import scan_script

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "audit_narrative_families"


# ---------------------------------------------------------------------------
# 构造器
# ---------------------------------------------------------------------------

def _shot(index: int, **kw) -> dict:
    base = {
        "index": index, "duration_sec": 6.0,
        "scene": "舱内·夜", "location": "舱内",
        "clip_transition": "硬切", "context_bridge": "",
        "ff_desc": "", "lf_desc": "", "motion_desc": "",
        "prompt": "[中景] 舱内", "narration_segment": "",
        "frame_layouts": {"ff": [], "lf": [], "kfs": []},
        "keyframes": [],
    }
    base.update(kw)
    return base


def _script(*shots: dict, characters=None) -> dict:
    return {
        "shots": list(shots),
        "hook": None,
        "total_duration": 6.0 * len(shots),
        "characters": list(characters or []),
    }


def _layout(ff_props=None, lf_props=None, name: str = "甲") -> dict:
    def entries(props):
        if props is None:
            return []
        return [{"name": name, "on_screen": True, "position": "画中",
                 "pose": "", "gaze_target": "", "props": list(props)}]
    return {"ff": entries(ff_props), "lf": entries(lf_props), "kfs": []}


def _issue(**kw) -> dict:
    base = {
        "code": "model_audit_FIELD_CONFLICT", "severity": "visual_risk",
        "shot_id": "1", "frame_id": "", "entity_id": "",
        "field_path": "prompt", "evidence": "",
        "message": "", "minimal_fix": "",
        "repair_targets": [{"shot_id": "1", "field_path": "prompt"}],
    }
    base.update(kw)
    return base


def _codes(findings: list[dict]) -> list[str]:
    return [f["code"] for f in findings]


# ---------------------------------------------------------------------------
# 1. 重复文本
# ---------------------------------------------------------------------------

def test_duplicate_parenthetical_is_reported_once():
    shot = _shot(1, prompt="甲推门（雨声很大）（雨声很大）走进屋内")
    findings = scan_script(_script(shot))
    dups = [f for f in findings if f["code"] == "DUPLICATE_TEXT"]
    assert len(dups) == 1
    assert dups[0]["field_path"] == "prompt"
    assert dups[0]["evidence"] == "（雨声很大）"


def test_single_mention_is_not_a_duplicate():
    shot = _shot(1, prompt="甲推门（雨声很大）走进屋内")
    assert "DUPLICATE_TEXT" not in _codes(scan_script(_script(shot)))


def test_short_rhetorical_repeat_is_not_a_duplicate():
    """「看着，看着」是节奏，不是可确定性删除的重复文本."""
    shot = _shot(1, prompt="甲看着，看着，转身")
    assert "DUPLICATE_TEXT" not in _codes(scan_script(_script(shot)))


def test_duplicate_in_keyframe_desc_is_detected():
    shot = _shot(1, keyframes=[{"desc": "甲转身（雨声很大）（雨声很大）",
                                "stage": "pre", "t_sec": 1.0}])
    findings = [f for f in scan_script(_script(shot)) if f["code"] == "DUPLICATE_TEXT"]
    assert len(findings) == 1
    assert findings[0]["field_path"] == "keyframes[0].desc"
    assert findings[0]["frame_id"] == "kf1"


# ---------------------------------------------------------------------------
# 2. 景别矛盾
# ---------------------------------------------------------------------------

def test_prompt_framing_outlier_is_conflict():
    """#5 形态：prompt 写大特写，同镜帧描述都是大远景，运镜没点名大特写."""
    shot = _shot(1, prompt="[大特写] 黄昏荷塘空镜",
                 ff_desc="黄昏荷塘大远景，画面空无一人",
                 lf_desc="荷塘大远景延续，画面空无一人",
                 motion_desc="镜头缓慢向前推近")
    findings = [f for f in scan_script(_script(shot)) if f["code"] == "FRAMING_CONFLICT"]
    assert len(findings) == 1
    assert findings[0]["field_path"] == "prompt"
    assert findings[0]["evidence"] == "大特写"


def test_declared_framing_trajectory_is_legal():
    shot = _shot(1, prompt="[大远景] 院中",
                 ff_desc="大远景，甲立于院中",
                 lf_desc="特写，甲抬手",
                 motion_desc="镜头从大远景推至特写")
    assert "FRAMING_CONFLICT" not in _codes(scan_script(_script(shot)))


def test_ff_lf_framing_jump_without_motion_is_conflict():
    shot = _shot(1, prompt="[大远景] 院中",
                 ff_desc="大远景，甲立于院中",
                 lf_desc="大特写，甲的手",
                 motion_desc="")
    findings = [f for f in scan_script(_script(shot)) if f["code"] == "FRAMING_CONFLICT"]
    assert len(findings) == 1
    assert findings[0]["field_path"] == "lf_desc"


# ---------------------------------------------------------------------------
# 3. 道具状态链
# ---------------------------------------------------------------------------

def test_prop_release_then_hold_fixture():
    case = json.loads((FIXTURES / "state_broken.json").read_text(encoding="utf-8"))
    findings = [f for f in scan_script(case["script"]) if f["code"] == "PROP_STATE_CHAIN"]
    assert len(findings) == 1
    assert findings[0]["shot_id"] == "2"
    assert findings[0]["field_path"] == "ff_desc"
    assert findings[0]["prop"] == "酒壶"


def test_prop_legal_fixture_has_no_chain_finding():
    case = json.loads((FIXTURES / "state_legal.json").read_text(encoding="utf-8"))
    assert "PROP_STATE_CHAIN" not in _codes(scan_script(case["script"]))


def test_prop_reappearance_with_pickup_is_legal():
    shot1 = _shot(1, ff_desc="甲右手提着酒壶", lf_desc="甲把酒壶放在案上",
                  frame_layouts=_layout(["酒壶"], []))
    shot2 = _shot(2, ff_desc="甲捡起酒壶，右手提着酒壶", lf_desc="甲举杯",
                  frame_layouts=_layout(["酒壶"], []))
    assert "PROP_STATE_CHAIN" not in _codes(scan_script(_script(shot1, shot2)))


def test_prop_reappearance_across_time_jump_is_legal():
    shot1 = _shot(1, ff_desc="甲右手提着酒壶", lf_desc="甲把酒壶放在案上",
                  frame_layouts=_layout(["酒壶"], []))
    shot2 = _shot(2, scene="舱内·次日", location="舱内",
                  context_bridge="次日清晨", clip_transition="淡出黑场",
                  ff_desc="甲右手提着酒壶", lf_desc="甲饮酒",
                  frame_layouts=_layout(["酒壶"], []))
    assert "PROP_STATE_CHAIN" not in _codes(scan_script(_script(shot1, shot2)))


# ---------------------------------------------------------------------------
# 4. 成对状态链
# ---------------------------------------------------------------------------

def _hair_script(*shots, character="乙") -> dict:
    return _script(*shots, characters=[{
        "identifier": character,
        "static_features": "黑发挽髻，一袭春衫",
        "dynamic_features": "一袭春衫",
    }])


def test_state_anchor_flip_without_transition_is_conflict():
    """#8 形态：静态形象挽髻，首见已披头散发，此前没有任何过渡依据."""
    shot1 = _shot(1, ff_desc="乙立于船头", lf_desc="乙转身")
    shot2 = _shot(2, ff_desc="乙披头散发撑坐船板", lf_desc="乙低头")
    findings = [f for f in scan_script(_hair_script(shot1, shot2))
                if f["code"] == "STATE_CHAIN_CONFLICT"]
    assert len(findings) == 1
    assert findings[0]["shot_id"] == "2"
    assert findings[0]["field_path"] == "ff_desc"
    assert findings[0]["subject"] == "乙"


def test_declared_state_transition_is_legal():
    shot1 = _shot(1, ff_desc="乙挽髻站立", lf_desc="乙转身")
    shot2 = _shot(2, ff_desc="乙挽髻站立", lf_desc="乙披头散发",
                  motion_desc="乙的头发被扯散披落")
    assert "STATE_CHAIN_CONFLICT" not in _codes(scan_script(_hair_script(shot1, shot2)))


def test_same_field_state_contradiction_is_conflict():
    shot = _shot(1, prompt="[乙: 黑发挽髻，一袭春衫] 中景，乙披头散发")
    findings = [f for f in scan_script(_hair_script(shot))
                if f["code"] == "STATE_CONFLICT"]
    assert len(findings) == 1
    assert findings[0]["field_path"] == "prompt"
    assert findings[0]["subject"] == "乙"


def test_two_characters_states_in_same_field_are_not_a_conflict():
    """同一字段里甲束髻、乙散发，是两个角色的正常状态，不是自相矛盾."""
    shot = _shot(1, prompt="[甲: 黑发束髻] 中景，乙披头散发")
    script = _script(shot, characters=[
        {"identifier": "甲", "static_features": "黑发束髻"},
        {"identifier": "乙", "static_features": "黑发挽髻"},
    ])
    assert "STATE_CONFLICT" not in _codes(scan_script(script))


def test_first_frame_result_leak_is_conflict():
    shot = _shot(1, ff_desc="乙披头散发撑坐船板",
                 lf_desc="乙披头散发垂首", motion_desc="乙的头发被扯散披落")
    findings = [f for f in scan_script(_hair_script(shot))
                if f["code"] == "STATE_RESULT_LEAK"]
    assert len(findings) == 1
    assert findings[0]["field_path"] == "ff_desc"


def test_scan_does_not_mutate_script():
    shot1 = _shot(1, prompt="甲推门（雨声很大）（雨声很大）走进屋内")
    script = _script(shot1)
    before = copy.deepcopy(script)
    scan_script(script)
    assert script == before


# ---------------------------------------------------------------------------
# 5. 裁决接线
# ---------------------------------------------------------------------------

def test_adjudication_confirms_framing_finding():
    shot = _shot(1, prompt="[大特写] 黄昏荷塘空镜",
                 ff_desc="黄昏荷塘大远景，画面空无一人",
                 lf_desc="荷塘大远景延续，画面空无一人",
                 motion_desc="镜头缓慢向前推近")
    issue = _issue(code="model_audit_FIELD_CONFLICT", shot_id="1",
                   field_path="prompt", evidence="大特写",
                   message="景别矛盾")
    rec = aj.adjudicate_issue(issue, _script(shot))
    assert rec["verdict"] == "confirmed"
    assert rec["disposition"] == "confirmed_defect"
    assert rec["matched_findings"] == ["FRAMING_CONFLICT"]


def test_adjudication_confirms_prop_chain_finding():
    shot1 = _shot(1, ff_desc="甲右手提着酒壶", lf_desc="甲把酒壶放在案上",
                  frame_layouts=_layout(["酒壶"], []))
    shot2 = _shot(2, ff_desc="甲右手提着酒壶", lf_desc="甲举杯",
                  frame_layouts=_layout(["酒壶"], []))
    issue = _issue(code="model_audit_PROP_CONTINUITY", shot_id="2",
                   frame_id="ff", field_path="ff_desc",
                   evidence="右手提着酒壶",
                   repair_targets=[{"shot_id": "2", "field_path": "ff_desc"}])
    rec = aj.adjudicate_issue(issue, _script(shot1, shot2))
    assert rec["verdict"] == "confirmed"
    assert rec["matched_findings"] == ["PROP_STATE_CHAIN"]


def test_adjudication_confirms_state_chain_finding():
    shot1 = _shot(1, ff_desc="乙立于船头", lf_desc="乙转身")
    shot2 = _shot(2, ff_desc="乙披头散发撑坐船板", lf_desc="乙低头")
    issue = _issue(code="model_audit_STATE_CHAIN_CONFLICT", shot_id="2",
                   frame_id="ff", field_path="ff_desc",
                   evidence="披头散发",
                   repair_targets=[{"shot_id": "2", "field_path": "ff_desc"}])
    rec = aj.adjudicate_issue(issue, _hair_script(shot1, shot2))
    assert rec["verdict"] == "confirmed"
    assert rec["matched_findings"] == ["STATE_CHAIN_CONFLICT"]


def test_adjudication_refutes_framing_claim_with_declared_trajectory():
    shot = _shot(1, prompt="[大远景] 院中",
                 ff_desc="大远景，甲立于院中",
                 lf_desc="特写，甲抬手",
                 motion_desc="镜头从大远景推至特写")
    issue = _issue(code="model_audit_FIELD_CONFLICT", shot_id="1",
                   frame_id="lf", field_path="lf_desc", evidence="特写",
                   message="景别与首帧不一致",
                   repair_targets=[{"shot_id": "1", "field_path": "lf_desc"}])
    rec = aj.adjudicate_issue(issue, _script(shot))
    assert rec["verdict"] == "refuted"
    assert rec["disposition"] == "legal_expression"
    assert "运镜" in rec["reasons"][0]


def test_adjudication_refutes_prop_claim_with_pickup_action():
    shot1 = _shot(1, ff_desc="甲右手提着酒壶", lf_desc="甲把酒壶放在案上",
                  frame_layouts=_layout(["酒壶"], []))
    shot2 = _shot(2, ff_desc="甲捡起酒壶，右手提着酒壶", lf_desc="甲举杯",
                  frame_layouts=_layout(["酒壶"], []))
    issue = _issue(code="model_audit_PROP_CONTINUITY", shot_id="2",
                   frame_id="ff", field_path="ff_desc",
                   evidence="右手提着酒壶",
                   repair_targets=[{"shot_id": "2", "field_path": "ff_desc"}])
    rec = aj.adjudicate_issue(issue, _script(shot1, shot2))
    assert rec["verdict"] == "refuted"
    assert rec["disposition"] == "legal_expression"
    assert "取回" in rec["reasons"][0]


def test_shadow_report_exposes_findings_without_gate_effect():
    shot = _shot(1, prompt="[大特写] 黄昏荷塘空镜",
                 ff_desc="黄昏荷塘大远景，画面空无一人",
                 lf_desc="荷塘大远景延续，画面空无一人",
                 motion_desc="镜头缓慢向前推近")
    issue = _issue(code="model_audit_FIELD_CONFLICT", shot_id="1",
                   field_path="prompt", evidence="大特写",
                   message="景别矛盾")
    report = aj.build_shadow_report({"status": "ok", "issues": [issue]}, _script(shot))
    assert report["gate_effect"] == "shadow_only"
    assert report["deterministic_findings"]["count"] >= 1
    assert report["summary"]["deterministic_findings"] == \
        report["deterministic_findings"]["count"]
    assert report["summary"]["issues_matched_to_findings"] == 1
    # 原始 issue 未被改写
    assert issue["severity"] == "visual_risk"
    assert report["records"][0]["issue"]["severity"] == "visual_risk"
