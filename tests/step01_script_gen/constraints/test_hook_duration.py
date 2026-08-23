"""R. HookDurationConstraint 单元测试."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_extends_duration_when_below_4s():
    """hook duration 3.0 → 4.0."""
    from tests.step01_script_gen.conftest import make_ctx, make_script

    script = make_script(
        shots=[],
        hook={"prompt": "[特写] 刺客出剑", "duration_sec": 3.0, "keyframes": []},
    )
    ctx = make_ctx(script)
    from step01_script_gen.constraints.hook_duration import HookDurationConstraint

    repairs = HookDurationConstraint().apply(ctx)

    assert len(repairs) >= 1
    assert script["hook"]["duration_sec"] == 4.0
    assert script["hook"]["duration"] == 4.0


def test_adds_after_keyframe_when_kfs_less_than_2():
    """kfs < 2 → 补 1 个 after 帧."""
    from tests.step01_script_gen.conftest import make_ctx, make_script

    script = make_script(
        shots=[],
        hook={
            "prompt": "[特写] 刺客出剑",
            "duration_sec": 3.0,
            "keyframes": [{"desc": "ff", "stage": "before", "t_sec": 0.5}],
        },
    )
    ctx = make_ctx(script)
    from step01_script_gen.constraints.hook_duration import HookDurationConstraint

    repairs = HookDurationConstraint().apply(ctx)

    assert len(repairs) >= 2  # 1 duration + 1 keyframe
    assert len(script["hook"]["keyframes"]) == 2
    assert script["hook"]["keyframes"][-1].get("stage") == "after"


def test_is_idempotent_when_already_4s():
    """duration_sec ≥ 4.0 → 跳过."""
    from tests.step01_script_gen.conftest import make_ctx, make_script

    script = make_script(
        shots=[],
        hook={
            "prompt": "[特写] 刺客出剑",
            "duration_sec": 5.0,
            "keyframes": [
                {"desc": "ff", "stage": "before", "t_sec": 0.5},
                {"desc": "lf", "stage": "after", "t_sec": 4.5},
            ],
        },
    )
    ctx = make_ctx(script)
    from step01_script_gen.constraints.hook_duration import HookDurationConstraint

    repairs = HookDurationConstraint().apply(ctx)
    assert repairs == []


def test_no_hook_no_repair():
    """无 hook → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, prompt="[中景]", duration_sec=3.0),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.hook_duration import HookDurationConstraint

    repairs = HookDurationConstraint().apply(ctx)
    assert repairs == []


def test_constraint_attributes():
    from step01_script_gen.constraints.hook_duration import HookDurationConstraint
    from step01_script_gen.constraints.types import Severity

    c = HookDurationConstraint()
    assert c.name == "hook_duration"
    assert c.severity == Severity.WARN
    assert c.fields == ()
