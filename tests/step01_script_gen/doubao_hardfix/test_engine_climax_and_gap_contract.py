"""Engine-level regression for the Doubao climax/GAP contract."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_gap_reads_comma_separated_core_keywords():
    from step01_script_gen.gap_analysis import calculate_keyword_coverage

    shot = {
        "core_keywords": "刺客,刀光",
        "prompt": "刺客持刀而出，刀光掠过水面",
        "ff_desc": "",
        "lf_desc": "",
        "motion_desc": "",
        "subtitle": "",
    }
    result = calculate_keyword_coverage(shot, "")
    assert result["total"] == 2
    assert result["covered"] == 2
    assert result["missing"] == []


def test_gap_counts_climax_from_string_core_keywords():
    from tests.step01_script_gen.conftest import make_script, make_shot
    from step01_script_gen.gap_analysis import analyze_shot_distribution

    script = make_script([
        make_shot(index=1, core_keywords="水湾"),
        make_shot(index=2, core_keywords="冲突"),
        make_shot(index=3, core_keywords="反转"),
        make_shot(index=4, core_keywords="真相"),
    ])
    report = analyze_shot_distribution(script, "")
    assert report["climax_shots"] == 3


def test_run_hard_fix_injects_words_that_gap_can_read(monkeypatch):
    """Reproduce the real Doubao bug: core_keywords is a comma string."""
    from tests.step01_script_gen.conftest import make_script, make_shot
    from step01_script_gen.constraints import run_hard_fix
    from step01_script_gen.constraints import engine as eng
    from step01_script_gen.doubao_hardfix import register_doubao_hardfixes
    from step01_script_gen.gap_analysis import validate_script_gap
    import step01_script_gen.doubao_hardfix as dh

    monkeypatch.setenv("REGEN_AUTO_TRIGGER", "0")
    saved_constraints = list(eng.ALL_CONSTRAINTS)
    saved_registered = dh._DOUBAO_HARDFIX_REGISTERED
    for c in [x for x in eng.ALL_CONSTRAINTS if x.name.startswith("doubao_")]:
        eng.ALL_CONSTRAINTS.remove(c)
    dh._DOUBAO_HARDFIX_REGISTERED = False
    try:
        register_doubao_hardfixes()
        script = make_script([
            make_shot(index=i, main_subject=f"叙事镜头{i}", core_keywords="水湾,小舟")
            for i in range(1, 7)
        ])
        script, report = run_hard_fix(
            script,
            is_in_repair_loop=False,
            is_final_pass=True,
            return_report=True,
        )
        assert report.repairs_by_constraint.get("doubao_climax_back_inject") == 3
        gap = validate_script_gap(script, "")
        assert gap["distribution"]["climax_shots"] >= 3
    finally:
        eng.ALL_CONSTRAINTS[:] = saved_constraints
        dh._DOUBAO_HARDFIX_REGISTERED = saved_registered
