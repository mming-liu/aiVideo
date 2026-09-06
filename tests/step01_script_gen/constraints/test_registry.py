from __future__ import annotations


def test_registry_names_are_unique_and_provider_neutral():
    from step01_script_gen.constraints import ALL_CONSTRAINTS

    names = [constraint.name for constraint in ALL_CONSTRAINTS]
    assert len(names) == len(set(names))
    assert names
    assert not any(name.startswith("doubao_") for name in names)


def test_registry_has_no_provider_specific_repair_marker():
    from step01_script_gen.constraints import ALL_CONSTRAINTS

    markers = [constraint.repair_marker for constraint in ALL_CONSTRAINTS if constraint.repair_marker]
    assert markers
    assert not any("_DOUBAO_FIX_" in marker for marker in markers)


def test_shared_constraints_are_registered():
    from step01_script_gen.constraints import ALL_CONSTRAINTS

    names = {constraint.name for constraint in ALL_CONSTRAINTS}
    expected = {
        "core_keyword_coverage",
        "role_anchor",
        "opening_empty_shot_insert",
        "climax_distribution",
        "overshoulder_consistency",
        "frame_spec_repair",
        "mandatory_biz_fields",
        "narration_subtitle_consistency",
    }
    assert expected <= names
