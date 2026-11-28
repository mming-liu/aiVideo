"""Lexical matches cannot establish state transitions or absent performance."""
import copy

import pytest


@pytest.mark.parametrize("modifier", ["险些", "差点", "差一点", "几乎", "正要", "试图"])
def test_unrealized_release_does_not_remove_possession(modifier):
    from step01_script_gen.weapon_states import asserted_marker, RELEASE_MARKERS
    assert not asserted_marker("灯盏" + modifier + "脱手", RELEASE_MARKERS)
    assert asserted_marker("灯盏已经脱手", RELEASE_MARKERS)


@pytest.mark.parametrize("gaze", ["无", "旅人", "画外"])
def test_explicit_gaze_is_not_rejected_by_legacy_pose(gaze):
    from step01_script_gen.validation import _has_layout_direction_fact
    shot = {"frame_layouts": {"ff": [{"name": "女子", "gaze_target": gaze}]}}
    assert _has_layout_direction_fact(shot, "女子")
    for missing in [None, "", " ", 7]:
        shot["frame_layouts"]["ff"][0]["gaze_target"] = missing
        assert not _has_layout_direction_fact(shot, "女子")


@pytest.mark.parametrize("prompt", [
    "女子青丝挽慵懒美人髻，侧身撑伞",
    "青年双目圆睁，惊恐与难以置信凝在眼底",
    "青年十分惊恐",
])
def test_lexical_miss_remains_visible_for_semantic_review(prompt):
    from step01_script_gen.validation import _check_emotion_visualization
    from step01_script_gen.quality_issues import collect_structured_issues
    from step01_script_gen.main import _final_audit_focus
    shot = {"index": 23.5, "prompt": prompt}
    result = _check_emotion_visualization([shot])
    assert result["issues"] == []
    assert result["semantic_review_required"]
    issues, failed, _ = collect_structured_issues(
        {"shots": []}, {"emotion_visualization"}, set(),
        checklist_runner=lambda _: {"results": {"emotion_visualization": result}})
    candidates = [i for i in issues if i.code == "emotion_visualization"]
    assert not failed and candidates
    assert all(i.severity == "advisory" for i in candidates)
    focus = _final_audit_focus({}, {"advisory": [i.to_dict() for i in candidates]})
    assert any("23.5" in f and "表演语义复核" in f for f in focus)


def test_short_result_does_not_inherit_pre_action_pose():
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot
    from step01_script_gen.constraints import run_hard_fix
    shot = make_shot(index=23.5, lf_desc="旅人垂手，女子转身。",
                     char_layout=[{"name": "旅人", "position": "画左", "facing": "拱手"}])
    script = make_script([shot])
    from step01_script_gen.constraints.lf_desc_length import LfDescLengthConstraint
    before = copy.deepcopy(script)
    context = make_ctx(script)
    assert LfDescLengthConstraint().apply(context) == []
    assert script == before
    violations = context.drain_violations()
    assert violations and not violations[-1].repaired


def test_blood_on_bystander_does_not_create_a_fatal_wound():
    from tests.step01_script_gen.conftest import make_script, make_shot
    from step01_script_gen.constraints import run_hard_fix
    shot = make_shot(index=23.5, main_subject="旅人",
                    ff_desc="蓝衣旅人看着指尖的血迹。",
                    lf_desc="蓝衣旅人仰头呼喊。",
                    keyframes=[{"t_sec": 1, "desc": "蓝衣旅人指尖暗红血迹"},
                               {"t_sec": 3, "desc": "蓝衣旅人仰头呼喊"}])
    script, report = run_hard_fix(make_script([shot]), return_report=True)
    final = script["shots"][0]
    assert "白袍" not in final["lf_desc"]
    assert "心口" not in final["keyframes"][1]["desc"]
    assert any(v.constraint_name == "kf_blood_monotonic" and not v.repaired
               for v in report.violations_remaining)
