from __future__ import annotations


def test_injects_anchor_from_dict_bank():
    from step01_script_gen.constraints.role_anchor import RoleAnchorConstraint
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, main_subject="杨旭站立", prompt="中景，杨旭端坐船头"),
    ])
    ctx = make_ctx(
        script,
        characters_bank={"杨旭": {"static_features": "白衣书生，腰悬长剑"}},
    )
    repairs = RoleAnchorConstraint().apply(ctx)

    assert len(repairs) == 1
    assert script["shots"][0]["prompt"].startswith("[杨旭: 白衣书生，腰悬长剑]")


def test_injects_anchor_from_object_bank():
    from step01_script_gen.constraints.role_anchor import RoleAnchorConstraint
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    class Character:
        static_features = "黑衣短袍，左手按刀"

    script = make_script([
        make_shot(index=1, main_subject="张十三站立", prompt="近景"),
    ])
    ctx = make_ctx(script, characters_bank={"张十三": Character()})

    assert len(RoleAnchorConstraint().apply(ctx)) == 1
    assert script["shots"][0]["prompt"].startswith("[张十三: 黑衣短袍，左手按刀]")


def test_uses_script_characters_when_bank_is_empty():
    from step01_script_gen.constraints.role_anchor import RoleAnchorConstraint
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script(
        [make_shot(index=1, main_subject="听香站立", prompt="近景")],
        characters=[{"identifier": "听香", "static_features": "青袍长发"}],
    )
    ctx = make_ctx(script)

    assert len(RoleAnchorConstraint().apply(ctx)) == 1
    assert script["shots"][0]["prompt"].startswith("[听香: 青袍长发]")


def test_recovers_generic_subject_from_readable_fields_and_reports_both_repairs():
    from step01_script_gen.constraints.role_anchor import RoleAnchorConstraint
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(
            index=1,
            main_subject="群演",
            core_keywords=["杨旭", "船头", "垂钓"],
            narration_segment="杨旭道：今日江面平静",
            prompt="中景，船头架灯下",
        ),
    ])
    ctx = make_ctx(
        script,
        characters_bank={"杨旭": {"static_features": "素白轻袍，右手持钓杆"}},
    )
    repairs = RoleAnchorConstraint().apply(ctx)
    targets = {repair.target for repair in repairs}

    assert targets == {"prompt", "main_subject"}
    assert script["shots"][0]["prompt"].startswith("[杨旭: 素白轻袍，右手持钓杆]")
    assert script["shots"][0]["main_subject"] == "杨旭，群演"


def test_is_idempotent():
    from step01_script_gen.constraints.role_anchor import RoleAnchorConstraint
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, main_subject="群演", core_keywords=["张十三"], prompt="中景"),
    ])
    ctx = make_ctx(script, characters_bank={"张十三": {"static_features": "黑衣短袍"}})
    constraint = RoleAnchorConstraint()
    first = constraint.apply(ctx)
    second = constraint.apply(ctx)

    assert len(first) == 2
    assert second == []
    assert script["shots"][0]["prompt"].count("[张十三: 黑衣短袍]") == 1
