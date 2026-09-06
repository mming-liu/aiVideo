from __future__ import annotations


def _dialogue_script(camera: str, location: str = "小舟"):
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            main_subject="杨旭质问",
            narration_segment="你为何背叛",
            subtitle="你为何背叛",
            location=location,
            camera_movement=camera,
            prompt="近景，固定机位。",
        ),
        make_shot(
            index=2,
            main_subject="刺客回答",
            narration_segment="我是被迫",
            subtitle="我是被迫",
            location=location,
            camera_movement=camera,
            prompt="近景，固定机位。",
        ),
        make_shot(
            index=3,
            main_subject="杨旭追问",
            narration_segment="为何不说",
            subtitle="为何不说",
            location=location,
            camera_movement=camera,
            prompt="近景，固定机位。",
        ),
    ])
    return script, make_ctx(script)


def test_changes_middle_shot_to_overshoulder():
    from step01_script_gen.constraints.overshoulder_consistency import (
        OvershoulderConsistencyConstraint,
    )

    script, ctx = _dialogue_script("固定镜头")
    repairs = OvershoulderConsistencyConstraint().apply(ctx)
    middle = script["shots"][1]

    assert len(repairs) == 1
    assert repairs[0].shot_index == 2
    assert middle["camera_movement"] == "过肩镜头"
    assert middle["prompt"].endswith("，过肩镜头。")


def test_skips_when_locations_differ():
    from step01_script_gen.constraints.overshoulder_consistency import (
        OvershoulderConsistencyConstraint,
    )
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, main_subject="杨旭质问", narration_segment="a", location="小舟", camera_movement="固定"),
        make_shot(index=2, main_subject="刺客回答", narration_segment="b", location="岸边", camera_movement="固定"),
        make_shot(index=3, main_subject="杨旭追问", narration_segment="c", location="船舱", camera_movement="固定"),
    ])

    assert OvershoulderConsistencyConstraint().apply(make_ctx(script)) == []


def test_skips_when_cameras_differ():
    from step01_script_gen.constraints.overshoulder_consistency import (
        OvershoulderConsistencyConstraint,
    )
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, main_subject="杨旭质问", narration_segment="a", location="小舟", camera_movement="固定"),
        make_shot(index=2, main_subject="刺客回答", narration_segment="b", location="小舟", camera_movement="推镜"),
        make_shot(index=3, main_subject="杨旭追问", narration_segment="c", location="小舟", camera_movement="拉镜"),
    ])

    assert OvershoulderConsistencyConstraint().apply(make_ctx(script)) == []


def test_is_idempotent():
    from step01_script_gen.constraints.overshoulder_consistency import (
        OvershoulderConsistencyConstraint,
    )

    script, ctx = _dialogue_script("固定镜头")
    constraint = OvershoulderConsistencyConstraint()

    assert len(constraint.apply(ctx)) == 1
    assert constraint.apply(ctx) == []
