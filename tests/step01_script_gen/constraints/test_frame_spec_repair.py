from __future__ import annotations


def _make_script(**extra):
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    shot = make_shot(index=1, main_subject="杨旭", **extra)
    script = make_script([shot])
    return script, make_ctx(script)


def test_fills_missing_pose_and_on_screen():
    from step01_script_gen.constraints.frame_spec_repair import (
        FrameSpecRepairConstraint,
    )

    script, ctx = _make_script(
        frame_layouts={
            "ff": [{"name": "杨旭"}],
            "lf": [{"name": "杨旭"}],
            "kfs": [],
        }
    )
    repairs = FrameSpecRepairConstraint().apply(ctx)
    layouts = script["shots"][0]["frame_layouts"]

    assert len(repairs) == 4
    assert layouts["ff"][0]["pose"] == "杨旭"
    assert layouts["ff"][0]["on_screen"] is True
    assert layouts["lf"][0]["pose"] == "杨旭"
    assert layouts["lf"][0]["on_screen"] is True


def test_aligns_kfs_length_to_keyframes():
    from step01_script_gen.constraints.frame_spec_repair import (
        FrameSpecRepairConstraint,
    )

    script, ctx = _make_script(
        keyframes=[{"t_sec": 1}, {"t_sec": 2}, {"t_sec": 3}],
        frame_layouts={
            "ff": [{"name": "杨旭", "pose": "站立", "on_screen": True}],
            "lf": [{"name": "杨旭", "pose": "站立", "on_screen": True}],
            "kfs": [[{"name": "杨旭", "pose": "站立", "on_screen": True}]],
        },
    )
    repairs = FrameSpecRepairConstraint().apply(ctx)

    assert len(repairs) == 2
    assert len(script["shots"][0]["frame_layouts"]["kfs"]) == 3


def test_trims_extra_kfs_to_keyframes():
    from step01_script_gen.constraints.frame_spec_repair import (
        FrameSpecRepairConstraint,
    )

    script, ctx = _make_script(
        keyframes=[{"t_sec": 1}],
        frame_layouts={
            "ff": [{"name": "杨旭", "pose": "站立", "on_screen": True}],
            "lf": [{"name": "杨旭", "pose": "站立", "on_screen": True}],
            "kfs": [
                [{"name": "杨旭", "pose": "站立", "on_screen": True}],
                [{"name": "杨旭", "pose": "转身", "on_screen": True}],
            ],
        },
    )
    repairs = FrameSpecRepairConstraint().apply(ctx)

    assert len(repairs) == 1
    assert len(script["shots"][0]["frame_layouts"]["kfs"]) == 1


def test_is_idempotent():
    from step01_script_gen.constraints.frame_spec_repair import (
        FrameSpecRepairConstraint,
    )

    script, ctx = _make_script(
        keyframes=[{"t_sec": 1}],
        frame_layouts={
            "ff": [{"name": "杨旭"}],
            "lf": [],
            "kfs": [],
        },
    )
    constraint = FrameSpecRepairConstraint()

    assert len(constraint.apply(ctx)) >= 1
    assert constraint.apply(ctx) == []
