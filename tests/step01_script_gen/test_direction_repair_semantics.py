import copy

import pytest

from step01_script_gen.constraints.context import ConstraintContext
from step01_script_gen.constraints.v6_43_prompt_enforcement import V643PromptEnforcementConstraint


@pytest.mark.parametrize("text", [
    "甲仰头望向画右的乙，乙站在窗边。",
    "甲抬头看向画上方的灯，乙站在画右。",
    "甲回首看向画右的乙，乙眺望窗外。",
    "甲抬眼望向门口，乙凝神听着。",
])
def test_direction_repair_does_not_invent_eye_state_or_gaze_target(text):
    shot = {"index": 1, "prompt": text, "ff_desc": text,
            "char_layout": [{"name": "甲", "role": "主", "position": "画中"},
                            {"name": "乙", "role": "次", "position": "画右"}]}
    original = copy.deepcopy(shot)
    ctx = ConstraintContext.from_script({"shots": [shot]})
    V643PromptEnforcementConstraint().apply(ctx)
    assert shot == original
    V643PromptEnforcementConstraint().apply(ctx)
    assert shot == original


@pytest.mark.parametrize("names", [("丙", "丁"), ("第三人物", "第四人物")])
def test_ambiguous_direction_is_kept_without_fabricating_missing_relations(names):
    text = names[0] + "回首；" + names[1] + "抬头。"
    shot = {"index": 1, "prompt": text, "ff_desc": text, "char_layout": []}
    V643PromptEnforcementConstraint().apply(ConstraintContext.from_script({"shots": [shot]}))
    assert shot["prompt"] == text
    assert shot["ff_desc"] == text


@pytest.mark.parametrize("name", ["甲", "另一人物"])
@pytest.mark.parametrize("state", ["仰卧", "昏迷"])
def test_injury_context_does_not_authorize_closed_eyes_or_rigid_neck(name, state):
    text = name + state + "在榻上，心口伤痕可见。"
    shot = {"index": 7, "ff_desc": text, "lf_desc": text, "prompt": text, "motion_desc": text}
    before = copy.deepcopy(shot)
    ctx = ConstraintContext.from_script({"shots": [shot]})
    V643PromptEnforcementConstraint().apply(ctx)
    assert shot == before
