"""F6. PostCombatBlankConstraint 单元测试（v6.49, 08-28）."""
from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def test_combat_shot_next_has_blank_appended():
    """打斗镜后 1 镜无空镜 → 追加大远景月夜空镜叠化收尾."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, duration_sec=6.0, prompt="刺客破水而出，匕首刺入杨旭心口"),
        make_shot(index=2, duration_sec=6.0, prompt="杨旭仰卧船头"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.post_combat_blank import PostCombatBlankConstraint

    repairs = PostCombatBlankConstraint().apply(ctx)

    assert len(repairs) == 1
    assert "大远景月夜空镜叠化收尾" in script["shots"][1]["prompt"]


def test_next_shot_already_has_blank_skipped():
    """后 1 镜已有空镜 → 0 修复（幂等）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, duration_sec=6.0, prompt="刺客刺入，激烈打斗"),
        make_shot(index=2, duration_sec=6.0, prompt="大远景月夜空镜叠化收尾"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.post_combat_blank import PostCombatBlankConstraint

    repairs = PostCombatBlankConstraint().apply(ctx)

    assert len(repairs) == 0


def test_no_combat_shot_no_repair():
    """无打斗镜 → 0 修复."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, duration_sec=6.0, prompt="杨旭静坐船头"),
        make_shot(index=2, duration_sec=6.0, prompt="听香跪坐一旁"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.post_combat_blank import PostCombatBlankConstraint

    repairs = PostCombatBlankConstraint().apply(ctx)

    assert len(repairs) == 0


def test_idempotent():
    """已含本模板 → 0 修复（幂等）."""
    from tests.step01_script_gen.conftest import make_ctx, make_script, make_shot

    script = make_script([
        make_shot(index=1, duration_sec=6.0, prompt="刺客破水"),
        make_shot(index=2, duration_sec=6.0, prompt="杨旭仰卧。大远景月夜空镜叠化收尾"),
    ])
    ctx = make_ctx(script)
    from step01_script_gen.constraints.post_combat_blank import PostCombatBlankConstraint

    repairs = PostCombatBlankConstraint().apply(ctx)

    assert len(repairs) == 0


def test_constraint_attributes():
    from step01_script_gen.constraints.post_combat_blank import PostCombatBlankConstraint
    from step01_script_gen.constraints.types import Severity
    c = PostCombatBlankConstraint()
    assert c.name == "post_combat_blank"
    assert c.severity == Severity.WARN
    assert c.skip_hook is True
