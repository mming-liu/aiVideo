import copy

import pytest

from step01_script_gen.cross_field_checks import cross_field_issues


def presence(text, names):
    shot = {"index": 7, "duration_sec": 4, "lf_desc": text, "motion_desc": "保持姿态",
            "frame_layouts": {"lf": [{"name": n, "on_screen": True, "props": []} for n in names]}}
    script = {"shots": [shot]}
    before = copy.deepcopy(script)
    issues, failures = cross_field_issues(script)
    assert not failures
    assert script == before
    return {i.entity_id for i in issues if i.code == "frame_presence_consistency"}


@pytest.mark.parametrize("name", ["甲", "另一人物"])
def test_exit_predicate_does_not_bind_its_destination_as_the_exiting_person(name):
    assert presence("短刃已没入" + name + "心口", [name]) == set()
    assert presence("乙已离开" + name + "的身旁", [name, "乙"]) == {"乙"}


def test_only_the_explicit_exit_subject_is_marked():
    assert presence("甲目送乙已走远", ["甲", "乙"]) == {"乙"}
    assert presence("甲和乙已离场", ["甲", "乙"]) == {"甲", "乙"}
    assert presence("甲的衣角已消失在雾里", ["甲"]) == set()


def test_death_is_not_an_offscreen_statement():
    assert presence("甲已死，仍仰卧在画中", ["甲"]) == set()


@pytest.mark.parametrize("text", ["甲已离场", "甲已经退下", "甲消失在夜色中", "甲的身影已走远"])
def test_explicit_completed_person_exit_still_blocks_visible_layout(text):
    assert presence(text, ["甲"]) == {"甲"}
