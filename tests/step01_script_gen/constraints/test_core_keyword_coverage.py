from __future__ import annotations


def _make_script(**extra):
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    shot = make_shot(index=1, prompt="中景，杨旭站立", ff_desc="杨旭站在船头", **extra)
    script = make_script([shot])
    return script, make_ctx(script)


def test_appends_up_to_three_missing_keywords():
    from step01_script_gen.constraints.core_keyword_coverage import (
        CoreKeywordCoverageConstraint,
    )

    script, ctx = _make_script(
        lf_desc="杨旭转身",
        motion_desc="缓慢推近",
        core_keywords=["杨旭", "刺客", "血", "刀光", "杀意"],
    )
    repairs = CoreKeywordCoverageConstraint().apply(ctx)
    ff_desc = script["shots"][0]["ff_desc"]

    assert len(repairs) == 1
    assert ff_desc.endswith("刺客、血、刀光可见。")
    assert "杀意" not in ff_desc


def test_skips_when_keywords_are_covered():
    from step01_script_gen.constraints.core_keyword_coverage import (
        CoreKeywordCoverageConstraint,
    )

    script, ctx = _make_script(
        lf_desc="刺客持刀光逼近",
        motion_desc="刺客持刀光逼近",
        core_keywords=["杨旭", "刺客", "刀光"],
    )

    assert CoreKeywordCoverageConstraint().apply(ctx) == []
    assert script["shots"][0]["ff_desc"] == "杨旭站在船头"


def test_accepts_string_core_keywords():
    from step01_script_gen.constraints.core_keyword_coverage import (
        CoreKeywordCoverageConstraint,
    )

    script, ctx = _make_script(core_keywords="刺客,血")
    repairs = CoreKeywordCoverageConstraint().apply(ctx)

    assert len(repairs) == 1
    assert "刺客、血可见。" in script["shots"][0]["ff_desc"]


def test_is_idempotent():
    from step01_script_gen.constraints.core_keyword_coverage import (
        CoreKeywordCoverageConstraint,
    )

    script, ctx = _make_script(core_keywords=["刺客"])
    constraint = CoreKeywordCoverageConstraint()
    first = constraint.apply(ctx)
    second = constraint.apply(ctx)

    assert len(first) == 1
    assert second == []
    assert script["shots"][0]["ff_desc"].count("刺客可见。") == 1
