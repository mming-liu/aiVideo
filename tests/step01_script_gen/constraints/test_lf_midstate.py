"""Q. LfMidstateConstraint 单元测试."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_replaces_initial_appears():
    """'初现' → '已现'."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, lf_desc="杨旭心口暗红洇痕初现"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.lf_midstate import LfMidstateConstraint

    repairs = LfMidstateConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert "初现" not in script["shots"][0]["lf_desc"]
    assert "已现" in script["shots"][0]["lf_desc"]


def test_is_idempotent():
    """已替换完毕（无 midstate 词） → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, lf_desc="杨旭心口暗红洇痕已现"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.lf_midstate import LfMidstateConstraint

    c = LfMidstateConstraint()
    c.apply(ctx)
    repairs2 = c.apply(ctx)
    assert repairs2 == []


def test_replaces_multiple_terms():
    """'即将/正在/渐渐' 都替换."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, lf_desc="刺客即将出剑，正在逼近，渐渐逼近"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.lf_midstate import LfMidstateConstraint

    repairs = LfMidstateConstraint().apply(ctx)

    assert len(repairs) >= 1
    lf = script["shots"][0]["lf_desc"]
    assert "即将" not in lf
    assert "正在" not in lf
    assert "渐渐" not in lf


def test_skips_when_no_midstate_term():
    """无 midstate 词 → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, lf_desc="杨旭心口暗红洇痕"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.lf_midstate import LfMidstateConstraint

    repairs = LfMidstateConstraint().apply(ctx)
    assert repairs == []


def test_constraint_attributes():
    from step01_script_gen.constraints.lf_midstate import LfMidstateConstraint
    from step01_script_gen.constraints.types import Severity

    c = LfMidstateConstraint()
    assert c.name == "lf_midstate"
    assert c.severity == Severity.WARN
    assert c.repair_marker == "已现"
    assert "lf_desc" in c.fields
