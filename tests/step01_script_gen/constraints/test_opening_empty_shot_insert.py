from __future__ import annotations


def test_inserts_opening_empty_shot_and_compensates_duration():
    from step01_script_gen.constraints.opening_empty_shot_insert import (
        OpeningEmptyShotInsertConstraint,
    )
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, main_subject="杨旭站立", duration_sec=4.0, scene="小舟"),
        make_shot(index=2, main_subject="刺客潜行", duration_sec=4.0),
        make_shot(index=3, main_subject="听香质问", duration_sec=4.0),
    ])
    ctx = make_ctx(script)
    repairs = OpeningEmptyShotInsertConstraint().apply(ctx)
    inserted = script["shots"][0]

    assert len(repairs) == 1
    assert len(script["shots"]) == 4
    assert inserted["index"] == 1
    assert "空镜" in inserted["main_subject"]
    assert inserted["duration_sec"] == 2
    assert [shot["index"] for shot in script["shots"]] == [1, 2, 3, 4]
    assert script["shots"][-1]["duration_sec"] == 2.0
    assert sum(shot["duration_sec"] for shot in script["shots"]) == 12.0


def test_skips_when_window_contains_empty_hint():
    from step01_script_gen.constraints.opening_empty_shot_insert import (
        OpeningEmptyShotInsertConstraint,
    )
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, main_subject="杨旭站立"),
        make_shot(index=2, main_subject="水波特写"),
        make_shot(index=3, main_subject="刺客潜行"),
    ])

    assert OpeningEmptyShotInsertConstraint().apply(make_ctx(script)) == []
    assert len(script["shots"]) == 3


def test_skips_when_too_few_shots():
    from step01_script_gen.constraints.opening_empty_shot_insert import (
        OpeningEmptyShotInsertConstraint,
    )
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, main_subject="杨旭站立"),
        make_shot(index=2, main_subject="刺客潜行"),
    ])

    assert OpeningEmptyShotInsertConstraint().apply(make_ctx(script)) == []


def test_is_idempotent():
    from step01_script_gen.constraints.opening_empty_shot_insert import (
        OpeningEmptyShotInsertConstraint,
    )
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, main_subject="杨旭站立", duration_sec=4.0),
        make_shot(index=2, main_subject="刺客潜行", duration_sec=4.0),
        make_shot(index=3, main_subject="听香质问", duration_sec=4.0),
    ])
    ctx = make_ctx(script)
    constraint = OpeningEmptyShotInsertConstraint()

    assert len(constraint.apply(ctx)) == 1
    assert constraint.apply(ctx) == []
