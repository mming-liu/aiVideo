import copy

import pytest

from step01_script_gen.validation import _EMOTION_ACTION_MAP, _fix_emotion_visualization
from step01_script_gen.constraints.context import ConstraintContext
from step01_script_gen.constraints.emotion_visualization_repair import EmotionVisualizationRepairConstraint, _strip_frame_state_wrapper


@pytest.mark.parametrize("emotion, action", _EMOTION_ACTION_MAP.items())
def test_explicit_emotion_values_are_stable(emotion, action):
    assert _fix_emotion_visualization(emotion) == emotion
    text = "甲，表情：" + emotion + "；乙，神情：" + emotion
    fixed = _fix_emotion_visualization(text)
    assert fixed == "甲，表情：" + action + "；乙，神情：" + action
    assert _fix_emotion_visualization(fixed) == fixed
    assert _fix_emotion_visualization(action) == action


@pytest.mark.parametrize("text", [
    "枯柳枝条毫无生气地耷拉，画面空无一人。",
    "花草充满生气，冰冷的石壁旁有温暖的灯光。",
    "慵懒的午后，紧张的氛围，温柔的风。",
    "色彩情绪：温暖；材质：冰冷。",
    "甲说他并不悲伤；乙并未生气。",
    "神情：不悲伤；表情：悲伤与喜悦交织。",
])
def test_free_prose_and_compound_or_negated_states_are_not_guessed(text):
    assert _fix_emotion_visualization(text) == text


def test_real_constraint_preserves_scene_and_is_idempotent_in_all_fields_and_hook():
    scene = "枯柳枝条毫无生气地耷拉，画面空无一人。"
    fields = ("prompt", "ff_desc", "lf_desc", "motion_desc")
    shot = {"index": 1, **dict.fromkeys(fields, scene), "keyframes": [{"desc": scene}]}
    actor = {"index": 2, **dict.fromkeys(fields, "表情：慵懒"), "keyframes": [{"desc": "神情：不满"}]}
    script = {"shots": [shot, actor], "hook": copy.deepcopy(actor)}
    ctx = ConstraintContext.from_script(script)
    constraint = EmotionVisualizationRepairConstraint()
    assert constraint.apply(ctx)
    assert shot == {"index": 1, **dict.fromkeys(fields, scene), "keyframes": [{"desc": _strip_frame_state_wrapper(scene)}]}
    fixed = copy.deepcopy(script)
    assert constraint.apply(ctx) == []
    assert script == fixed


def test_role_anchor_keeps_the_complete_description_in_the_real_constraint():
    from step01_script_gen.constraints.role_anchor import RoleAnchorConstraint
    features = "黑发束冠，身姿挺拔，穿着青衣小帽，双手各有五指，腰间佩着旧布袋，鞋面沾有泥土"
    for name in ("甲", "另一人物"):
        shot = {"index": 1, "main_subject": name, "prompt": "站在门边"}
        script = {"characters": [{"name": name, "appearance": features}], "shots": [shot]}
        ctx = ConstraintContext.from_script(script)
        constraint = RoleAnchorConstraint()
        assert constraint.apply(ctx)
        assert shot["prompt"] == "[" + name + ": " + features + "] 站在门边"
        assert constraint.apply(ctx) == []


def test_split_continuity_uses_the_same_scoped_emotion_repair():
    from step01_script_gen.constraints.split_continuity import SplitContinuityConstraint
    scene = "枯柳枝条毫无生气地耷拉"
    action = _EMOTION_ACTION_MAP["慵懒"]
    shot = {"index": 9.5, "duration_sec": 3.0,
            "ff_desc": "甲仰卧，画面聚焦甲（聆听反应）；旁边乙，表情：慵懒；" + scene,
            "char_layout": [{"name": "甲"}]}
    ctx = ConstraintContext.from_script({"shots": [shot]})
    SplitContinuityConstraint().apply(ctx)
    assert "聆听反应" not in shot["ff_desc"]
    assert "表情：" + action in shot["ff_desc"]
    assert scene in shot["ff_desc"]
    fixed = copy.deepcopy(shot)
    SplitContinuityConstraint().apply(ctx)
    assert shot == fixed
